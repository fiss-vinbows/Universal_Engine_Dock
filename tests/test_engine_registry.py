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
