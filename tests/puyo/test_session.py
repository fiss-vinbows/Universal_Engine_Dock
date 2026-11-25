"""ぷよの支援モードの開始・終了(session.py)と、サイドの設定項目(side_panel.py・side_options.py)のテスト。"""

from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402

import tests.config_isolation as isolation  # noqa: E402
from PyQt6 import QtCore, QtWidgets  # noqa: E402

from uedock.capture.game_window import GameWindow  # noqa: E402
from uedock.game_mode import LAYOUT_SOLO, LAYOUT_VERSUS, AssistRequest, EngineSpec, SideSetup  # noqa: E402
from uedock.games.puyo import MODE, session, side_options  # noqa: E402
from uedock.games.puyo.engine.ama_client import HAIPUYO_BRIDGE_ARGS, PEEK_BRIDGE_ARGS  # noqa: E402
from uedock.games.puyo.engine.registry import BUILTIN_BOOK, BookEntry, Registry  # noqa: E402
from uedock.games.puyo.side_options import SideOptions  # noqa: E402
from uedock.games.puyo.side_panel import PuyoSidePanel  # noqa: E402
from uedock.plugin_api import FEATURE_DEBUG_LOG, Plugin  # noqa: E402
from uedock.plugin_loader import PluginManager  # noqa: E402
from uedock.ui.toggle_switch import ToggleSwitch  # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
FIXTURES = Path(__file__).parent / "fixtures"
YAMANEKO = EngineSpec("YamaNEKO", Path("C:/engines/yamaneko-bridge.exe"))
OTHER = EngineSpec("MyAI", Path("C:/ai/my.exe"), args=("--fast",))


class FakeClient:
    """AmaClient の代わり。起動引数を覚える。"""

    started: list["FakeClient"] = []

    def __init__(self, **kw) -> None:  # noqa: ANN003
        self.kw = kw
        self.engine = "yamaneko" if Path(kw["exe_path"]).name.startswith("yamaneko") else "ama"
        self.closed = 0
        FakeClient.started.append(self)

    def close(self) -> None:
        self.closed += 1


class FakeWorker(QtCore.QObject):
    """AssistWorker の代わり。観測のスレッドは動かさず、渡された設定を覚える。"""

    draw_data_ready = QtCore.pyqtSignal(object)
    status_changed = QtCore.pyqtSignal(str)
    fatal_error = QtCore.pyqtSignal(str)
    fps_ready = QtCore.pyqtSignal(float)
    made: list["FakeWorker"] = []

    def __init__(self, calibration, client, debug_log_path, **kw) -> None:  # noqa: ANN001, ANN003
        super().__init__()
        self.calibration, self.client, self.debug_log_path, self.kw = calibration, client, debug_log_path, kw
        self.started = self.stopped = 0
        FakeWorker.made.append(self)

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def wait(self, _ms: int = 0) -> bool:
        return True


def request(sides: list[SideSetup], layout: str = LAYOUT_VERSUS, opponents: dict | None = None,
            window: GameWindow | None = None, plugins: PluginManager | None = None, debug_log: bool = False) -> AssistRequest:
    return AssistRequest(layout=layout, sides=tuple(sides), game_window=window, plugins=plugins or PluginManager(),
                         opponents=opponents or {}, debug_log=debug_log)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        isolation.reset()
        FakeClient.started = []
        FakeWorker.made = []
        # 読み込み時に決まる置き場所を一時フォルダへ(ほかのテストが先に読み込んでいても本番の設定を使わない)
        config = isolation.ROOT / "config"
        for target, name, value in (
            (session, "CONFIG_PATH", config / "puyo_calibration.json"),
            (session, "SOLO_CONFIG_PATH", config / "puyo_calibration_solo.json"),
            (session, "AmaClient", FakeClient),
            (session, "AssistWorker", FakeWorker),
            (session, "_exclude_window_from_screen_capture", lambda _w: True),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(Registry, "load", side_effect=lambda *a: self.registry)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = Registry()
        self.messages: list[str] = []
        for kind in ("warning", "information", "critical"):
            patcher = mock.patch.object(QtWidgets.QMessageBox, kind, side_effect=lambda _p, _t, text: self.messages.append(text))
            patcher.start()
            self.addCleanup(patcher.stop)

    def put_calibration(self, solo: bool = False) -> None:
        path = session.SOLO_CONFIG_PATH if solo else session.CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / "calibration_1080p.json", path)


