"""docs/samples のサンプルのエンジンが、説明書の規格どおりに本体と話せることを、実物の通信コードで確かめる。"""

from __future__ import annotations

import json
import random
import sys
import unittest
from pathlib import Path

from uedock.games.puyo.engine.ama_client import AmaClient, NoValidPlacementError, PlayerState, parse_battle
from uedock.games.puyo.engine.puyo_field import PuyoField
from uedock.games.tetris.engine.board_state import BoardState
from uedock.games.tetris.engine.cold_clear_client import ColdClearClient

SAMPLES = Path(__file__).resolve().parent.parent / "docs" / "samples"


class TestTetrisSample(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ColdClearClient(engine=(Path(sys.executable), None),
                                      extra_args=["-u", str(SAMPLES / "tetris_engine_sample.py")])
        self.addCleanup(self.client.close)

    def test_plays_by_start(self) -> None:
        rng = random.Random(1)
        pieces = [p for _ in range(20) for p in rng.sample("IOTLJSZ", 7)]
        board, hold, lines = BoardState(), None, 0
        for i in range(60):
            current, queue = pieces[0], pieces[1:6]
            self.client.start_thinking(board, current, hold, queue)
            move = self.client.poll_suggestion(current)
            self.assertIsNotNone(move, f"{i}手目で手が返らない")
            self.assertTrue(board.is_placement_physically_valid(move.landing_cells), f"{i}手目が置けない: {move}")
            self.assertIn(move.piece, (current, hold or queue[0]))
            pieces.pop(0)
            if move.piece != current:
                if hold is None:
                    pieces.pop(0)
                hold = current
            for r, c in move.landing_cells:
                board.grid[r][c] = move.piece
            board, cleared = board.clear_lines()
            lines += cleared
        self.assertGreaterEqual(lines, 10)

    def test_play_and_new_piece_advance_the_position(self) -> None:
        board = BoardState()
        self.client.start_thinking(board, "I", None, ["O", "T", "L", "J", "S"])
        first = self.client.poll_suggestion("I")
        self.client.advance_thinking(first.placement, ["Z"])
        # HOLD を使ったかで次の操作ミノが変わる
        second = self.client.poll_suggestion("O" if not first.use_hold else "T")
        for r, c in first.landing_cells:
            board.grid[r][c] = first.piece
        self.assertTrue(board.clear_lines()[0].is_placement_physically_valid(second.landing_cells))

    def test_unknown_messages_are_ignored_and_hold_can_be_disallowed(self) -> None:
        self.client.send_battle(4, BoardState())
        self.client.start_thinking(BoardState(), "S", "I", ["Z"], disallow_hold=True)
        self.assertEqual(self.client.poll_suggestion("S").piece, "S")


class TestPuyoSample(unittest.TestCase):
    def setUp(self) -> None:
        self.client = AmaClient(Path(sys.executable), custom_args=["-u", str(SAMPLES / "puyo_engine_sample.py")])
        self.addCleanup(self.client.close)

    def test_solo(self) -> None:
        field = PuyoField.from_rows(["......"] * 12 + ["RRR.GG"])
        move = self.client.solo(field, ["RB", "GY", "GG"])
        self.assertEqual(move.label, "build")
        self.assertEqual(field.grid[move.axis_cell[0]][move.axis_cell[1]], ".")
        self.assertGreaterEqual(move.self_analysis.immediate.count, 1)
        self.assertEqual(move.self_analysis.immediate.score, 40)

    def test_battle(self) -> None:
        me = PlayerState(PuyoField(), ["RG", "BY", "RR"])
        move = self.client.battle(me, PlayerState(PuyoField(), ["RG", "BY"]), target_point=70)
        self.assertIn(move.label, ("build", "main", "harass", "accept", "all_clear", "prepare"))
        self.assertIsNotNone(move.enemy_analysis)

    def test_full_board_has_no_placement(self) -> None:
        field = PuyoField.from_rows(["######"] * 13)
        with self.assertRaises(NoValidPlacementError):
            self.client.suggest(field, ["RG"])
        # 対戦モードでも、置ける手が無いだけなら支援モードを止めない
        with self.assertRaises(NoValidPlacementError):
            self.client.battle(PlayerState(field, ["RG", "BY"]), PlayerState(PuyoField(), ["RG", "BY"]))

    def test_unknown_mode_returns_an_error(self) -> None:
        self.client._send({"mode": "future_mode"})
        self.assertIn("error", self.client._recv())


class TestBattleError(unittest.TestCase):
    def test_other_errors_are_not_no_placement(self) -> None:
        with self.assertRaises(RuntimeError) as caught:
            parse_battle({"error": "内部エラー"})
        self.assertNotIsInstance(caught.exception, NoValidPlacementError)


class TestSpecExamples(unittest.TestCase):
    def test_complete_json_examples_are_valid(self) -> None:
        """説明書の JSON の例(省略の … が無いもの)が、JSON として正しいこと。"""
        counts = []
        for name in ("ENGINE_SPEC.md", "ENGINE_SPEC.en.md"):
            text = (SAMPLES.parent / name).read_text(encoding="utf-8")
            examples = [block.split("```")[0] for block in text.split("```json\n")[1:]]
            counts.append(len(examples))
            for example in examples:
                if "…" not in example:
                    json.loads(example)
        self.assertGreaterEqual(counts[0], 6)
        self.assertEqual(counts[0], counts[1], "日本語版と英語版で例の数が違う")


if __name__ == "__main__":
    unittest.main()
