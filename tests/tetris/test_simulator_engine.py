"""シミュレーターのAIの切り替え(同梱の KasugAI / プラグイン・登録の思考エンジン)のテスト。

シミュレーターだけでAIを選べるようにする。
プラグイン方式: Cold Clear 2 以外の思考エンジンはプラグイン(src/plugin_api.py の engines)が足す。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests.tetris.config_isolation  # noqa: E402,F401  本番の設定ファイル・プラグインを使わない
from PyQt6 import QtWidgets  # noqa: E402

from uedock import plugin_loader  # noqa: E402
from uedock.games.tetris.education import keybindings  # noqa: E402
from uedock.games.tetris.education.advisor import AI_ID, Advisor  # noqa: E402
from uedock.games.tetris.education.rules import GameState  # noqa: E402
from uedock import engine_registry  # noqa: E402
from uedock.games.tetris.engine import cold_clear_client  # noqa: E402
from tests import config_isolation as isolation  # noqa: E402
from tests.tetris.test_education_advisor import FakeEngine  # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

ENGINE_PLUGIN = '''
from pathlib import Path
from uedock.game_mode import EngineSpec
from uedock.plugin_api import Plugin

PLUGIN = Plugin(id="test_engine", name="試験用エンジン", engines={"tetris": (EngineSpec("試験AI", Path(r"{exe}")),)})
'''


class _FakeClient(FakeEngine):
    """ColdClearClientの代わり。どのエンジンで起動されたかを記録する。"""

    created: list = []

    def __init__(self, engine=None) -> None:
        super().__init__()
        _FakeClient.created.append(engine)


class TestAdvisorSetEngine(unittest.TestCase):
    def test_switch_closes_the_old_engine_and_relabels(self) -> None:
        old = FakeEngine()
        advisor = Advisor(engine_factory=lambda: old, odds_search=lambda *a, **k: None)
        advisor.preferred_id = AI_ID
        state = GameState.new(3)
        advisor.update(state, now=0.0)
        self.assertIs(advisor._engine, old)
        new = FakeEngine()
        advisor.set_engine(lambda: new, "試験AI")
        self.assertTrue(old.closed, "前のエンジンを終了していない")
        advisor.update(state, now=0.0)
        self.assertIs(advisor._engine, new, "今の局面を新しいエンジンで考え直していない")
        self.assertIn("試験AI", [c.label for c in advisor.candidates()])


class TestSimulatorEngineSwitch(unittest.TestCase):
    def setUp(self) -> None:
        isolation.reset()
        plugin_loader._current = None  # プラグインを読み直させる
        _FakeClient.created = []
        self.patcher = patch.object(cold_clear_client, "ColdClearClient", _FakeClient)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        isolation.reset()
        plugin_loader._current = None

    def _add_engine_plugin(self) -> Path:
        exe = Path(sys.executable)
        folder = plugin_loader.plugin_dir() / "test_engine"
        folder.mkdir(parents=True)
        (folder / "plugin.py").write_text(ENGINE_PLUGIN.replace("{exe}", str(exe)), encoding="utf-8")
        return exe

    def _window(self):
        from uedock.games.tetris.education.window import PracticeWindow

        window = PracticeWindow(seed=3)
        window.advisor.preferred_id = AI_ID
        window.advisor._turn_key = None  # AIを選んだ状態で考え直す(AIのエンジンを起動させる)
        window.refresh()
        return window

    def test_without_other_engine_only_kasugai(self) -> None:
        window = self._window()
        try:
            self.assertFalse(window.engine_btn.isEnabled(), "思考エンジンのプラグインが無いのに選べる")
            self.assertEqual(window.advisor.ai_label, "KasugAI")
            window.advisor.update(window.state)
            self.assertIsNone(_FakeClient.created[-1], "エンジンを指定せず、同梱の KasugAI で起動する")
        finally:
            window.close()

    def test_switch_to_plugin_engine_and_remember(self) -> None:
        exe = self._add_engine_plugin()
        window = self._window()
        try:
            self.assertTrue(window.engine_btn.isEnabled())
            self.assertEqual(window.engine_btn.on_text, "試験AI", "切り替えにプラグインのエンジンの名前を出す")
            window.engine_btn.click()
            self.assertEqual(window.advisor.ai_label, "試験AI")
            self.assertTrue(keybindings.load_view()["use_plugin_engine"], "選んだAIを覚えていない")
            window.advisor.update(window.state)
            self.assertEqual(_FakeClient.created[-1], (exe, None), "プラグインのエンジンで起動していない")
        finally:
            window.close()
        reopened = self._window()
        try:
            self.assertTrue(reopened.engine_btn.isChecked(), "次に開いたときプラグインのエンジンに戻っていない")
            self.assertEqual(reopened.advisor.ai_label, "試験AI")
        finally:
            reopened.close()

    def test_engine_selected_on_main_screen_is_used(self) -> None:
        # メイン画面の 1P でテトリスに選んだ(登録した)エンジンを、KasugAI と切り替える相手にする
        self._add_engine_plugin()
        exe = Path(sys.executable)
        engine_registry.Registry([engine_registry.EngineEntry("tetris", "登録AI", str(exe))]).save()
        selection = engine_registry.SideSelection()
        selection.set_engine("1p", "tetris", "登録AI")
        selection.save()
        keybindings.save_view(dict(keybindings.DEFAULT_VIEW, use_plugin_engine=True))
        window = self._window()
        try:
            self.assertEqual(window.engine_btn.on_text, "登録AI")
            self.assertEqual(window.advisor.ai_label, "登録AI")
        finally:
            window.close()

    def test_disabled_engine_plugin_falls_back_to_kasugai(self) -> None:
        self._add_engine_plugin()
        plugin_loader.save_disabled({"test_engine"})
        keybindings.save_view(dict(keybindings.DEFAULT_VIEW, use_plugin_engine=True))
        window = self._window()
        try:
            self.assertFalse(window.engine_btn.isEnabled())
            self.assertEqual(window.advisor.ai_label, "KasugAI", "無効にしたプラグインのエンジンを使っている")
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