class SideOptionsTest(Base):
    def test_defaults_and_round_trip_per_side(self) -> None:
        self.assertEqual(side_options.load("1p"), SideOptions())
        side_options.save("1p", SideOptions(language="en", book="GTR", cpu_use="half", haipuyo=True, gaze=False))
        side_options.save("2p", SideOptions(peek=True))
        self.assertEqual(side_options.load("1p"), SideOptions(language="en", book="GTR", cpu_use="half", haipuyo=True, gaze=False))
        self.assertEqual(side_options.load("2p"), SideOptions(peek=True))
        self.assertEqual(side_options.load("solo"), SideOptions())

    def test_broken_values_fall_back_to_defaults(self) -> None:
        path = side_options.options_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"1p": {"language": 3, "haipuyo": "yes", "peek": true, "unknown": 1}, "2p": 5}', encoding="utf-8")
        self.assertEqual(side_options.load("1p"), SideOptions(peek=True))
        self.assertEqual(side_options.load("2p"), SideOptions())
        path.write_text("{", encoding="utf-8")
        self.assertEqual(side_options.load("1p"), SideOptions())

    def test_search_threads(self) -> None:
        with mock.patch.object(side_options.os, "cpu_count", return_value=16):
            self.assertIsNone(SideOptions().search_threads(None))  # 自動・1つだけ: エンジンの既定(全コア)
            self.assertEqual(SideOptions().search_threads(8), 8)  # 自動は両方の時の半分のまま
            self.assertEqual(SideOptions(cpu_use="all").search_threads(8), 16)
            self.assertEqual(SideOptions(cpu_use="half").search_threads(None), 8)
            self.assertEqual(SideOptions(cpu_use="quarter").search_threads(8), 4)
            self.assertEqual(SideOptions(cpu_use="two").search_threads(8), 2)


class SidePanelTest(Base):
    def test_changes_are_saved_and_shown_again(self) -> None:
        self.registry = Registry([BookEntry("GTR定跡", "C:/books/gtr.json")])
        panel = PuyoSidePanel("1p")
        self.assertEqual([panel.book_combo.itemData(i) for i in range(panel.book_combo.count())], ["", BUILTIN_BOOK, "GTR定跡"])
        panel.book_combo.setCurrentIndex(panel.book_combo.findData("GTR定跡"))
        panel.threads_combo.setCurrentIndex(panel.threads_combo.findData("quarter"))
        panel.low_priority.setChecked(True)
        panel.haipuyo.setChecked(True)
        panel.gaze.setChecked(False)
        want = SideOptions(book="GTR定跡", cpu_use="quarter", low_priority=True, haipuyo=True, gaze=False)
        self.assertEqual(side_options.load("1p"), want)
        self.assertEqual(side_options.load("2p"), SideOptions(), "ほかのサイドの設定まで変わった")
        # ゲーム種別を切り替えて戻すと画面は作り直される。その時に前の設定が出る
        self.assertEqual(PuyoSidePanel("1p").options(), want)

    def test_removed_book_becomes_unused(self) -> None:
        side_options.save("1p", SideOptions(book="消した定跡"))
        panel = PuyoSidePanel("1p")
        self.assertEqual(panel.options().book, "")

    def test_yamaneko_only_options_are_disabled_otherwise(self) -> None:
        panel = PuyoSidePanel("1p")
        for w in (panel.haipuyo, panel.peek, panel.threads_combo):
            self.assertTrue(w.isEnabled())
        panel.set_engine("ama")  # 共通のメイン画面が、思考エンジンが変わるたびに呼ぶ
        for w in (panel.haipuyo, panel.peek, panel.threads_combo):
            self.assertFalse(w.isEnabled())
        self.assertTrue(panel.low_priority.isEnabled())  # 優先度はどのエンジンでも
        panel.set_engine("YamaNEKO")
        self.assertTrue(panel.haipuyo.isEnabled())

    def test_all_switches_are_toggles_and_solo_has_no_gaze(self) -> None:
        panel = PuyoSidePanel("solo")
        for w in panel.findChildren(QtWidgets.QCheckBox):
            self.assertIsInstance(w, ToggleSwitch)
        self.assertTrue(panel.gaze.isHidden(), "一人用に「相手の盤面を見る」が出ている")
        versus = PuyoSidePanel("1p")
        self.assertFalse(versus.gaze.isHidden())

    def test_mode_makes_the_panel(self) -> None:
        self.assertIsInstance(MODE.side_panel("2p", None), PuyoSidePanel)


