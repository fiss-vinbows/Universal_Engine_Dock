"""テトリスの支援モードの開始・終了(session.py)と、ゲームモードの登録(__init__.py)のテスト。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests.tetris.config_isolation  # noqa: E402,F401  本番の設定を使わない
from PyQt6 import QtCore, QtWidgets  # noqa: E402

from uedock.capture.game_window import GameWindow  # noqa: E402
from uedock.game_mode import LAYOUT_SOLO, LAYOUT_VERSUS, AssistRequest, EngineSpec, SideSetup  # noqa: E402
from uedock.games.tetris import MODE, assist_view, session  # noqa: E402
from uedock.games.tetris.assist_view import AssistSettingsPanel, load_assist_view, save_assist_view  # noqa: E402
from uedock.games.tetris.capture.calibrate import CalibrationResult  # noqa: E402
from uedock.plugin_api import FEATURE_DEBUG_LOG, Plugin  # noqa: E402
from uedock.plugin_loader import PluginManager  # noqa: E402
from tests import config_isolation as isolation  # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
KASUGAI = EngineSpec("KasugAI", Path("C:/engines/tetrisai-bot.exe"), weights=Path("C:/engines/run16.toml"))
CC2 = EngineSpec("Cold Clear 2", Path("C:/engines/cold-clear-2.exe"), args=("--threads", "4"))
WINDOW = GameWindow(77, "PuyoPuyoTetris2", 0, 0, 2560, 1440)


class FakeClient:
    """ColdClearClient の代わり。起動の指定を覚える。"""

    started: list["FakeClient"] = []

    def __init__(self, engine=None, opening=None, extra_args=None) -> None:  # noqa: ANN001
        self.engine, self.extra_args = engine, extra_args
        self.closed = 0
        FakeClient.started.append(self)

    def close(self) -> None:
        self.closed += 1


class FakeWorker(QtCore.QObject):
    """AssistWorker の代わり。観測のスレッドは動かさず、渡された設定を覚える。"""

    draw_data_ready = QtCore.pyqtSignal(object)
    plan_text_ready = QtCore.pyqtSignal(object)
    next_debug_ready = QtCore.pyqtSignal(object)
    fatal_error = QtCore.pyqtSignal(str)
    fps_ready = QtCore.pyqtSignal(float)
    made: list["FakeWorker"] = []

    def __init__(self, calibration, cold_clear, debug_log_path, **kw) -> None:  # noqa: ANN001, ANN003
        super().__init__()
        self.calibration, self.cold_clear, self.debug_log_path, self.kw = calibration, cold_clear, debug_log_path, kw
        self.started = self.stopped = 0
        FakeWorker.made.append(self)

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def wait(self, _ms: int = 0) -> bool:
        return True


def request(sides: list[SideSetup], layout: str = LAYOUT_VERSUS, window: GameWindow | None = WINDOW,
            plugins: PluginManager | None = None, debug_log: bool = False, primary: bool = True) -> AssistRequest:
    return AssistRequest(layout=layout, sides=tuple(sides), game_window=window, plugins=plugins or PluginManager(),
                         debug_log=debug_log, primary=primary)


def manual_calibration() -> CalibrationResult:
    return CalibrationResult(
        board_origin_x=300, board_origin_y=100, board_width=360, board_height=720, cell_size=36, board_cols=10, board_rows=20,
        hold_rect=(190, 140, 93, 58), next_rects=[(700, 100 + 76 * i, 84, 57) for i in range(5)],
    )


class Base(unittest.TestCase):
    def setUp(self) -> None:
        isolation.reset()
        FakeClient.started = []
        FakeWorker.made = []
        self.config_path = isolation.ROOT / "config" / "tetris_calibration.json"
        for name, value in (
            ("CONFIG_PATH", self.config_path),
            ("ColdClearClient", FakeClient),
            ("AssistWorker", FakeWorker),
            ("_exclude_window_from_screen_capture", lambda _w: True),
            ("title_bar_rect", None),
        ):
            if name == "title_bar_rect":
                continue
            patcher = mock.patch.object(session, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # 終了ボタンの置き場所(ゲームのタイトルバー)は Win32 に聞かない
        patcher = mock.patch("uedock.games.tetris.worker.title_bar_rect", return_value=(0, 0, 2560, 31))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.messages: list[str] = []
        for kind in ("warning", "information", "critical"):
            patcher = mock.patch.object(QtWidgets.QMessageBox, kind, side_effect=lambda _p, _t, text: self.messages.append(text))
            patcher.start()
            self.addCleanup(patcher.stop)

    def put_calibration(self) -> None:
        import json

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(manual_calibration().to_dict()), encoding="utf-8")

    def begin(self, req: AssistRequest) -> session.TetrisAssistSession:
        s = session.start_assist(req)
        s.begin()
        self.addCleanup(s.stop)
        return s


class NameplateTest(Base):
    """プレイヤー名の帯に、使っているエンジンの名前を重ねる。"""

    def test_versus_shows_engine_names(self) -> None:
        s = self.begin(request([SideSetup("1p", KASUGAI), SideSetup("2p", CC2)]))
        self.assertEqual([(p.side, p.text) for p in s.nameplates], [("1p", "KasugAI"), ("2p", CC2.name)])
        s.stop()
        self.assertEqual(s.nameplates, [])

    def test_solo_has_no_nameplate(self) -> None:
        s = self.begin(request([SideSetup("solo", KASUGAI)], layout=LAYOUT_SOLO))
        self.assertEqual(s.nameplates, [])

    def test_not_shown_when_it_cannot_be_hidden_from_capture(self) -> None:
        with mock.patch.object(session, "_exclude_window_from_screen_capture", return_value=False):
            s = self.begin(request([SideSetup("1p", KASUGAI)]))
        self.assertEqual(s.nameplates, [])


class PositionTest(Base):
    def test_window_gives_each_side_its_board(self) -> None:
        s = session.start_assist(request([SideSetup("1p", KASUGAI), SideSetup("2p", CC2)]))
        self.addCleanup(s.stop)
        one, two = s.calibrations["1p"], s.calibrations["2p"]
        self.assertEqual((one.board_origin_x, one.board_origin_y), (409, 211))  # 2560×1440 では基準の位置そのまま
        self.assertEqual(two.board_origin_x - one.board_origin_x, 1259, "2P は 1P を右へずらした位置")

    def test_solo_uses_the_left_board(self) -> None:
        s = session.start_assist(request([SideSetup("solo", KASUGAI)], layout=LAYOUT_SOLO))
        self.addCleanup(s.stop)
        self.assertEqual(s.calibrations["solo"].board_origin_x, 409)

    def test_without_window_the_manual_calibration_is_used(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            session.start_assist(request([SideSetup("1p", KASUGAI)], window=None))
        self.assertIn("盤面の位置が分かりません", str(ctx.exception))
        self.assertEqual(FakeClient.started, [], "始められないのに思考エンジンを起動した")
        self.put_calibration()
        s = session.start_assist(request([SideSetup("1p", KASUGAI)], window=None))
        self.addCleanup(s.stop)
        self.assertEqual(s.calibrations["1p"].board_origin_x, 300)

    def test_2p_needs_the_game_window(self) -> None:
        self.put_calibration()  # 手動キャリブレーションは 1P・一人用の位置だけ
        with self.assertRaises(RuntimeError) as ctx:
            session.start_assist(request([SideSetup("2p", KASUGAI)], window=None))
        self.assertIn("2P は、ゲームのウィンドウ", str(ctx.exception))

    def test_broken_calibration_is_reported(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text('{"board_origin_x": 1}', encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            session.start_assist(request([SideSetup("1p", KASUGAI)], window=None))
        self.assertIn("キャリブレーションが不正です", str(ctx.exception))


class EngineTest(Base):
    def test_engine_weights_and_registered_args(self) -> None:
        s = session.start_assist(request([SideSetup("1p", KASUGAI), SideSetup("2p", CC2)]))
        self.addCleanup(s.stop)
        one, two = FakeClient.started
        self.assertEqual((one.engine, one.extra_args), ((KASUGAI.exe, KASUGAI.weights), None))
        self.assertEqual((two.engine, two.extra_args), ((CC2.exe, None), ["--threads", "4"]))

    def test_failure_closes_the_ones_already_started(self) -> None:
        def flaky(engine=None, opening=None, extra_args=None):  # noqa: ANN001, ANN202
            if FakeClient.started:
                raise TimeoutError("応答がありません")
            return FakeClient(engine, opening, extra_args)

        with mock.patch.object(session, "ColdClearClient", flaky), self.assertRaises(RuntimeError) as ctx:
            session.start_assist(request([SideSetup("1p", KASUGAI), SideSetup("2p", CC2)]))
        self.assertIn("応答がありません", str(ctx.exception))
        self.assertEqual([c.closed for c in FakeClient.started], [1])


class RunTest(Base):
    def test_both_sides_run_with_their_own_settings(self) -> None:
        # 支援の設定(テンプレ等)はサイドごとのファイル。2P だけテンプレを切る
        save_assist_view(dict(load_assist_view(session.side_view_path("2p")), templates=False), session.side_view_path("2p"))
        s = self.begin(request([SideSetup("1p", KASUGAI), SideSetup("2p", CC2)]))
        one, two = FakeWorker.made
        self.assertEqual((one.kw["side"], two.kw["side"]), ("1p", "2p"))
        self.assertEqual((one.kw["opener_enabled"], two.kw["opener_enabled"]), (True, False))
        # 1P と 2P を同時に動かす時は、撮影と認識を側ごとの子プロセスで行う
        self.assertEqual((one.kw["separate_recognition"], two.kw["separate_recognition"]), (True, True))
        self.assertEqual((one.started, two.started), (1, 1))
        self.assertEqual((one.debug_log_path, one.kw["record_video"]), (None, False))
        self.assertTrue(s.is_running())
        # 終了ボタンは1つ(1P の側)。情報パネルは側ごとにあり、エンジンの名前が出る
        self.assertEqual([b.layout().count() > 0 for b in s.badges.values()], [True, False])
        self.assertEqual(s.badges["1p"]._side, "1p_versus")
        s.stop()
        self.assertFalse(s.is_running())
        self.assertEqual((one.stopped, two.stopped), (1, 1))
        self.assertEqual([c.closed for c in FakeClient.started], [1, 1])
        self.assertEqual((s.badges, s.overlays), ({}, {}))
        s.stop()  # 終了ボタンで止めた後、メイン画面がもう一度呼ぶ
        self.assertEqual([c.closed for c in FakeClient.started], [1, 1])

    def test_single_side_recognizes_in_process_except_2p(self) -> None:
        self.begin(request([SideSetup("1p", KASUGAI)]))
        self.assertFalse(FakeWorker.made[-1].kw["separate_recognition"])
        self.begin(request([SideSetup("2p", KASUGAI)]))
        self.assertTrue(FakeWorker.made[-1].kw["separate_recognition"])
        self.begin(request([SideSetup("solo", KASUGAI)], layout=LAYOUT_SOLO))
        self.assertEqual((FakeWorker.made[-1].kw["side"], FakeWorker.made[-1].kw["separate_recognition"]), ("1p", False))

    def test_no_stop_button_when_another_mode_shows_one(self) -> None:
        # 1P がぷよ・2P がテトリスの時、タイトルバーの終了ボタンはぷよの側が出す(重ならないように)
        s = self.begin(request([SideSetup("2p", KASUGAI)], primary=False))
        self.assertEqual(s.badges["2p"].layout().count(), 0)
        t = self.begin(request([SideSetup("2p", KASUGAI)], primary=True))
        self.assertGreater(t.badges["2p"].layout().count(), 0)

    def test_restarted_engine_is_the_one_closed(self) -> None:
        s = self.begin(request([SideSetup("1p", KASUGAI)]))
        replaced = FakeClient()  # ワーカーが、応答しなくなった思考エンジンを作り直した
        FakeWorker.made[0].cold_clear = replaced
        s.stop()
        self.assertEqual(replaced.closed, 1, "作り直した方の思考エンジンが残る")

    def test_long_silence_stops_and_suggestions_keep_it_alive(self) -> None:
        s = self.begin(request([SideSetup("1p", KASUGAI)]))
        self.assertTrue(s._safety.isActive())
        self.assertEqual(s._safety.interval(), session.SAFETY_TIMEOUT_MS)
        with mock.patch.object(s._safety, "start") as restart:
            s._keep_alive(None)  # 提示が消えただけでは数え直さない
            restart.assert_not_called()
            s._keep_alive(object())
            restart.assert_called_once_with(session.SAFETY_TIMEOUT_MS)
        s._safety.timeout.emit()
        self.assertFalse(s.is_running())

    def test_moving_the_window_stops(self) -> None:
        rect = [(0, 0, 2560, 1440)]
        with mock.patch.object(session, "client_rect", side_effect=lambda _h: rect[0]):
            s = self.begin(request([SideSetup("1p", KASUGAI)]))
            s._check_game_window()
            self.assertTrue(s.is_running())
            rect[0] = (10, 0, 2560, 1440)
            s._check_game_window()
        self.assertFalse(s.is_running())
        self.assertIn("ゲームのウィンドウが動いた", self.messages[-1])

    def test_fatal_error_stops_and_is_reported_once(self) -> None:
        s = self.begin(request([SideSetup("1p", KASUGAI)]))
        s._on_fatal_error("プロセスが終了しました")
        self.assertFalse(s.is_running())
        self.assertIn("プロセスが終了しました", self.messages[-1])
        s._on_fatal_error("後から届いたもう片方のエラー")
        self.assertEqual(len(self.messages), 1)

    def test_debug_log_and_video_need_the_plugin_and_the_switch(self) -> None:
        plugins = PluginManager([Plugin(id="debug_log", name="ログ出力", features=frozenset({FEATURE_DEBUG_LOG}))])
        with mock.patch("uedock.games.tetris.worker.DEBUG_LOG_DIR", isolation.ROOT / "debug_logs"):
            (isolation.ROOT / "debug_logs").mkdir(parents=True, exist_ok=True)
            for wanted, manager, expect in ((True, PluginManager(), False), (False, plugins, False), (True, plugins, True)):
                self.begin(request([SideSetup("2p", KASUGAI)], plugins=manager, debug_log=wanted))
                worker = FakeWorker.made[-1]
                self.assertEqual((worker.debug_log_path is not None, worker.kw["record_video"]), (expect, expect))
        self.assertTrue(worker.debug_log_path.name.endswith("_2p.txt"))
        self.assertEqual(worker.kw["video_path"].suffix, ".mp4")


class ModeTest(Base):
    def test_side_panel_saves_per_side(self) -> None:
        self.assertEqual(session.side_view_path("1p"), assist_view.ASSIST_VIEW_PATH)
        self.assertEqual(session.side_view_path("2p").name, "tetris_assist_view_2p.json")
        self.assertEqual(session.side_view_path("solo").name, "tetris_assist_view_solo.json")
        scroll = MODE.side_panel("2p", None)
        panel = scroll.widget()
        self.assertIsInstance(panel, AssistSettingsPanel)
        panel._set("templates", False)
        self.assertFalse(load_assist_view(session.side_view_path("2p"))["templates"])
        self.assertTrue(load_assist_view(session.side_view_path("1p"))["templates"], "ほかのサイドの設定まで変わった")

    def test_mode_is_ready(self) -> None:
        self.assertEqual((MODE.id, MODE.default_engine()), ("tetris", "KasugAI"))
        self.assertIsNotNone(MODE.start_assist)
        self.assertIsNotNone(MODE.calibrate)


if __name__ == "__main__":
    unittest.main()
