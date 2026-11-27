"""メイン画面の枠(uedock/ui/main_window.py)とエンジンの登録画面のテスト。画面は出さずに動かす。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests.config_isolation as isolation  # noqa: E402
from PyQt6 import QtCore, QtWidgets  # noqa: E402

from uedock.engine_registry import EngineEntry, Registry, SideSelection, portable_path  # noqa: E402
from uedock.game_mode import LAYOUT_SOLO, NO_GAME, EngineSpec, GameMode, SimulatorEntry  # noqa: E402
from uedock.plugin_loader import PluginManager  # noqa: E402
from uedock.ui import main_window  # noqa: E402
from uedock.ui.engine_dialog import EngineRegistryDialog  # noqa: E402
from uedock.ui.main_window import MISSING_SUFFIX, MainWindow  # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
EXE = Path(sys.executable)  # 実在する実行ファイルの代わり


class FakeSession:
    def __init__(self, request) -> None:  # noqa: ANN001
        self.request = request
        self.running = True
        self.begun = 0
        self.stopped = 0

    def begin(self) -> None:
        self.begun += 1

    def stop(self) -> None:
        self.running = False
        self.stopped += 1

    def is_running(self) -> bool:
        return self.running


def fake_modes(started: list[FakeSession]) -> dict[str, GameMode]:
    def start(request):  # noqa: ANN001, ANN202
        started.append(FakeSession(request))
        return started[-1]

    return {
        "puyo": GameMode("puyo", "ぷよ", builtin_engines=(EngineSpec("YamaNEKO", EXE),), start_assist=start,
                         side_panel=lambda side, parent: QtWidgets.QLabel(f"定跡({side})", parent),
                         simulators=(SimulatorEntry("ぷよのシミュレーターを開く", lambda parent: QtWidgets.QLabel("sim")),)),
        "tetris": GameMode("tetris", "テトリス", builtin_engines=(EngineSpec("KasugAI", EXE),), start_assist=start,
                           calibrate=lambda layout, parent: None),
    }


class MainWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        isolation.reset()
        patcher = patch.object(main_window, "find_game_window", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.warnings: list[str] = []
        patcher = patch.object(QtWidgets.QMessageBox, "warning", side_effect=lambda _p, _t, text: self.warnings.append(text))
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, registry: Registry | None = None) -> tuple[MainWindow, list[FakeSession]]:
        if registry is not None:
            registry.save()
        window = MainWindow(PluginManager())
        self.addCleanup(window.close)
        started: list[FakeSession] = []
        window.modes = fake_modes(started)
        # 起動した時に本物の同梱のエンジンを登録へ入れているので、偽のゲームモードの同梱のエンジンで入れ直す
        window.registry = Registry(list(registry.engines) if registry is not None else [])
        window._add_bundled_engines()
        window._rebuild_mode_buttons()
        window._refresh_sides()
        return window, started

    def test_standard_modes_are_listed_per_side(self) -> None:
        window = MainWindow(PluginManager())
        self.addCleanup(window.close)
        box = window.side_boxes["1p"]
        self.assertEqual([box.game_combo.itemText(i) for i in range(box.game_combo.count())], ["支援なし", "ぷよ", "テトリス"])
        self.assertEqual(box.game(), "puyo")
        self.assertEqual(box.engine(), "YamaNEKO")
        self.assertEqual(window.side_boxes["2p"].game(), NO_GAME)
        self.assertFalse(window.side_boxes["2p"].engine_combo.isEnabled(), "支援なしでも思考エンジンを選べる")
        # ぷよの設定項目(定跡など)が枠に入る
        self.assertEqual(type(box._panel).__name__, "PuyoSidePanel")
        # テトリスに切り替えると、テトリスのエンジンと設定項目(テンプレ等)に入れ替わる
        box.game_combo.setCurrentIndex(box.game_combo.findData("tetris"))
        self.assertEqual(box.engine(), "KasugAI")
        self.assertEqual(type(box._panel.widget()).__name__, "AssistSettingsPanel")
        # どのサイドも支援なしなら始められず、理由を出す
        box.game_combo.setCurrentIndex(box.game_combo.findData(NO_GAME))
        self.assertFalse(window.start_assist())
        self.assertIn("支援するサイドがありません", self.warnings[-1])
        self.assertFalse(window.assist_running())

    def test_tetris_cannot_be_chosen_on_esports(self) -> None:
        from uedock.capture.game_window import GameWindow

        esports = GameWindow(5, "Puyo Puyo Champions", 0, 0, 1920, 1080)
        pt2 = GameWindow(6, "PuyoPuyoTetris2", 0, 0, 1920, 1080)
        selection = SideSelection(games={"1p": "puyo", "2p": "tetris"})
        selection.save()
        main_window.find_game_window.return_value = esports
        window = MainWindow(PluginManager())
        self.addCleanup(window.close)
        for side in ("1p", "2p", "solo"):
            combo = window.side_boxes[side].game_combo
            index = combo.findData("tetris")
            self.assertFalse(combo.model().item(index).isEnabled(), f"ぷよぷよeスポーツでテトリスを選べる({side})")
            self.assertEqual(combo.itemText(index), "テトリス" + main_window.UNAVAILABLE_SUFFIX)
            self.assertIn("テトリスがありません", combo.itemData(index, QtCore.Qt.ItemDataRole.ToolTipRole))
            self.assertTrue(combo.model().item(combo.findData("puyo")).isEnabled())
        # 前に選んであったテトリスは選んだまま残す(ぷよぷよテトリス2 に戻ると、そのまま使える)。支援モードは理由を出して始めない
        self.assertEqual(window.side_boxes["2p"].game(), "tetris")
        self.assertFalse(window.start_assist())
        self.assertIn("ぷよぷよeスポーツにはテトリスがありません(2P)", self.warnings[-1])
        self.assertEqual(SideSelection.load().game("2p"), "tetris", "選んであったゲーム種別を消した")
        # ぷよぷよテトリス2 を検出し直すと、また選べる
        main_window.find_game_window.return_value = pt2
        window.detect_btn.click()
        combo = window.side_boxes["2p"].game_combo
        self.assertTrue(combo.model().item(combo.findData("tetris")).isEnabled())
        self.assertEqual(combo.itemText(combo.findData("tetris")), "テトリス")
        self.assertEqual(window.side_boxes["2p"].game(), "tetris")
        # ウィンドウが見つからない時(キャプチャボード)は、どれも選べる
        # (「検出」ボタンは見つからない時に一覧を開くので、表示を出し直すところだけを呼ぶ)
        main_window.find_game_window.return_value = None
        window._refresh_window_status()
        combo = window.side_boxes["2p"].game_combo
        self.assertTrue(combo.model().item(combo.findData("tetris")).isEnabled())

    def test_changing_game_switches_engines_and_is_saved(self) -> None:
        registry = Registry([EngineEntry("tetris", "Cold Clear 2", str(EXE)), EngineEntry("puyo", "ama", "無い.exe")])
        window, _ = self.make(registry)
        box = window.side_boxes["1p"]
        self.assertEqual([box.engine_combo.itemText(i) for i in range(box.engine_combo.count())],
                         ["YamaNEKO", "ama" + MISSING_SUFFIX])
        box.game_combo.setCurrentIndex(box.game_combo.findData("tetris"))
        self.assertEqual([box.engine_combo.itemData(i) for i in range(box.engine_combo.count())], ["KasugAI", "Cold Clear 2"])
        self.assertEqual(box.engine(), "KasugAI")
        box.engine_combo.setCurrentIndex(1)
        saved = SideSelection.load()
        self.assertEqual(saved.game("1p"), "tetris")
        self.assertEqual(saved.engine_name("1p", window.modes["tetris"]), "Cold Clear 2")
        # ぷよに切り替えても、テトリスで選んだエンジンは消えない
        box.game_combo.setCurrentIndex(box.game_combo.findData("puyo"))
        self.assertEqual(box.engine(), "YamaNEKO")
        self.assertEqual(SideSelection.load().engine_name("1p", window.modes["tetris"]), "Cold Clear 2")

    def test_side_panel_follows_the_game(self) -> None:
        window, _ = self.make()
        box = window.side_boxes["1p"]
        self.assertEqual(box._panel.text(), "定跡(1p)")
        box.game_combo.setCurrentIndex(box.game_combo.findData("tetris"))
        self.assertIsNone(box._panel)

    def test_initial_size(self) -> None:
        window, _ = self.make()
        self.assertEqual((window.width(), window.height()), main_window.INITIAL_SIZE)

    def test_mode_buttons(self) -> None:
        window, _ = self.make()
        self.assertEqual([b.text() for b in window.simulator_buttons], ["ぷよのシミュレーターを開く"])
        self.assertEqual(list(window.calibrate_buttons), ["tetris"])
        window.simulator_buttons[0].click()
        self.assertEqual(len(window._simulators), 1)

    def test_mixed_sides_start_and_stop(self) -> None:
        window, started = self.make()
        box = window.side_boxes["2p"]
        box.game_combo.setCurrentIndex(box.game_combo.findData("tetris"))
        self.assertTrue(window.start_assist())
        self.assertEqual([[s.side for s in f.request.sides] for f in started], [["1p"], ["2p"]])
        self.assertEqual([f.begun for f in started], [1, 1], "エンジンの起動の後に観測を始めていない")
        self.assertTrue(window.isHidden(), "支援モード中はメイン画面を隠す(ゲームに重なると盤面の位置合わせに写り込む)")
        self.assertEqual(window.assist_btn.text(), "支援モード終了")
        self.assertFalse(window.tabs.isEnabled(), "支援モード中に選択を変えられる")
        window.assist_btn.click()
        self.assertEqual([f.stopped for f in started], [1, 1])
        self.assertEqual(window.assist_btn.text(), "支援モード開始")
        self.assertTrue(window.tabs.isEnabled())
        self.assertFalse(window.isHidden(), "止めた後にメイン画面が戻らない")

    def test_session_that_stopped_itself_stops_the_rest(self) -> None:
        window, started = self.make()
        box = window.side_boxes["2p"]
        box.game_combo.setCurrentIndex(box.game_combo.findData("tetris"))
        window.start_assist()
        started[0].running = False  # ゲームのウィンドウが動いた等で自分で止まった
        window._check_sessions()
        self.assertFalse(window.assist_running())
        self.assertEqual(started[1].stopped, 1)
        self.assertEqual(window.assist_btn.text(), "支援モード開始")

    def test_failure_while_starting_leaves_nothing_running(self) -> None:
        window, started = self.make()

        def fail(request):  # noqa: ANN001, ANN202
            raise OSError("エンジンを起動できない")

        window.modes["tetris"] = GameMode("tetris", "テトリス", builtin_engines=(EngineSpec("KasugAI", EXE),), start_assist=fail)
        window.selection.set_game("2p", "tetris")
        self.assertFalse(window.start_assist())
        self.assertIn("エンジンを起動できない", self.warnings[-1])
        self.assertEqual([f.stopped for f in started], [1], "先に始めたサイドが動いたまま")
        self.assertFalse(window.assist_running())

    def test_failure_in_begin_stops_everything_and_shows_the_window(self) -> None:
        window, started = self.make()

        class NoBoard(FakeSession):
            def begin(self) -> None:
                raise RuntimeError("盤面の位置が分かりません")

        window.modes["puyo"] = GameMode("puyo", "ぷよ", builtin_engines=(EngineSpec("YamaNEKO", EXE),), start_assist=NoBoard)
        self.assertFalse(window.start_assist())
        self.assertEqual(self.warnings[-1], "盤面の位置が分かりません")
        self.assertFalse(window.assist_running())
        self.assertFalse(window.isHidden())

    def test_debug_log_switch_needs_the_plugin(self) -> None:
        from uedock.plugin_api import FEATURE_DEBUG_LOG, Plugin

        window, started = self.make()
        self.assertTrue(window.debug_log_switch.isHidden(), "ログ出力プラグインが無いのに切り替えが出ている")
        window.debug_log_switch.setChecked(True)
        window.start_assist()
        self.assertFalse(started[-1].request.debug_log)
        window.stop_assist()
        window.plugins.plugins.append(Plugin(id="debug_log", name="ログ出力", features=frozenset({FEATURE_DEBUG_LOG})))
        window._rebuild_mode_buttons()
        self.assertFalse(window.debug_log_switch.isHidden())
        window.start_assist()
        self.assertTrue(started[-1].request.debug_log)

    def test_solo_tab_uses_only_solo_side(self) -> None:
        window, started = self.make()
        window.tabs.setCurrentIndex(1)
        self.assertEqual(SideSelection.load().layout, LAYOUT_SOLO)
        self.assertTrue(window.start_assist())
        self.assertEqual([[s.side for s in f.request.sides] for f in started], [["solo"]])


class EngineDialogTest(unittest.TestCase):
    def test_weights_question_explains_and_hints_cold_clear(self) -> None:
        from uedock.ui.engine_dialog import weights_question

        plain = weights_question("puyo", "C:/engines/ama-bridge.exe")
        self.assertIn("分からなければ「いいえ」", plain)
        self.assertNotIn("cold_clear_beginner", plain)
        self.assertIn("cold_clear_beginner.json", weights_question("tetris", "C:/x/cold-clear-2.exe"))

    def test_bundled_engines_are_registered_at_start(self) -> None:
        window = MainWindow(PluginManager())
        self.addCleanup(window.close)
        saved = Registry.load()
        self.assertEqual(sorted(saved.bundled), ["puyo/YamaNEKO", "tetris/KasugAI"])
        self.assertEqual([e.name for e in saved.for_game("puyo")], ["YamaNEKO"])
        self.assertEqual([e.name for e in saved.for_game("tetris")], ["KasugAI"])
        # 登録へ入れた同梱のエンジンの名前は、消した後に新しい版で登録し直せる
        self.assertEqual(window._reserved_engine_names(), {"puyo": set(), "tetris": set()})

    def test_change_and_restore_bundled_engine(self) -> None:
        modes = fake_modes([])
        registry = Registry()
        registry.add_bundled(modes.values())
        dialog = EngineRegistryDialog(registry, modes)
        bundled = portable_path(EXE)  # 本体のフォルダの中なら相対パス
        self.assertEqual(dialog.list.item(0).text(), f"YamaNEKO (同梱)  —  {bundled}")
        # 新しい版に差し替える: 名前はそのまま、印は消える
        entry = dialog.engines[0]
        self.assertIn("見つかりません", dialog.change_engine(entry, "無い.exe", [], ""))
        new_exe = Path(__file__)
        self.assertIsNone(dialog.change_engine(entry, str(new_exe), ["--x"], ""))
        self.assertEqual((entry.name, entry.exe, entry.args), ("YamaNEKO", str(new_exe), ["--x"]))
        self.assertNotIn("(同梱)", dialog.list.item(0).text())
        self.assertEqual(registry.engines[0].exe, bundled, "保存する前に元の登録を書き換えた")
        # 同梱に戻す
        self.assertEqual(dialog.restore_bundled("puyo"), ["YamaNEKO"])
        self.assertEqual((dialog.engines[0].exe, dialog.engines[0].args), (bundled, []))
        self.assertIn("(同梱)", dialog.list.item(0).text())
        # 消してから戻すと、そのゲーム種別の先頭に入る
        self.assertIsNone(dialog.add_engine("puyo", "ama", str(EXE)))
        dialog.list.setCurrentRow(0)
        dialog._remove()
        self.assertEqual([e.name for e in dialog.engines if e.game == "puyo"], ["ama"])
        dialog.restore_bundled("puyo")
        self.assertEqual([e.name for e in dialog.engines if e.game == "puyo"], ["YamaNEKO", "ama"])
        self.assertEqual(dialog.restored, {"puyo/YamaNEKO"})

    def test_add_per_game_with_reserved_names(self) -> None:
        modes = fake_modes([])
        dialog = EngineRegistryDialog(Registry(), modes, {"puyo": {"YamaNEKO"}, "tetris": {"KasugAI"}})
        self.assertIn("もう使われている", dialog.add_engine("puyo", "YamaNEKO", str(EXE)))
        self.assertIn("見つかりません", dialog.add_engine("puyo", "ama", "無い.exe"))
        self.assertIn("名前を入れてください", dialog.add_engine("puyo", "  ", str(EXE)))
        self.assertIsNone(dialog.add_engine("puyo", "ama", str(EXE)))
        self.assertIn("もう使われている", dialog.add_engine("puyo", "ama", str(EXE)))
        # 同じ名前でも、ゲーム種別が違えば登録できる
        self.assertIsNone(dialog.add_engine("tetris", "ama", str(EXE), ["--threads", "4"]))
        self.assertEqual([(e.game, e.name, e.args) for e in dialog.engines], [("puyo", "ama", []), ("tetris", "ama", ["--threads", "4"])])
        # 一覧は選んでいるゲーム種別の分だけ
        self.assertEqual(dialog.list.count(), 1)
        dialog.game_combo.setCurrentIndex(dialog.game_combo.findData("tetris"))
        self.assertEqual(dialog.list.count(), 1)
        dialog.list.setCurrentRow(0)
        dialog._remove()
        self.assertEqual([(e.game, e.name) for e in dialog.engines], [("puyo", "ama")])


class ExtensionTest(unittest.TestCase):
    """プラグインの拡張(uedock/extension.py)が、メイン画面に項目を足し、支援モードの前後に呼ばれること。"""

    def setUp(self) -> None:
        isolation.reset()
        self.warnings: list[str] = []
        for target, attr, kwargs in (
            (QtWidgets.QMessageBox, "warning", {"side_effect": lambda _p, _t, text: self.warnings.append(text)}),
            (main_window, "find_game_window", {"return_value": None}),
        ):
            patcher = patch.object(target, attr, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make(self, ext, enabled: bool = True):  # noqa: ANN001, ANN201
        from uedock.plugin_api import Plugin

        plugins = PluginManager([Plugin(id="ext", name="拡張", extension=lambda: ext)], disabled=set() if enabled else {"ext"})
        window = MainWindow(plugins)
        self.addCleanup(window.close)
        started: list[FakeSession] = []
        window.modes = fake_modes(started)
        window._rebuild_mode_buttons()
        window._refresh_sides()
        return window, started

    @staticmethod
    def recorder():  # noqa: ANN205
        from uedock.extension import Extension

        class Ext(Extension):
            def __init__(self) -> None:
                self.calls: list = []
                self.refused: str | None = None

            def install(self, window) -> None:  # noqa: ANN001
                self.side_widget = QtWidgets.QLabel("サイドの項目")
                self.tool_widget = QtWidgets.QPushButton("足したボタン")
                window.add_side_widget("1p", self.side_widget)
                window.add_tool_widget(self.tool_widget)
                self.calls.append("install")

            def refresh(self, window, enabled: bool) -> None:  # noqa: ANN001
                self.calls.append(("refresh", enabled))

            def prepare(self, window, requests):  # noqa: ANN001, ANN201
                self.calls.append(("prepare", all(r.extensions == (self,) for _m, r in requests)))
                return self.refused

            def started(self, window, sessions) -> None:  # noqa: ANN001
                locked = not window.tabs.isEnabled() and not self.tool_widget.isEnabled()
                self.calls.append(("started", len(sessions), locked))

            def stopped(self, window) -> None:  # noqa: ANN001
                self.calls.append("stopped")

        return Ext()

    def test_install_refresh_and_assist_hooks(self) -> None:
        ext = self.recorder()
        window, started = self.make(ext)
        self.assertEqual(ext.calls[:2], ["install", ("refresh", True)])
        self.assertIn(ext.side_widget, window.side_boxes["1p"].findChildren(QtWidgets.QLabel), "サイドの枠に足されていない")
        self.assertTrue(window.start_assist())
        self.assertIn(("prepare", True), ext.calls, "支援モードに拡張が渡っていない")
        self.assertIn(("started", 1, True), ext.calls, "始めた後に呼ばれていない、または足したボタンを触れなくしていない")
        window.stop_assist(show=False)
        self.assertEqual(ext.calls[-1], "stopped")
        self.assertTrue(ext.tool_widget.isEnabled())

    def test_prepare_can_refuse(self) -> None:
        ext = self.recorder()
        ext.refused = "拡張の理由で始められません"
        window, started = self.make(ext)
        self.assertFalse(window.start_assist())
        self.assertEqual(self.warnings[-1], "拡張の理由で始められません")
        self.assertEqual(started, [])

    def test_disabled_plugin_is_not_called(self) -> None:
        ext = self.recorder()
        window, started = self.make(ext, enabled=False)
        self.assertIn(("refresh", False), ext.calls)
        self.assertTrue(window.start_assist())
        self.assertFalse(any(isinstance(c, tuple) and c[0] in ("prepare", "started") for c in ext.calls))
        self.assertEqual(started[0].request.extensions, ())
        window.stop_assist(show=False)


if __name__ == "__main__":
    unittest.main()