class MakeClientTest(Base):
    def test_threads_priority_and_yamaneko_args(self) -> None:
        with mock.patch.object(side_options.os, "cpu_count", return_value=16):
            client = session.make_client(SideSetup("1p", YAMANEKO), SideOptions(), None)
            self.assertEqual(client.kw["exe_path"], YAMANEKO.exe)
            self.assertIsNone(client.kw["search_threads"])
            self.assertFalse(client.kw["low_priority"])
            self.assertIsNone(client.kw["extra_args"])  # どちらも無ければ None
            self.assertIsNone(client.kw["custom_args"])
            client = session.make_client(SideSetup("1p", YAMANEKO), SideOptions(cpu_use="quarter", low_priority=True, peek=True), 8)
            self.assertEqual((client.kw["search_threads"], client.kw["low_priority"]), (4, True))
            self.assertEqual(client.kw["extra_args"], PEEK_BRIDGE_ARGS)
            client = session.make_client(SideSetup("1p", YAMANEKO), SideOptions(haipuyo=True, peek=True), None)
            self.assertEqual(client.kw["extra_args"], HAIPUYO_BRIDGE_ARGS + PEEK_BRIDGE_ARGS)

    def test_registered_engine_is_started_with_its_args(self) -> None:
        client = session.make_client(SideSetup("2p", OTHER), SideOptions(), None)
        self.assertEqual((client.kw["exe_path"], client.kw["custom_args"]), (OTHER.exe, ["--fast"]))

    def test_book_wraps_the_client_and_missing_book_closes_it(self) -> None:
        from uedock.games.puyo.engine.book_client import BookClient

        client = session.make_client(SideSetup("1p", YAMANEKO), SideOptions(book=BUILTIN_BOOK), None)
        self.assertIsInstance(client, BookClient)
        self.assertEqual(client.engine, "yamaneko")  # 中の思考エンジンの種類がそのまま見える
        self.registry = Registry([BookEntry("無い", str(isolation.ROOT / "none.json"))])
        with self.assertRaises(ValueError):
            session.make_client(SideSetup("1p", YAMANEKO), SideOptions(book="無い"), None)
        self.assertEqual(FakeClient.started[-1].closed, 1, "定跡が読めなかったのに思考エンジンが動いたまま")


