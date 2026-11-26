"""プラグインの読み込みと、ゲームモード・エンジンの登録(uedock/plugin_loader.py・uedock/game_mode.py)のテスト。"""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path

import tests.config_isolation as isolation
from uedock.game_mode import all_modes, builtin_modes
from uedock.plugin_api import FEATURE_DEBUG_LOG
from uedock.plugin_loader import PluginManager, config_path, load_disabled, plugin_dir, save_disabled


def write_plugin(name: str, body: str) -> Path:
    folder = plugin_dir() / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return folder


SWAP_PLUGIN = """
    from pathlib import Path

    from uedock.game_mode import EngineSpec, GameMode
    from uedock.plugin_api import Plugin

    PLUGIN = Plugin(
        id="swap",
        name="スワップ",
        features=frozenset({"debug_log"}),
        game_modes=(GameMode(id="swap", name="スワップ"),),
        engines={"tetris": (EngineSpec("別のAI", Path("other.exe")),)},
    )
"""


class TestPluginLoader(unittest.TestCase):
    def setUp(self) -> None:
        isolation.reset()

    def test_places_are_isolated(self) -> None:
        self.assertEqual(plugin_dir(), isolation.ROOT / "plugins")
        self.assertEqual(config_path(), isolation.ROOT / "config" / "plugins.json")

    def test_no_plugin_folder(self) -> None:
        manager = PluginManager.load()
        self.assertEqual((manager.plugins, manager.errors), ([], {}))

    def test_plugin_adds_game_mode_engine_and_feature(self) -> None:
        write_plugin("swap", SWAP_PLUGIN)
        manager = PluginManager.load()
        self.assertEqual(manager.errors, {})
        self.assertEqual([p.id for p in manager.enabled], ["swap"])
        self.assertTrue(manager.has_feature(FEATURE_DEBUG_LOG))
        self.assertFalse(manager.has_feature("obs_overlay"))
        self.assertEqual([e.name for e in manager.engines("tetris")], ["別のAI"])
        self.assertEqual(manager.engines("puyo"), [])
        # 標準のぷよ・テトリスに続いて、プラグインのゲームモードが並ぶ
        self.assertEqual(list(all_modes(manager)), ["puyo", "tetris", "swap"])

    def test_disabled_plugin_adds_nothing(self) -> None:
        write_plugin("swap", SWAP_PLUGIN)
        save_disabled({"swap"})
        self.assertEqual(load_disabled(), {"swap"})
        manager = PluginManager.load()
        self.assertEqual([p.id for p in manager.plugins], ["swap"], "無効でも一覧には出す")
        self.assertEqual(manager.enabled, [])
        self.assertFalse(manager.has_feature(FEATURE_DEBUG_LOG))
        self.assertEqual(manager.engines("tetris"), [])
        self.assertEqual(list(all_modes(manager)), ["puyo", "tetris"])

    def test_plugin_cannot_replace_builtin_mode(self) -> None:
        write_plugin("fake", """
            from uedock.game_mode import GameMode
            from uedock.plugin_api import Plugin

            PLUGIN = Plugin(id="fake", name="偽物", game_modes=(GameMode(id="puyo", name="偽のぷよ"),))
        """)
        self.assertEqual(all_modes(PluginManager.load())["puyo"].name, "ぷよ")

    def test_broken_plugin_is_skipped_with_reason(self) -> None:
        write_plugin("broken", "raise RuntimeError('壊れている')\n")
        write_plugin("not_plugin", "PLUGIN = 1\n")
        write_plugin("swap", SWAP_PLUGIN)
        manager = PluginManager.load()
        self.assertEqual([p.id for p in manager.plugins], ["swap"], "1つの不具合でほかのプラグインも読めなくなった")
        self.assertIn("壊れている", manager.errors["broken"])
        self.assertIn("TypeError", manager.errors["not_plugin"])

    def test_several_plugins_in_one_folder(self) -> None:
        write_plugin("pair", """
            from uedock.plugin_api import Plugin

            PLUGINS = [Plugin(id="a", name="A"), Plugin(id="b", name="B")]
        """)
        self.assertEqual([p.id for p in PluginManager.load().plugins], ["a", "b"])


    def test_changes_on_disk_after_loading(self) -> None:
        """読み込んだ後に足した・書き換えた・消したプラグインに気づく(再起動を勧めるため)。"""
        import os

        self.assertFalse(PluginManager().changed_on_disk(), "plugins/ から読み込んでいないのに変わったと言った")
        folder = write_plugin("swap", SWAP_PLUGIN)
        manager = PluginManager.load()
        self.assertFalse(manager.changed_on_disk())
        added = write_plugin("added", 'from uedock.plugin_api import Plugin\nPLUGIN = Plugin(id="added", name="足した")\n')
        self.assertTrue(manager.changed_on_disk(), "足したのに気づかない")
        manager = PluginManager.load()
        self.assertEqual(sorted(p.id for p in manager.plugins), ["added", "swap"])
        # 書き換えた(同じ大きさでも時刻で分かる)。プラグインのフォルダの中の別のファイルも見る
        later = os.stat(folder / "plugin.py").st_mtime + 10
        os.utime(folder / "plugin.py", (later, later))
        self.assertTrue(manager.changed_on_disk(), "書き換えたのに気づかない")
        manager = PluginManager.load()
        (folder / "helper.py").write_text("X = 1\n", encoding="utf-8")
        self.assertTrue(manager.changed_on_disk())
        manager = PluginManager.load()
        (added / "plugin.py").unlink()
        self.assertTrue(manager.changed_on_disk(), "消したのに気づかない")


class TestBuiltinModes(unittest.TestCase):
    def test_puyo_and_tetris_with_bundled_engines(self) -> None:
        modes = {m.id: m for m in builtin_modes()}
        self.assertEqual(list(modes), ["puyo", "tetris"])
        # 初期で搭載するのは YamaNEKO と KasugAI だけ
        self.assertEqual([e.name for e in modes["puyo"].builtin_engines], ["YamaNEKO"])
        self.assertEqual([e.name for e in modes["tetris"].builtin_engines], ["KasugAI"])
        self.assertEqual(modes["puyo"].default_engine(), "YamaNEKO")
        self.assertEqual(modes["tetris"].default_engine(), "KasugAI")


if __name__ == "__main__":
    unittest.main()
