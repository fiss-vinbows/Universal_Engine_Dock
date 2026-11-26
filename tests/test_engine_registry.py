"""エンジンの登録・サイドごとの選択(uedock/engine_registry.py)と、支援モードの段取り(uedock/assist.py)のテスト。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import tests.config_isolation as isolation
from uedock.assist import build_requests
from uedock.engine_registry import EngineEntry, Registry, SideSelection, available_engines, bundled_engine
from uedock.game_mode import LAYOUT_SOLO, LAYOUT_VERSUS, NO_GAME, EngineSpec, GameMode
from uedock.plugin_loader import PluginManager

EXE = Path(sys.executable)  # 実在する実行ファイルの代わり


class FakeSession:
    def __init__(self, request) -> None:  # noqa: ANN001
        self.request = request

    def begin(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_running(self) -> bool:
        return True


def modes(puyo_ready: bool = True, tetris_ready: bool = True) -> dict[str, GameMode]:
    return {
        "puyo": GameMode("puyo", "ぷよ", builtin_engines=(EngineSpec("YamaNEKO", EXE),),
                         start_assist=FakeSession if puyo_ready else None),
        "tetris": GameMode("tetris", "テトリス", builtin_engines=(EngineSpec("KasugAI", EXE),),
                           start_assist=FakeSession if tetris_ready else None),
    }


class TestRegistry(unittest.TestCase):
    def setUp(self) -> None:
        isolation.reset()

    def test_empty_when_no_file(self) -> None:
        self.assertEqual(Registry.load().engines, [])

    def test_saved_and_loaded_per_game(self) -> None:
        registry = Registry([EngineEntry("puyo", "ama", "ama-bridge.exe"),
                             EngineEntry("tetris", "Cold Clear 2", "cc2.exe", ["--x"], "w.json")])
        registry.save()
        loaded = Registry.load()
        self.assertEqual(loaded.engines, registry.engines)
        self.assertEqual([e.name for e in loaded.for_game("tetris")], ["Cold Clear 2"])

    def test_broken_file_is_ignored(self) -> None:
        path = isolation.ROOT / "config" / "engines.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1, 2", encoding="utf-8")
        self.assertEqual(Registry.load().engines, [])
        path.write_text('{"engines": [{"name": "ゲーム種別なし", "exe": "a.exe"}, 3]}', encoding="utf-8")
        self.assertEqual(Registry.load().engines, [])

    def test_available_engines_builtin_first_and_only_for_the_game(self) -> None:
        registry = Registry([EngineEntry("puyo", "ama", "ama-bridge.exe"),
                             EngineEntry("puyo", "YamaNEKO", "偽物.exe"),
                             EngineEntry("tetris", "Cold Clear 2", "cc2.exe")])
        engines = available_engines(modes()["puyo"], registry, PluginManager())
        self.assertEqual(list(engines), ["YamaNEKO", "ama"])
        self.assertEqual(engines["YamaNEKO"].exe, EXE, "登録が同梱のエンジンを置き換えた")

    def test_bundled_engines_are_added_once_at_the_front(self) -> None:
        registry = Registry([EngineEntry("tetris", "Cold Clear 2", "cc2.exe"), EngineEntry("puyo", "ama", "ama-bridge.exe")])
        self.assertTrue(registry.add_bundled(modes().values()))
        self.assertEqual([e.name for e in registry.for_game("puyo")], ["YamaNEKO", "ama"])
        self.assertEqual([e.name for e in registry.for_game("tetris")], ["KasugAI", "Cold Clear 2"])
        self.assertEqual(sorted(registry.bundled), ["puyo/YamaNEKO", "tetris/KasugAI"])
        self.assertFalse(registry.add_bundled(modes().values()), "2回目も入れた")
        registry.save()
        loaded = Registry.load()
        self.assertEqual((loaded.engines, loaded.bundled), (registry.engines, registry.bundled))
        # 消した同梱のエンジンは入れ直さない
        loaded.engines = [e for e in loaded.engines if e.name != "YamaNEKO"]
        self.assertFalse(loaded.add_bundled(modes().values()))
        self.assertEqual([e.name for e in loaded.for_game("puyo")], ["ama"])

    def test_bundled_engine_keeps_a_registration_with_the_same_name(self) -> None:
        registry = Registry([EngineEntry("puyo", "YamaNEKO", "新しい版.exe")])
        self.assertTrue(registry.add_bundled(modes().values()))
        self.assertEqual([e.exe for e in registry.for_game("puyo")], ["新しい版.exe"])

    def test_paths_inside_the_app_folder_are_relative(self) -> None:
        from uedock.engine_registry import portable_path, resolve_path
        from uedock.paths import app_root

        inside = app_root() / "engines" / "kasugai" / "tetrisai-bot.exe"
        self.assertEqual(portable_path(inside), "engines/kasugai/tetrisai-bot.exe")
        self.assertEqual(resolve_path("engines/kasugai/tetrisai-bot.exe"), inside)
        self.assertEqual(resolve_path(portable_path(EXE)), EXE, "本体のフォルダの外は絶対パスのまま")
        spec = EngineSpec("KasugAI", inside, weights=app_root() / "engines" / "kasugai" / "run16.toml")
        entry = EngineEntry.from_spec("tetris", spec)
        self.assertEqual((entry.exe, entry.weights), ("engines/kasugai/tetrisai-bot.exe", "engines/kasugai/run16.toml"))
        self.assertEqual(entry.spec(), spec)

    def test_replaced_bundled_engine_is_used(self) -> None:
        registry = Registry()
        registry.add_bundled(modes().values())
        registry.find("puyo", "YamaNEKO").exe = "C:/新しい版/yamaneko-bridge.exe"
        engines = available_engines(modes()["puyo"], registry, PluginManager())
        self.assertEqual(list(engines), ["YamaNEKO"])
        self.assertEqual(engines["YamaNEKO"].exe, Path("C:/新しい版/yamaneko-bridge.exe"))
        # 消せば一覧から無くなる
        registry.engines = [e for e in registry.engines if e.name != "YamaNEKO"]
        self.assertEqual(available_engines(modes()["puyo"], registry, PluginManager()), {})

    def test_registered_engine_for_simulators(self) -> None:
        from uedock.engine_registry import registered_engine

        mode = modes()["tetris"]
        self.assertEqual(registered_engine(mode, "KasugAI").exe, EXE, "登録へ入れる前は同梱のもの")
        registry = Registry()
        registry.add_bundled(modes().values())
        registry.find("tetris", "KasugAI").exe = "C:/新しい版/tetrisai-bot.exe"
        registry.save()
        self.assertEqual(registered_engine(mode, "KasugAI").exe, Path("C:/新しい版/tetrisai-bot.exe"))
        registry.engines = []
        registry.save()
        self.assertIsNone(registered_engine(mode, "KasugAI"), "消したのに同梱のものを返した")

    def test_simulators_use_the_replaced_version(self) -> None:
        """シミュレーター(エンジンを指定しない ColdClearClient・AmaClient)も、登録で差し替えた版を使う。"""
        from uedock.games import puyo, tetris
        from uedock.games.puyo.engine.ama_client import YAMANEKO_BRIDGE_EXE, resolve_bridge_exe
        from uedock.games.tetris.engine import cold_clear_client

        self.assertEqual(resolve_bridge_exe(), YAMANEKO_BRIDGE_EXE)
        self.assertEqual(cold_clear_client.engine_exe(), cold_clear_client.KASUGAI_EXE)
        registry = Registry()
        registry.add_bundled([puyo.MODE, tetris.MODE])
        weights = isolation.ROOT / "new.toml"
        weights.write_text("", encoding="utf-8")
        registry.find("puyo", "YamaNEKO").exe = "C:/新しい版/yamaneko-bridge.exe"
        kasugai = registry.find("tetris", "KasugAI")
        kasugai.exe, kasugai.weights = "C:/新しい版/tetrisai-bot.exe", str(weights)
        registry.save()
        self.assertEqual(resolve_bridge_exe(), Path("C:/新しい版/yamaneko-bridge.exe"))
        self.assertEqual(cold_clear_client.engine_exe(), Path("C:/新しい版/tetrisai-bot.exe"))
        self.assertEqual(cold_clear_client.engine_weights(), weights)
        # 登録から消してあれば同梱のもの
        Registry([], registry.bundled).save()
        self.assertEqual(resolve_bridge_exe(), YAMANEKO_BRIDGE_EXE)
        self.assertEqual(cold_clear_client.engine_exe(), cold_clear_client.KASUGAI_EXE)

    def test_bundled_engine_falls_back_to_dev_build_only_when_missing(self) -> None:
        dev = Path("dev.exe")
        self.assertEqual(bundled_engine("x/a.exe", dev), dev)
        bundled = isolation.ROOT / "engines" / "x" / "a.exe"
        bundled.parent.mkdir(parents=True)
        bundled.write_bytes(b"")
        try:
            self.assertEqual(bundled_engine("x/a.exe", dev), bundled)
        finally:
            bundled.unlink()


class TestSideSelection(unittest.TestCase):
    def setUp(self) -> None:
        isolation.reset()

    def test_defaults(self) -> None:
        selection = SideSelection.load()
        self.assertEqual(selection.layout, LAYOUT_VERSUS)
        self.assertEqual([selection.game(s) for s in ("1p", "2p", "solo")], ["puyo", NO_GAME, "puyo"])
        self.assertEqual(selection.engine_name("1p", modes()["tetris"]), "KasugAI")

    def test_engine_is_remembered_per_game(self) -> None:
        selection = SideSelection()
        selection.set_game("1p", "tetris")
        selection.set_engine("1p", "tetris", "Cold Clear 2")
        selection.set_engine("1p", "puyo", "ama")
        selection.layout = LAYOUT_SOLO
        selection.save()
        loaded = SideSelection.load()
        self.assertEqual(loaded.layout, LAYOUT_SOLO)
        self.assertEqual(loaded.game("1p"), "tetris")
        self.assertEqual(loaded.engine_name("1p", modes()["tetris"]), "Cold Clear 2")
        # ぷよに切り替えて戻しても、ゲーム種別ごとの選択が残る
        self.assertEqual(loaded.engine_name("1p", modes()["puyo"]), "ama")

    def test_resolve(self) -> None:
        registry = Registry([EngineEntry("tetris", "Cold Clear 2", str(EXE))])
        selection = SideSelection()
        selection.set_game("1p", "tetris")
        selection.set_engine("1p", "tetris", "Cold Clear 2")
        mode, engine = selection.resolve("1p", modes(), registry, PluginManager())
        self.assertEqual((mode.id, engine.name), ("tetris", "Cold Clear 2"))
        # 登録を消したら同梱の既定に戻る
        mode, engine = selection.resolve("1p", modes(), Registry(), PluginManager())
        self.assertEqual((mode.id, engine.name), ("tetris", "KasugAI"))
        # 同梱の既定も登録から消されていれば、一覧の先頭
        only_cc2 = Registry([EngineEntry("tetris", "Cold Clear 2", str(EXE))], ["tetris/KasugAI"])
        selection.set_engine("1p", "tetris", "無いエンジン")
        mode, engine = selection.resolve("1p", modes(), only_cc2, PluginManager())
        self.assertEqual(engine.name, "Cold Clear 2")
        self.assertEqual(selection.resolve("2p", modes(), registry, PluginManager()), (None, None))
        # プラグインを無効にしてゲームモードが無くなった時
        selection.set_game("solo", "swap")
        self.assertEqual(selection.resolve("solo", modes(), registry, PluginManager()), (None, None))


class TestBuildRequests(unittest.TestCase):
    def setUp(self) -> None:
        isolation.reset()

    def build(self, selection: SideSelection, m: dict[str, GameMode] | None = None):  # noqa: ANN201
        return build_requests(selection, modes() if m is None else m, Registry(), PluginManager(), None)

    def test_same_game_on_both_sides_is_one_request(self) -> None:
        selection = SideSelection(games={"1p": "puyo", "2p": "puyo"})
        requests, error = self.build(selection)
        self.assertIsNone(error)
        self.assertEqual(len(requests), 1)
        mode, request = requests[0]
        self.assertEqual(mode.id, "puyo")
        self.assertEqual([s.side for s in request.sides], ["1p", "2p"])
        self.assertEqual(request.opponents, {"1p": "puyo", "2p": "puyo"})

    def test_mixed_games_are_split_per_mode(self) -> None:
        selection = SideSelection(games={"1p": "puyo", "2p": "tetris"})
        requests, error = self.build(selection)
        self.assertIsNone(error)
        self.assertEqual([(m.id, [s.side for s in r.sides]) for m, r in requests], [("puyo", ["1p"]), ("tetris", ["2p"])])
        # 相手が別種だと分かるように、相手のゲーム種別を渡す(v0.1 では凝視しない)
        self.assertEqual(requests[0][1].opponents, {"1p": "tetris"})
        self.assertEqual(requests[1][1].opponents, {"2p": "puyo"})
        self.assertEqual(requests[0][1].sides[0].engine.name, "YamaNEKO")
        self.assertEqual(requests[1][1].sides[0].engine.name, "KasugAI")

    def test_only_sides_of_the_layout(self) -> None:
        selection = SideSelection(layout=LAYOUT_SOLO, games={"1p": "puyo", "2p": "puyo", "solo": "tetris"})
        requests, error = self.build(selection)
        self.assertIsNone(error)
        self.assertEqual([(m.id, [s.side for s in r.sides]) for m, r in requests], [("tetris", ["solo"])])
        self.assertEqual(requests[0][1].layout, LAYOUT_SOLO)
        self.assertEqual(requests[0][1].opponents, {})

    def test_reasons_when_it_cannot_start(self) -> None:
        _, error = self.build(SideSelection(games={"1p": NO_GAME, "2p": NO_GAME}))
        self.assertIn("支援するサイドがありません", error)
        _, error = self.build(SideSelection(games={"1p": "puyo"}), modes(puyo_ready=False))
        self.assertIn("ぷよの支援モードは、まだ用意されていません", error)
        missing = {"puyo": GameMode("puyo", "ぷよ", builtin_engines=(EngineSpec("YamaNEKO", Path("無い.exe")),),
                                    start_assist=FakeSession)}
        _, error = self.build(SideSelection(games={"1p": "puyo"}), missing)
        self.assertIn("実行ファイルが見つかりません", error)
        no_engine = {"puyo": GameMode("puyo", "ぷよ", start_assist=FakeSession)}
        _, error = self.build(SideSelection(games={"1p": "puyo"}), no_engine)
        self.assertIn("思考エンジンが選ばれていません", error)


if __name__ == "__main__":
    unittest.main()