class WatchesEnemyTest(unittest.TestCase):
    def test_only_a_puyo_or_unknown_opponent_is_watched(self) -> None:
        on, off = SideOptions(), SideOptions(gaze=False)
        versus = lambda opponent: request([SideSetup("1p", YAMANEKO)], opponents={"1p": opponent})  # noqa: E731
        self.assertTrue(session.watches_enemy(versus("puyo"), "1p", on))
        self.assertTrue(session.watches_enemy(versus(""), "1p", on))  # 相手は支援なし: 設定に従う
        self.assertFalse(session.watches_enemy(versus(""), "1p", off))
        # 相手のサイドがテトリスなら、設定に関わらず見ない(v0.1 では別種の相手を凝視しない)
        self.assertFalse(session.watches_enemy(versus("tetris"), "1p", on))
        solo = request([SideSetup("solo", YAMANEKO)], layout=LAYOUT_SOLO)
        self.assertFalse(session.watches_enemy(solo, "solo", on))


class SessionTest(Base):
    def test_refuses_before_starting_engines_when_position_is_unknown(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            session.start_assist(request([SideSetup("1p", YAMANEKO)]))
        self.assertIn("盤面の位置が分かりません", str(ctx.exception))
        self.assertEqual(FakeClient.started, [], "始められないのに思考エンジンを起動した")

    def test_engine_failure_closes_the_ones_already_started(self) -> None:
        self.put_calibration()

        def flaky(**kw):  # noqa: ANN003, ANN202
            if len(FakeClient.started) == 1:
                raise FileNotFoundError("ブリッジが無い")
            return FakeClient(**kw)

        with mock.patch.object(session, "AmaClient", flaky), self.assertRaises(RuntimeError) as ctx:
            session.start_assist(request([SideSetup("1p", YAMANEKO), SideSetup("2p", OTHER)]))
        self.assertIn("ブリッジが無い", str(ctx.exception))
        self.assertEqual([c.closed for c in FakeClient.started], [1])

    def test_both_sides_with_swapped_calibration(self) -> None:
        # 1P・2P の両方がぷよなら1つのセッションで動かす。2P は対戦用キャリブレーションの自分と相手を入れ替える
        self.put_calibration()
        side_options.save("2p", SideOptions(language="en", haipuyo=True))
        with mock.patch.object(session.os, "cpu_count", return_value=16):
            s = session.start_assist(request([SideSetup("1p", YAMANEKO), SideSetup("2p", OTHER)],
                                             opponents={"1p": "puyo", "2p": "puyo"}))
        self.assertEqual([c.kw["search_threads"] for c in FakeClient.started], [8, 8], "2つの時は探索スレッドを半分ずつ")
        self.assertFalse(s.is_running())
        s.begin()
        self.addCleanup(s.stop)
        self.assertTrue(s.is_running())
        one, two = FakeWorker.made
        self.assertEqual((one.kw["side"], two.kw["side"]), ("1p", "2p"))
        self.assertFalse(one.calibration.self_on_right())
        self.assertTrue(two.calibration.self_on_right())
        self.assertEqual((one.kw["solo"], two.kw["solo"]), (False, False))
        self.assertFalse(two.kw["haipuyo"], "配ぷよ読みは YamaNEKO だけ(登録したエンジンでは使わない)")
        # 情報パネルの表記はメイン画面の「表示の言語」に従う
        self.assertEqual(s.overlays["2p"].language, "ja")
        self.assertEqual((one.started, two.started), (1, 1))
        self.assertEqual((one.debug_log_path, two.debug_log_path), (None, None))
        # 状態はバーに出さず、ツールチップに2行で残す
        s._on_status_changed("1p", "思考中")
        self.assertIn("1P: 思考中", s.badge.toolTip())
        s.stop()
        self.assertFalse(s.is_running())
        self.assertEqual((one.stopped, two.stopped), (1, 1))
        self.assertEqual([c.closed for c in FakeClient.started], [1, 1])
        self.assertIsNone(s.badge)
        s.stop()  # 終了バッジで止めた後、メイン画面がもう一度呼ぶ
        self.assertEqual([c.closed for c in FakeClient.started], [1, 1])

    def test_opencv_uses_one_thread_while_running(self) -> None:
        """支援モードの間だけ OpenCV を1スレッドにし、終えたら元に戻す。"""
        import cv2

        self.put_calibration()
        before = cv2.getNumThreads()
        self.addCleanup(cv2.setNumThreads, before)
        cv2.setNumThreads(6)
        s = session.start_assist(request([SideSetup("1p", YAMANEKO)]))
        s.begin()
        self.addCleanup(s.stop)
        self.assertEqual(cv2.getNumThreads(), session.OPENCV_THREADS)
        s.stop()
        self.assertEqual(cv2.getNumThreads(), 6)
        s.stop()  # もう一度呼んでも変えない
        self.assertEqual(cv2.getNumThreads(), 6)

    def test_tetris_opponent_is_not_watched(self) -> None:
        self.put_calibration()
        side_options.save("1p", SideOptions(haipuyo=True))
        s = session.start_assist(request([SideSetup("1p", YAMANEKO)], opponents={"1p": "tetris"}))
        s.begin()
        self.addCleanup(s.stop)
        worker = FakeWorker.made[0]
        self.assertTrue(worker.kw["solo"], "相手がテトリスなのに相手の盤面を読む")
        self.assertTrue(worker.kw["haipuyo"])

    def test_solo_uses_its_own_calibration_file_and_1p_slot(self) -> None:
        self.put_calibration()
        with self.assertRaises(RuntimeError):  # 一人用は画面の配置が違うので、対戦用のファイルでは始めない
            session.start_assist(request([SideSetup("solo", YAMANEKO)], layout=LAYOUT_SOLO))
        self.put_calibration(solo=True)
        s = session.start_assist(request([SideSetup("solo", YAMANEKO)], layout=LAYOUT_SOLO))
        s.begin()
        self.addCleanup(s.stop)
        self.assertEqual((FakeWorker.made[0].kw["side"], FakeWorker.made[0].kw["solo"]), ("1p", True))

    def test_broken_calibration_is_reported(self) -> None:
        session.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        session.CONFIG_PATH.write_text('{"self": {}}', encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            session.start_assist(request([SideSetup("1p", YAMANEKO)]))
        self.assertIn("キャリブレーションが不正です", str(ctx.exception))

    def test_game_window_gives_the_position_and_moving_it_stops(self) -> None:
        window = GameWindow(77, "PuyoPuyoTetris2", 100, 50, 1920, 1080)
        rect = [(100, 50, 1920, 1080)]
        with mock.patch.object(session, "detect_calibration", wraps=session.detect_calibration) as detect, \
                mock.patch.object(session, "ScreenCapture") as capture, \
                mock.patch.object(session, "title_bar_rect", return_value=(92, 19, 1936, 31)), \
                mock.patch.object(session, "client_rect", side_effect=lambda _h: rect[0]):
            # 盤面の暗い矩形が見つからない画面(真っ黒)。計算した位置のまま使う
            capture.return_value.__enter__.return_value.grab_bgr.return_value = np.zeros((1080, 1920, 3), np.uint8)
            s = session.start_assist(request([SideSetup("1p", YAMANEKO)], window=window))  # キャリブレーションのファイルは要らない
            s.begin()
            self.addCleanup(s.stop)
            self.assertEqual(detect.call_args.args[0], window)
            self.assertFalse(detect.call_args.kwargs["solo"])
            self.assertIsNotNone(s.badge.anchor)  # タイトルバーの中央に置く
            # 1920×1080 のウィンドウなら、盤面は基準の位置をウィンドウの左上だけずらしたところ
            (x, y), *_ = FakeWorker.made[0].calibration.self_player.board_quad
            self.assertEqual((round(x), round(y)), (272 + 100, 159 + 50))
            # プレイヤー名の帯に、使っているエンジンの名前を重ねる
            self.assertEqual([(p.side, p.text) for p in s.nameplates], [("1p", "YamaNEKO")])
            s._check_game_window()
            self.assertTrue(s.is_running())
            rect[0] = (300, 50, 1920, 1080)
            s._check_game_window()
        self.assertFalse(s.is_running())
        self.assertIn("ゲームのウィンドウが動いた", self.messages[-1])
        self.assertEqual(s.nameplates, [], "止めたのにエンジン名の札が残った")

    def test_extension_makes_the_worker_and_is_told_before_start(self) -> None:
        """プラグインの拡張(uedock/extension.py)が観測ループを作れること(create_worker)と、動かす直前に呼ばれること(session_ready)。"""
        from dataclasses import replace

        from uedock.extension import Extension

        self.put_calibration()
        calls = []

        class Ext(Extension):
            def create_worker(self, game, side, context):  # noqa: ANN001, ANN201
                calls.append(("create", game, side))
                return FakeWorker(**context["worker_args"], made_by="ext") if side == "2p" else None

            def session_ready(self, game, session_) -> None:  # noqa: ANN001
                calls.append(("ready", game, sorted(session_.workers), [w.started for w in session_.workers.values()]))

        req = replace(request([SideSetup("1p", YAMANEKO), SideSetup("2p", YAMANEKO)]), extensions=(Ext(),))
        s = session.start_assist(req)
        s.begin()
        self.addCleanup(s.stop)
        self.assertEqual(calls[:2], [("create", "puyo", "1p"), ("create", "puyo", "2p")])
        self.assertEqual(calls[2], ("ready", "puyo", ["1p", "2p"], [0, 0]), "動かす前に呼ばれていない")
        self.assertNotIn("made_by", s.workers["1p"].kw, "拡張が作らなかった側は標準の観測ループ")
        self.assertEqual(s.workers["2p"].kw["made_by"], "ext")
        self.assertEqual([w.started for w in s.workers.values()], [1, 1])

    def test_fatal_error_stops_and_is_reported_once(self) -> None:
        self.put_calibration()
        s = session.start_assist(request([SideSetup("1p", YAMANEKO)]))
        s.begin()
        s._on_fatal_error("認識で例外")
        self.assertFalse(s.is_running())
        self.assertIn("認識で例外", self.messages[-1])
        s._on_fatal_error("後から届いたもう片方のエラー")
        self.assertEqual(len(self.messages), 1)

    def test_debug_log_needs_the_plugin_and_the_switch(self) -> None:
        self.put_calibration()
        plugins = PluginManager([Plugin(id="debug_log", name="ログ出力", features=frozenset({FEATURE_DEBUG_LOG}))])
        with mock.patch.object(session, "app_root", return_value=isolation.ROOT):
            for wanted, manager, expect in ((True, PluginManager(), False), (False, plugins, False), (True, plugins, True)):
                FakeWorker.made = []
                s = session.start_assist(request([SideSetup("2p", YAMANEKO)], plugins=manager, debug_log=wanted))
                s.begin()
                path = FakeWorker.made[0].debug_log_path
                s.stop()
                self.assertEqual(path is not None, expect)
        self.assertEqual(path.parent, isolation.ROOT / "debug_logs")
        self.assertTrue(path.name.endswith("_2p.txt"))


if __name__ == "__main__":
    unittest.main()


class BadgeWidgetTest(unittest.TestCase):
    """プラグインの拡張が、上のバーに部品を足せること(add_widget)。"""

    def test_badge_gets_extra_widget(self) -> None:
        from uedock.games.puyo.worker import ControlBadge

        badge = ControlBadge(on_stop=lambda: None)
        self.addCleanup(badge.close)
        width = badge.width()
        button = QtWidgets.QPushButton("足した部品")
        badge.add_widget(button)
        self.assertEqual(badge.extra_widgets, [button])
        self.assertGreater(badge.width(), width, "部品の分だけ広がっていない")
        self.assertTrue(badge.windowFlags() & QtCore.Qt.WindowType.WindowDoesNotAcceptFocus,
                        "クリックでバッジが前面になり、ゲームが操作を受け付けなくなる")
