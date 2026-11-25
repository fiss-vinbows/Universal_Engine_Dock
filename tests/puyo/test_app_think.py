"""AssistWorker の思考まわり: 相手側の認識エラーが自分の提案を止めないこと。"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets  # noqa: E402

from uedock.games.puyo.worker import AssistWorker  # noqa: E402
from uedock.games.puyo.engine.ama_client import BattleMove  # noqa: E402
from uedock.games.puyo.engine.puyo_field import COLS, ROWS  # noqa: E402
from uedock.games.puyo.engine.tracker import PlayerTracker  # noqa: E402
from uedock.games.puyo.overlay.renderer import OverlayDrawData  # noqa: E402
from uedock.games.puyo.vision.cell_classifier import EMPTY  # noqa: E402


def _empty_cells() -> list[list[str]]:
    return [[EMPTY] * COLS for _ in range(ROWS)]


def _board_with(rows: list[str]) -> list[list[str]]:
    """下から並べた行(R/Y/G/B/P と .)の盤面(色名の13行)。"""
    names = {"R": "red", "Y": "yellow", "G": "green", "B": "blue", "P": "purple"}
    cells = _empty_cells()
    for i, row in enumerate(rows):
        for c, ch in enumerate(row):
            if ch != ".":
                cells[ROWS - 1 - i][c] = names[ch]
    return cells


def _tracker(current: tuple[str, str], n1: tuple[str, str], n2: tuple[str, str]) -> PlayerTracker:
    tr = PlayerTracker()
    tr.settled = _empty_cells()
    tr.current_pair, tr.next1, tr.next2 = current, n1, n2
    return tr


class BuildStatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        # calibration/client は _build_states では使わない
        self.worker = AssistWorker(calibration=None, client=None, debug_log_path=None)  # type: ignore[arg-type]

    def test_enemy_fifth_color_does_not_block_self(self) -> None:
        # 相手ネクストの黄が赤と誤認識され(この試合の4色は緑紫黄青)、
        # 5色目エラーで自分の思考が5手番スキップされた。相手側の失敗は相手なし(None)にする
        me = _tracker(("green", "purple"), ("yellow", "blue"), ("green", "yellow"))
        en = _tracker(("red", "purple"), ("green", "yellow"), ("purple", "green"))
        my_state, en_state, my_pairs, en_pairs = self.worker._build_states(me, en, me.settled, [me.current_pair, me.next1, me.next2])
        self.assertIsNone(en_state)
        self.assertEqual(len(my_pairs), 3)
        self.assertEqual(en_pairs, [])
        self.assertNotIn("red", self.worker.mapper.mapping)  # 誤認識の色を割り当てに残さない

    def test_enemy_ok_gives_battle_state(self) -> None:
        me = _tracker(("green", "purple"), ("yellow", "blue"), ("green", "yellow"))
        en = _tracker(("blue", "purple"), ("green", "yellow"), ("purple", "green"))
        _, en_state, _, en_pairs = self.worker._build_states(me, en, me.settled, [me.current_pair, me.next1, me.next2])
        self.assertIsNotNone(en_state)
        self.assertEqual(len(en_pairs), 3)

    def test_self_fifth_color_still_raises(self) -> None:
        me = _tracker(("green", "purple"), ("yellow", "blue"), ("red", "yellow"))
        en = _tracker(("blue", "purple"), ("green", "yellow"), ("purple", "green"))
        with self.assertRaises(ValueError):
            self.worker._build_states(me, en, me.settled, [me.current_pair, me.next1, me.next2])


class FormTextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.worker = AssistWorker(calibration=None, client=None, debug_log_path=None)  # type: ignore[arg-type]

    def _move(self, form: str | None) -> BattleMove:
        return BattleMove(0, "UP", 0, (12, 0), (11, 0), "build", None, None, 0, form=form)

    def test_aim_then_transition(self) -> None:
        self.assertEqual(self.worker._form_text(self._move("GTR")), "狙い: GTR")
        self.worker._last_form = "GTR"
        self.assertEqual(self.worker._form_text(self._move("GTR")), "狙い: GTR")
        self.assertEqual(self.worker._form_text(self._move("SGTR")), "移行: GTR → フキゲンGTR")
        self.assertEqual(self.worker._form_text(self._move(None)), "定型を離脱: GTR")


class InitialResetTest(unittest.TestCase):
    def test_false_first_move_is_cleared_from_the_display(self) -> None:
        """開始前の画面を初手と誤検出して取り消した時は、出していた提案を消す。"""
        worker = AssistWorker(calibration=None, client=None, debug_log_path=None)  # type: ignore[arg-type]
        shown: list = []
        worker.draw_data_ready.connect(shown.append)
        worker._last_data = OverlayDrawData(axis_cell=(12, 0), child_cell=(11, 0), axis_color="red", child_color="blue", label="build")
        worker._on_initial_reset()
        self.assertIsNone(worker._last_data)
        self.assertEqual(shown, [None])


class PrecomputeKeyTest(unittest.TestCase):
    def test_precomputed_requires_matching_pairs(self) -> None:
        # 先読みは「期待盤面」だけでなく「その時の組ぷよ」も一致した時だけ使う
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        worker = AssistWorker(calibration=None, client=None, debug_log_path=None)  # type: ignore[arg-type]
        expected = _empty_cells()
        move = BattleMove(0, "UP", 0, (12, 0), (11, 0), "build", None, None, 0)
        worker._precomputed = (expected, (("red", "green"), ("blue", "yellow")), move)
        me = _tracker(("red", "green"), ("blue", "yellow"), ("green", "green"))
        pre = worker._precomputed
        self.assertTrue(pre[0] == me.settled and pre[1] == (me.current_pair, me.next1))
        me = _tracker(("green", "red"), ("blue", "yellow"), ("green", "green"))  # 軸子が逆
        self.assertFalse(pre[1] == (me.current_pair, me.next1))


class FakeClient:
    """battle の応答を差し替えられる偽クライアント(呼び出し内容も記録する)。"""

    def __init__(self, moves: list[BattleMove]) -> None:
        self.moves = list(moves)
        self.calls: list[dict] = []

    def battle(self, my_state, en_state, **kwargs) -> BattleMove:
        self.calls.append({"my": my_state, "en": en_state, **kwargs})
        return self.moves.pop(0)

    def solo(self, field, pairs, **kwargs) -> BattleMove:
        self.calls.append({"solo": field, "pairs": list(pairs), **kwargs})
        return self.moves.pop(0)


def _move(label: str, axis=(12, 0), child=(11, 0), fire_reason=None) -> BattleMove:
    return BattleMove(0, "UP", 0, axis, child, label, None, None, 0, fire_reason=fire_reason)


class RethinkPolicyTest(unittest.TestCase):
    """手番途中の再計算: 表示中のラベルと攻撃の大きさで許可を決める。"""

    def setUp(self) -> None:
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _worker(self, moves: list[BattleMove]) -> tuple[AssistWorker, FakeClient, list]:
        client = FakeClient(moves)
        worker = AssistWorker(calibration=None, client=client, debug_log_path=None)  # type: ignore[arg-type]
        worker.trackers["self"] = _tracker(("green", "purple"), ("yellow", "blue"), ("green", "yellow"))
        worker.trackers["enemy"] = _tracker(("blue", "purple"), ("green", "yellow"), ("purple", "green"))
        emitted: list = []
        worker.draw_data_ready.connect(lambda d: emitted.append(d))
        return worker, client, emitted

    def test_rethink_same_placement_updates_label_and_note(self) -> None:
        # 再計算の結果が同じ置き場所でも、判断の意味(ラベル・理由)は最新に更新する
        worker, client, emitted = self._worker([_move("main", (12, 2), (12, 3), fire_reason="counter")])
        worker._last_data = OverlayDrawData(axis_cell=(12, 2), child_cell=(12, 3), axis_color="green", child_color="purple", label="harass")
        self.assertTrue(worker._think(1.0, _frame(), note="相手の消去開始を検出: 提案を更新"))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(emitted[-1].label, "main")
        self.assertEqual(emitted[-1].axis_cell, (12, 2))
        self.assertIn("相手の消去開始を検出", emitted[-1].note)

    def test_enemy_firing_shown_until_fresh_think(self) -> None:
        # 相手の連鎖中は撃っている連鎖(点滅から再現)をパネルに出し、連鎖が終わった後に依頼した思考が届くまで残す
        from uedock.games.puyo.engine.chain_sim import ChainResult

        worker, _, emitted = self._worker([])
        me, en = worker.trackers["self"], worker.trackers["enemy"]
        worker._last_data = OverlayDrawData(axis_cell=(12, 2), child_cell=(12, 3), axis_color="green", child_color="purple", label="build")
        en.chain_expected = ChainResult(steps=6, score=18530, cleared=44, final=_empty_cells())
        en.chain_active = True
        worker._on_enemy_chain(en, "chain_start")
        self.assertEqual(emitted[-1].enemy_firing, (6, 18530, True))
        worker._emit_move(me, _move("build"))  # 連鎖中に届いた思考でも撃っている連鎖のまま
        self.assertEqual(emitted[-1].enemy_firing, (6, 18530, True))
        en.chain_active, en.chain_steps, en.chain_score = False, 6, 18530
        worker._think_submitted = 1.0
        worker._on_enemy_chain(en, "chain_end")
        self.assertEqual(emitted[-1].enemy_firing, (6, 18530, False))
        worker._emit_move(me, _move("build"))  # 連鎖中に依頼した思考: 撃った連鎖を残す
        self.assertEqual(emitted[-1].enemy_firing, (6, 18530, False))
        worker._think_submitted = worker._enemy_fire_end + 0.1  # 連鎖の後に依頼した思考: 解析に戻す
        worker._emit_move(me, _move("build"))
        self.assertIsNone(emitted[-1].enemy_firing)

    def test_rethink_keeps_build_display_when_still_build(self) -> None:
        # 構築中に再計算して構築のままなら、乱数で変わっただけの手を出さず表示を維持する
        worker, client, emitted = self._worker([_move("build", (12, 4), (12, 5))])
        worker._last_data = OverlayDrawData(axis_cell=(12, 2), child_cell=(12, 3), axis_color="green", child_color="purple", label="build")
        worker._think(1.0, _frame(), note="相手の連鎖が終了: 提案を更新")
        self.assertEqual(emitted[-1].axis_cell, (12, 2))
        self.assertEqual(emitted[-1].label, "build")

    def test_rethink_kept_display_requests_precompute_again(self) -> None:
        # 相手の消去開始などで先読みを捨てた後、再計算の結果が構築のまま(表示を維持)でも、表示中の手で次の手を
        # 先読みし直す
        worker, client, emitted = self._worker([_move("build", (12, 4), (12, 5)), _move("build", (12, 0), (11, 0))])
        worker._last_data = OverlayDrawData(axis_cell=(12, 2), child_cell=(12, 3), axis_color="green", child_color="purple", label="build")
        shown = _move("build", (12, 2), (12, 3))
        shown.field_after = ["......"] * 13
        worker._last_move = shown
        worker._precomputed = None
        worker._think(1.0, _frame(), note="相手の消去開始を検出: 提案を更新")
        self.assertEqual(len(client.calls), 2)  # 再計算 + 先読み
        self.assertIsNotNone(worker._precomputed)
        self.assertIsNotNone(worker._precomputed_attack)


    def test_rethink_does_not_use_stale_precompute(self) -> None:
        # 再計算(note あり)では、相手の状態が古い先読みを一度表示する経路を使わない
        worker, client, emitted = self._worker([_move("main", (12, 4), (12, 5), fire_reason="counter")])
        me = worker.trackers["self"]
        worker._precomputed = (me.settled, (me.current_pair, me.next1), _move("build", (12, 0), (12, 1)))
        worker._think(1.0, _frame(), note="相手の消去開始を検出: 提案を更新")
        self.assertEqual([d.axis_cell for d in emitted], [(12, 4)])

    def test_new_turn_does_not_keep_previous_turn_display(self) -> None:
        # 手番の開始(new_turn)と再計算の理由が重なっても、前の手番の表示を「構築のまま維持」しない
        worker, client, emitted = self._worker([_move("build", (12, 4), (12, 5))])
        worker._last_data = OverlayDrawData(axis_cell=(12, 2), child_cell=(12, 3), axis_color="green", child_color="purple", label="build")
        worker._think(1.0, _frame(), note="相手の消去開始を検出: 提案を更新", new_turn=True)
        self.assertEqual(emitted[-1].axis_cell, (12, 4))

    def test_incoming_estimate_is_carried_to_next_think(self) -> None:
        # 相手の連鎖中にブリッジが返した着弾見込みを保持し、次の思考では予告(アイコン数)より大きい方を渡す
        big = _move("accept")
        big.incoming, big.incoming_source = 50, "live"
        worker, client, _ = self._worker([big, _move("build")])
        worker.trackers["self"].pending_garbage = 2
        worker._think(1.0, _frame())
        self.assertEqual(worker._incoming_carry, 50)
        worker._think(2.0, _frame())
        self.assertEqual(client.calls[1]["my"].attack, 50)

    def test_carry_kept_while_enemy_chain_continues(self) -> None:
        # 相手の連鎖中に予告の表示が0になっても(自分の相殺)、着弾見込みは解除しない
        worker, _, _ = self._worker([])
        me, en = worker.trackers["self"], worker.trackers["enemy"]
        worker._incoming_carry = 48
        me.pending_garbage = 0
        en.chain_active = True
        worker._release_carry_if_pending_cleared(1.0, me)
        self.assertEqual(worker._incoming_carry, 48)
        # 相手の連鎖が終わって予告も無ければ解除する
        en.chain_active = False
        worker._release_carry_if_pending_cleared(2.0, me)
        self.assertEqual(worker._incoming_carry, 0)

    def test_incoming_estimate_is_revised_downward(self) -> None:
        # 見積もりは max で保持せず最新値で置き換える(148→60 の下方訂正が届く。分析文書第3弾 P0-3)
        first, second = _move("accept"), _move("accept")
        first.incoming, first.incoming_source = 148, "live"
        second.incoming, second.incoming_source = 60, "live"
        worker, client, _ = self._worker([first, second, _move("build")])
        worker._think(1.0, _frame())
        self.assertEqual(worker._incoming_carry, 148)
        worker._think(2.0, _frame())
        self.assertEqual(worker._incoming_carry, 60)
        worker._think(3.0, _frame())
        self.assertEqual(client.calls[2]["my"].attack, 60)

    def test_no_valid_placement_clears_display_and_continues(self) -> None:
        # ブリッジの「置ける手が無い」は例外で止めず、提案を消して観測を続ける
        from uedock.games.puyo.engine.ama_client import NoValidPlacementError

        class Raising(FakeClient):
            def battle(self, my_state, en_state, **kwargs):
                raise NoValidPlacementError("ama ブリッジがエラーを返しました: no valid placement")

        client = Raising([])
        worker = AssistWorker(calibration=None, client=client, debug_log_path=None)  # type: ignore[arg-type]
        worker.trackers["self"] = _tracker(("green", "purple"), ("yellow", "blue"), ("green", "yellow"))
        worker.trackers["enemy"] = _tracker(("blue", "purple"), ("green", "yellow"), ("purple", "green"))
        emitted: list = []
        worker.draw_data_ready.connect(lambda d: emitted.append(d))
        worker._last_data = OverlayDrawData(axis_cell=(12, 2), child_cell=(12, 3), axis_color="green", child_color="purple", label="build")
        self.assertTrue(worker._think(1.0, _frame()))
        self.assertIsNone(worker._last_data)
        self.assertEqual(emitted[-1], None)
        self.assertFalse(worker._think_pending)

    def test_spawn_promotes_next_dots_or_hides_stale_dots(self) -> None:
        # 手番の切り替わり: 前の手番のドット(置いたツモの色)を出したままにしない。
        # 2手先の提案があり新しい組ぷよの色と一致すればそれを先に出し、無ければドットを消す
        worker, _, emitted = self._worker([])
        me = worker.trackers["self"]
        me.current_pair = ("yellow", "blue")  # (子, 軸)
        worker._last_data = OverlayDrawData(
            axis_cell=(12, 2), child_cell=(12, 3), axis_color="purple", child_color="green", label="build",
            next_axis_cell=(12, 0), next_child_cell=(11, 0), next_axis_color="blue", next_child_color="yellow",
        )
        worker._on_self_spawn(me)
        d = emitted[-1]
        self.assertFalse(d.waiting)
        self.assertEqual((d.axis_cell, d.child_cell), ((12, 0), (11, 0)))
        self.assertEqual((d.child_color, d.axis_color), ("yellow", "blue"))
        self.assertIsNone(d.next_axis_cell)
        # 2手先が無い(または色が合わない)時はドットを隠す
        me.current_pair = ("red", "red")
        worker._on_self_spawn(me)
        self.assertTrue(emitted[-1].waiting)

    def test_self_chain_start_hides_stale_dots_but_keeps_next(self) -> None:
        # 自分の発火(chain_start)を検出したら、置き終わった組ぷよのドットを連鎖の間出したままにしない
        # 2手先の提案は残し、次の spawn で色が合えば差し替えられる
        worker, _, emitted = self._worker([])
        me = worker.trackers["self"]
        worker._last_data = OverlayDrawData(
            axis_cell=(12, 2), child_cell=(12, 3), axis_color="purple", child_color="green", label="build",
            next_axis_cell=(12, 0), next_child_cell=(11, 0), next_axis_color="blue", next_child_color="yellow",
        )
        worker._on_self_chain_start()
        d = emitted[-1]
        self.assertTrue(d.waiting)
        self.assertEqual(d.next_axis_cell, (12, 0))
        # 連鎖後の spawn: 2手先の色が新しい組ぷよと一致すれば提案として出る
        me.current_pair = ("yellow", "blue")
        worker._on_self_spawn(me)
        d = emitted[-1]
        self.assertFalse(d.waiting)
        self.assertEqual((d.axis_cell, d.child_cell), ((12, 0), (11, 0)))
        # 提案が無い時は何もしない
        worker._last_data = None
        n = len(emitted)
        worker._on_self_chain_start()
        self.assertEqual(len(emitted), n)

    def test_self_chain_hides_building_chain_info(self) -> None:
        # 自分の連鎖の消化中は、組んでいる連鎖の情報をパネルに出さない。連鎖が終われば戻す
        from uedock.games.puyo.engine.chain_sim import simulate_chain

        worker, _, emitted = self._worker([])
        me = worker.trackers["self"]
        worker._last_data = OverlayDrawData(axis_cell=(12, 2), child_cell=(12, 3), axis_color="purple", child_color="green", label="build")
        me.chain_expected = simulate_chain(_board_with(["RRRR.."]))
        worker._on_self_chain_start()
        self.assertTrue(emitted[-1].chaining)
        # 撃っている連鎖(点滅から再現した値)を自分の欄に出す
        self.assertEqual(emitted[-1].self_firing, (1, 40, True))
        me.chain_steps, me.chain_score = 1, 40
        worker._on_self_chain_end(me)
        self.assertFalse(emitted[-1].chaining)
        self.assertEqual(emitted[-1].self_firing, (1, 40, False))
        self.assertEqual(emitted[-1].note, "")  # 再現どおりなら訂正のメモは出さない
        # 連鎖数が分からない終わり方でも戻す
        me.chain_expected = None
        worker._on_self_chain_start()
        me.chain_steps = 0
        worker._on_self_chain_end(me)
        self.assertFalse(emitted[-1].chaining)

    def test_self_firing_corrected_when_replay_was_wrong(self) -> None:
        # 再現した連鎖より多い段が消えたら、その場で観測の段数に直し、終わりに訂正をメモに出す
        from uedock.games.puyo.engine.chain_sim import simulate_chain

        worker, _, emitted = self._worker([])
        me = worker.trackers["self"]
        worker._last_data = OverlayDrawData(axis_cell=(12, 2), child_cell=(12, 3), axis_color="purple", child_color="green", label="build")
        me.chain_expected = simulate_chain(_board_with(["RRRR.."]))  # 再現では1連鎖40点
        me.chain_active = True
        worker._on_self_chain_start()
        me.chain_steps = 1
        worker._on_self_chain_step(me)
        self.assertEqual(emitted[-1].self_firing, (1, 40, True))  # 再現どおりなら変えない
        me.chain_steps = 2
        worker._on_self_chain_step(me)
        self.assertEqual(emitted[-1].self_firing, (2, 0, True))  # 再現より多い段: 観測の段数(点数は不明)
        me.chain_active = False
        me.chain_steps, me.chain_score = 2, 360
        worker._on_self_chain_end(me)
        self.assertEqual(emitted[-1].self_firing, (2, 360, False))
        self.assertEqual(emitted[-1].note, "点数を訂正: 1連鎖 40点 → 2連鎖 360点")

    def test_new_game_reset_waits_longer_while_holding_all_clear(self) -> None:
        # 全消し直後の空盤面を「新しい試合」と誤認して全消しボーナスと色割当を失わない
        from uedock.games.puyo.engine.tracker import Snapshot

        def snaps(n: int) -> dict:
            cells = [[EMPTY] * COLS for _ in range(ROWS)]
            for i in range(n):
                cells[ROWS - 1 - i][0] = "red"
            s = Snapshot(t=0.0, cells=cells, next1=("red", "red"), next2=("red", "red"), pending_garbage=0, eyeless_cells=frozenset())
            return {"self": s, "enemy": s}

        worker, _, _ = self._worker([])
        worker._check_new_game(0.0, snaps(10))  # 積みがあった
        me = worker.trackers["self"]
        me.all_clear = True
        worker._check_new_game(1.0, snaps(0))
        worker._check_new_game(2.5, snaps(0))  # 1秒経過でもボーナス持ちならリセットしない
        self.assertIs(worker.trackers["self"], me)
        self.assertTrue(me.all_clear)
        worker._check_new_game(12.0, snaps(0))  # 長く空のままなら本当に新しい試合
        self.assertIsNot(worker.trackers["self"], me)
        # ボーナス無しなら従来どおり1秒
        worker._check_new_game(20.0, snaps(10))
        me = worker.trackers["self"]
        worker._check_new_game(21.0, snaps(0))
        worker._check_new_game(22.1, snaps(0))
        self.assertIsNot(worker.trackers["self"], me)

    def test_game_over_clears_all_clear_bonus_so_new_game_resets(self) -> None:
        # 全消しの直後に試合が終わると、ボーナス持ちの10秒待ちの間に次の試合が始まりリセットされなかった
        # 負け(12個以上が一気に消えて全消しにならない)でボーナスを消す
        from uedock.games.puyo.engine.tracker import Snapshot

        def snaps(n: int) -> dict:
            cells = [[EMPTY] * COLS for _ in range(ROWS)]
            for i in range(n):
                cells[ROWS - 1 - i][0] = "red"
            s = Snapshot(t=0.0, cells=cells, next1=("red", "red"), next2=("red", "red"), pending_garbage=0, eyeless_cells=frozenset())
            return {"self": s, "enemy": s}

        worker, _, _ = self._worker([])
        worker._check_new_game(0.0, snaps(10))
        me, en = worker.trackers["self"], worker.trackers["enemy"]
        me.all_clear = True
        en.chain_dropped = 5  # 小さい盤面の消去(点滅を見逃した全消しの可能性)ではボーナスを消さない
        worker._on_game_over(0.5, "enemy", en)
        self.assertTrue(me.all_clear)
        en.chain_dropped = 43  # 相手の負け
        worker._on_game_over(0.5, "enemy", en)
        self.assertFalse(me.all_clear)
        worker._check_new_game(1.0, snaps(0))
        worker._check_new_game(2.1, snaps(0))  # 通常の1秒でリセットされる
        self.assertIsNot(worker.trackers["self"], me)

    def test_opening_preview_before_start_and_adopted_at_first_turn(self) -> None:
        # 開始前(操作中の組ぷよ無し・盤面空・枠に第1手と第2手)に空盤面+2組で初手を計算して表示し、
        # 開始の出現で第1手・第2手が一致すれば先読みとして使う。計算は思考スレッドで行う
        first = _move("build", (12, 2), (11, 2))
        first.field_after = None
        worker, client, emitted = self._worker([first, _move("build", (12, 2), (11, 2))])
        me = worker.trackers["self"]
        me.current_pair, me.next1, me.next2 = None, ("green", "purple"), ("yellow", "blue")
        worker._opening_preview(0.5, me)
        self.assertEqual(len(client.calls), 1)
        self.assertIn("solo", client.calls[0])
        self.assertEqual(len(client.calls[0]["pairs"]), 2)
        self.assertIn("開始前", emitted[-1].note)
        self.assertEqual((emitted[-1].child_color, emitted[-1].axis_color), ("green", "purple"))
        # 同じ枠なら再計算しない
        worker._opening_preview(0.6, me)
        self.assertEqual(len(client.calls), 1)
        # 開始: 第1手が出現(枠はまだ入れ替わっていない時はネクネク未読)→ 出現で計算済みの初手を先読みとして置く
        me.current_pair, me.next1, me.next2 = ("green", "purple"), ("yellow", "blue"), None
        worker._on_self_spawn(me)
        self.assertIsNotNone(worker._precomputed)
        self.assertIs(worker._precomputed[2], first)
        # 開始後は開始前の計算をしない
        worker._opening_preview(2.1, me)
        self.assertEqual(len(client.calls), 1)

    def test_opening_preview_uses_first_pair_after_frame_swap(self) -> None:
        # 開始で枠が入れ替わった後(第1手は _prestart_first、枠は第2・3手)の解釈でも計算する
        # 枠がまだ入れ替わっていない解釈と両方計算する
        two = _move("build", (12, 0), (11, 0))
        three = _move("build", (12, 2), (11, 2))
        worker, client, emitted = self._worker([two, three])
        me = worker.trackers["self"]
        me.current_pair, me.next1, me.next2 = None, ("purple", "red"), ("purple", "purple")
        me._prestart_first = ("blue", "blue")
        worker._opening_preview(0.5, me)
        self.assertEqual([len(c["pairs"]) for c in client.calls], [2, 3])
        self.assertEqual((emitted[-1].child_color, emitted[-1].axis_color), ("blue", "blue"))  # 表示は入れ替わりの後の解釈
        # 入れ替わりが開始だった: 第1手 (青青) が出現 → 3組で計算した手を使う
        me.current_pair, me.next1, me.next2 = ("blue", "blue"), ("purple", "red"), ("purple", "purple")
        worker._on_self_spawn(me)
        self.assertEqual(worker._precomputed[1], (("blue", "blue"), ("purple", "red")))
        self.assertIs(worker._precomputed[2], three)


    def test_precompute_kept_when_board_taken_in_during_same_turn(self) -> None:
        # 同じ手番のうちに盤面の取り込みが進んだ(自分の連鎖中など)だけなら、届いた先読みを捨てずに残す
        worker, _, _ = self._worker([])
        me = worker.trackers["self"]
        pre_move = _move("build", (12, 0), (11, 0))
        pre_move.path = ["DROP"]
        expected = _empty_cells()
        ctx = {"key": (me.turn, me.current_pair, me.next1, me.next2, "古い盤面"), "move": pre_move, "expected": expected,
               "pairs": (me.next1, me.next2), "attack": worker._attack_snapshot(me)}
        worker._apply_precompute(ctx, pre_move, None)
        self.assertIsNotNone(worker._precomputed)
        self.assertEqual(worker._precomputed[1], (me.next1, me.next2))


    def _high_board(self, height: int) -> list[list[str]]:
        """3列目だけ height 段積んだ盤面(同じ色が4つ並ばない並び)。"""
        colors = "GPYB"
        return _board_with(["..{}...".format(colors[i % 4]) for i in range(height)])


    def test_field_edge_offset_follows_horizontal_shake(self) -> None:
        # 横揺れの確認用: 盤面(暗い)と右の枠(明るい)の境目の横位置を、校正した位置からのずれで返す
        import numpy as np

        from uedock.games.puyo.worker import field_edge_offset
        from uedock.games.puyo.capture.geometry import BoardGeometry

        geo = BoardGeometry(((100, 100), (580, 100), (580, 1060), (100, 1060)))  # 1マス80px
        frame = np.full((1200, 800, 3), 200, np.uint8)
        frame[100:1060, 100:580] = 30
        self.assertEqual(field_edge_offset(frame, geo), 0)
        self.assertEqual(field_edge_offset(np.roll(frame, 6, axis=1), geo), 6)
        self.assertEqual(field_edge_offset(np.roll(frame, -5, axis=1), geo), -5)
        # 壁際のぷよ(盤面の内側)の輪郭は境目と取り違えない
        frame[300:900, 540:570] = 255  # 壁から10px内側まで(実機のぷよの輪郭は壁から離れている)
        self.assertEqual(field_edge_offset(frame, geo), 0)

    def test_boards_match_ignores_garbage_color_misread(self) -> None:
        # 相手の攻撃の演出中は、おじゃまが色ぷよに読まれることがある。期待盤面がおじゃまのマスは、埋まっていれば何色でもよい
        from uedock.games.puyo.worker import boards_match

        expected = _empty_cells()
        expected[ROWS - 1][0] = "garbage"
        expected[ROWS - 1][1] = "red"
        observed = [row[:] for row in expected]
        observed[ROWS - 1][0] = "green"  # おじゃまを緑と読んだ
        self.assertTrue(boards_match(observed, expected))
        observed[ROWS - 1][1] = "blue"  # 色ぷよの色が違うのは別の盤面
        self.assertFalse(boards_match(observed, expected))
        observed = [row[:] for row in expected]
        observed[ROWS - 1][0] = EMPTY  # おじゃまのマスが空なのも別の盤面
        self.assertFalse(boards_match(observed, expected))


    def test_all_clear_from_banner(self) -> None:
        # 「全消し」の文字がはっきり見えたら全消しを持っているとする(連鎖の終わりの盤面を読み違えても、双方が全消しの構えが働く)
        worker, _, _ = self._worker([])
        worker._log = lambda text: None
        en = worker.trackers["enemy"]
        self.assertFalse(en.all_clear)
        worker._all_clear_from_banner(1.0, "enemy")
        self.assertTrue(en.all_clear)


    def test_instant_build_kept_when_thought_is_build_like(self) -> None:
        # 支援モード: 相手の連鎖中、先読みの構築の手を表示した後の思考が「伸ばす」(中身は構築)なら置き場所を変えず、
        # 判断の内容だけ更新する。返しの準備のように置き場所に意味がある時は差し替える
        from uedock.games.puyo.engine.ama_client import action_text

        for reason, steps, expected_cell, expected_action in (
            ("EXTEND", 0, (12, 1), "伸ばす"),
            ("COUNTER_PREP", 3, (12, 5), "返しの準備 @3"),
        ):
            pre_move = _move("build", (12, 1), (11, 1))
            fresh = _move("accept", (12, 5), (11, 5))
            fresh.reason, fresh.plan_steps, fresh.incoming = reason, steps, 40
            worker, client, emitted = self._worker([fresh])
            me = worker.trackers["self"]
            me.settling = False
            worker.trackers["enemy"].chain_active = True
            worker._precomputed = (me.settled, (me.current_pair, me.next1), pre_move)
            self.assertTrue(worker._think(1.0, _frame(), new_turn=True))
            self.assertEqual(emitted[-1].axis_cell, expected_cell, reason)
            self.assertEqual(emitted[-1].label, "accept", reason)
            self.assertIn(expected_action, emitted[-1].action, reason)
        # 受けて組み続ける時は受ける量を出す
        accept = _move("accept")
        accept.reason = "BUILD_CHAIN"
        self.assertEqual(action_text(accept, 5), "受ける(5個は受けて組み続ける)")

    def test_think_returns_false_when_input_missing(self) -> None:
        # 入力不足(ネクスト未認識)なら False を返し、_loop が再計算の理由を次の tick へ持ち越せる
        worker, client, _ = self._worker([])
        worker.trackers["self"].next1 = None
        self.assertFalse(worker._think(1.0, _frame()))
        self.assertEqual(client.calls, [])

    def test_rethink_switches_display_when_response_changes(self) -> None:
        # 潰しを表示中に相手の大きい攻撃(着弾見込みが増えた)で再計算し、本線発火に変わったら表示を切り替える
        worker, client, emitted = self._worker([_move("main", (12, 4), (12, 5), fire_reason="counter")])
        worker._last_data = OverlayDrawData(axis_cell=(12, 2), child_cell=(12, 3), axis_color="green", child_color="purple", label="harass")
        client.moves[0].incoming = 40  # 前回の思考時(0個)から見込みが増えた
        worker._think(1.0, _frame(), note="相手が本線を発火: 提案を更新")
        self.assertEqual(emitted[-1].label, "main")
        self.assertEqual(emitted[-1].axis_cell, (12, 4))
        self.assertIn("相手が本線を発火", emitted[-1].note)

    def test_flash_frame_board_is_passed_as_chain_field(self) -> None:
        # 点滅から復元した発火直前盤面があれば chain_field として渡す。無ければ渡さない(見積もりで代用しない)
        from uedock.games.puyo.engine.think_input import build_think_input

        worker, client, _ = self._worker([_move("build")])
        en = worker.trackers["enemy"]
        en.chain_active, en.chain_step_drops, en.chain_observed = True, [4], _empty_cells()
        before = _empty_cells()
        before[12][0] = before[12][1] = before[12][2] = before[11][0] = "green"
        en.chain_before, en.chain_before_source = before, "flash_frame"
        inp = build_think_input(worker.mapper, worker.trackers["self"], en, worker.trackers["self"].settled,
                                [worker.trackers["self"].current_pair, worker.trackers["self"].next1, worker.trackers["self"].next2])
        self.assertIsNotNone(inp.en_state.chain_field)
        self.assertEqual(inp.en_state.chain_field.to_rows()[-1][:3], "GGG")
        en.chain_before_source = "settled_without_trigger"
        inp = build_think_input(worker.mapper, worker.trackers["self"], en, worker.trackers["self"].settled,
                                [worker.trackers["self"].current_pair, worker.trackers["self"].next1, worker.trackers["self"].next2])
        self.assertIsNone(inp.en_state.chain_field)

    def test_haipuyo_history_is_passed(self) -> None:
        # 配ぷよ読み: 試合の最初から置いた組ぷよ(組ぷよと同じ文字)と置いた数、相手の置いた数を要求に入れる。
        # 切っている時・履歴が欠けている時は入れない
        from uedock.games.puyo.engine.think_input import build_think_input

        worker, client, _ = self._worker([_move("build")])
        me, en = worker.trackers["self"], worker.trackers["enemy"]
        me.turn, en.turn = 2, 3
        c1, c2 = me.current_pair
        me.pair_by_turn = {0: (c1, c2), 1: (c2, c1), 2: me.current_pair}
        src = [me.current_pair, me.next1, me.next2]
        inp = build_think_input(worker.mapper, me, en, me.settled, src, haipuyo=True)
        req = inp.my_state.to_json()
        self.assertEqual(req["placed"], 2)
        self.assertEqual(len(req["history"]), 2)
        # 同じ色は組ぷよと同じ文字になる
        self.assertEqual(req["history"][0], req["queue"][0])
        self.assertEqual(req["history"][1], req["queue"][0][::-1])
        self.assertEqual(inp.en_state.to_json()["placed"], 3)
        off = build_think_input(worker.mapper, me, en, me.settled, src)
        self.assertNotIn("history", off.my_state.to_json())
        self.assertNotIn("placed", off.en_state.to_json())
        # 先読みの要求(次の手番の手を次・次々の組で先に計算する): 今の組ぷよも置いた扱いにする
        pre = build_think_input(inp.mapper, me, en, me.settled, [me.next1, me.next2], haipuyo=True, ahead=True).my_state.to_json()
        self.assertEqual(pre["placed"], 3)
        self.assertEqual(pre["history"], req["history"] + [req["queue"][0]])
        self.assertEqual(pre["queue"][0], req["queue"][1])
        del me.pair_by_turn[1]
        gap = build_think_input(worker.mapper, me, en, me.settled, src, haipuyo=True)
        self.assertNotIn("history", gap.my_state.to_json())

    def test_peek_sends_placed(self) -> None:
        # 相手のツモを覗く: 配ぷよ読みなしでも自分と相手の置いた数を入れる(履歴は入れない)。先読みの要求は1つ先。
        # 相手の操作中の組ぷよが読めない時は、ネクストの位置がずれるので相手の置いた数を入れない
        from uedock.games.puyo.engine.think_input import build_think_input

        worker, client, _ = self._worker([_move("build")])
        me, en = worker.trackers["self"], worker.trackers["enemy"]
        me.turn, en.turn = 4, 5
        src = [me.current_pair, me.next1, me.next2]
        inp = build_think_input(worker.mapper, me, en, me.settled, src, peek=True)
        req = inp.my_state.to_json()
        self.assertEqual(req["placed"], 4)
        self.assertNotIn("history", req)
        self.assertEqual(inp.en_state.to_json()["placed"], 5)
        self.assertEqual(len(inp.en_state.to_json()["queue"]), 3)
        pre = build_think_input(inp.mapper, me, en, me.settled, [me.next1, me.next2], ahead=True, peek=True)
        self.assertEqual(pre.my_state.to_json()["placed"], 5)
        off = build_think_input(worker.mapper, me, en, me.settled, src)
        self.assertNotIn("placed", off.my_state.to_json())
        self.assertNotIn("placed", off.en_state.to_json())
        en.current_pair = None
        no_cur = build_think_input(worker.mapper, me, en, me.settled, src, peek=True)
        self.assertEqual(no_cur.my_state.to_json()["placed"], 4)
        self.assertNotIn("placed", no_cur.en_state.to_json())

    def test_tracker_records_pair_history(self) -> None:
        # 追跡は手番ごとの操作中の組ぷよを覚える(試合ごとに追跡を作り直すので試合の最初からの履歴)
        tr = PlayerTracker()
        tr.turn, tr.pair_by_turn = 2, {0: ("red", "red"), 1: ("blue", "green")}
        self.assertEqual(tr.pair_history(), [("red", "red"), ("blue", "green")])
        tr.pair_by_turn = {1: ("blue", "green")}
        self.assertIsNone(tr.pair_history())

    def test_enemy_chain_bonus_is_passed_to_engine(self) -> None:
        # 相手が全消しボーナス付きの連鎖中: chain_start で all_clear は落ちるが chain_bonus で渡す
        worker, client, _ = self._worker([_move("build")])
        en = worker.trackers["enemy"]
        en.chain_active, en.chain_dropped, en.chain_bonus, en.all_clear = True, 12, True, False
        worker._think(1.0, _frame())
        en_state = client.calls[0]["en"]
        self.assertTrue(en_state.chain_bonus)
        self.assertTrue(en_state.to_json()["chain_bonus"])


def _frame():
    import numpy as np

    return np.zeros((8, 8, 3), dtype=np.uint8)


class AsyncThinkTest(unittest.TestCase):
    """思考を思考スレッドで行い、観測ループを止めない。"""

    def _worker(self, delay: float):
        import time as _time

        from uedock.games.puyo.engine.background import ThinkWorker

        class SlowClient(FakeClient):
            def battle(self, my_state, en_state, **kwargs):
                _time.sleep(delay)
                return super().battle(my_state, en_state, **kwargs)

        client = SlowClient([_move("build", (12, 2), (11, 2))])
        worker = AssistWorker(calibration=None, client=client, debug_log_path=None)  # type: ignore[arg-type]
        worker.enable_precompute = False
        worker.trackers["self"] = _tracker(("green", "purple"), ("yellow", "blue"), ("green", "yellow"))
        worker.trackers["enemy"] = _tracker(("blue", "purple"), ("green", "yellow"), ("purple", "green"))
        worker._think_worker = ThinkWorker()
        worker._client_lock = worker._think_worker.lock
        emitted: list = []
        worker.draw_data_ready.connect(lambda d: emitted.append(d))
        self.addCleanup(worker._think_worker.stop)
        return worker, emitted

    def _wait_results(self, worker, timeout: float = 2.0) -> None:
        import time as _time

        end = _time.perf_counter() + timeout
        while _time.perf_counter() < end:
            worker._poll_think_results()
            if worker._think_worker._results.empty() and not worker._think_worker._order and not worker._think_worker.lock.locked():
                return
            _time.sleep(0.01)

    def test_think_returns_without_waiting_and_result_is_applied(self) -> None:
        import time as _time

        worker, emitted = self._worker(delay=0.3)
        start = _time.perf_counter()
        self.assertTrue(worker._think(1.0, _frame(), new_turn=True))
        self.assertLess(_time.perf_counter() - start, 0.1)  # 思考(0.3秒)を待たずに観測へ戻る
        self.assertEqual(emitted, [])
        _time.sleep(0.4)
        self._wait_results(worker)
        self.assertEqual(emitted[-1].axis_cell, (12, 2))

    def test_stale_result_is_discarded(self) -> None:
        # 思考中に手番が進んだら、古い組ぷよの結果は表示も操作もしない
        import time as _time

        worker, emitted = self._worker(delay=0.2)
        worker._think(1.0, _frame(), new_turn=True)
        worker.trackers["self"].turn += 1
        _time.sleep(0.3)
        self._wait_results(worker)
        self.assertEqual(emitted, [])


class AttackFrameEstimateTest(unittest.TestCase):
    def test_remaining_moves_shrink_with_elapsed_time(self) -> None:
        # 相手の連鎖が終わるまでに置ける手数は、経過時間とともに減る(固定の「3手」にしない)
        from uedock.games.puyo.engine.chain_sim import ChainResult
        from uedock.games.puyo.engine.think_input import CHAIN_STEP_SEC, MY_MOVE_SEC, estimate_attack_frames
        from uedock.games.puyo.engine.tracker import PlayerTracker

        en = PlayerTracker(chain_active=True, chain_started_t=10.0)
        en.chain_expected = ChainResult(steps=6, score=9100, cleared=28, final=_empty_cells())
        total = 6 * CHAIN_STEP_SEC
        self.assertEqual(estimate_attack_frames(en, 10.0), int(total // MY_MOVE_SEC))
        self.assertEqual(estimate_attack_frames(en, 10.0 + total - 1.5), 1)
        self.assertEqual(estimate_attack_frames(en, 10.0 + total + 1.0), 0)
        # 再現できていない連鎖・時刻が無い時は見積もらない(ブリッジの固定値)
        self.assertIsNone(estimate_attack_frames(en, None))
        en.chain_expected = None
        self.assertIsNone(estimate_attack_frames(en, 12.0))


class ChainEvidenceTest(unittest.TestCase):
    """型B の調査用の画像。"""

    def setUp(self) -> None:
        self.worker = AssistWorker(calibration=None, client=None, debug_log_path=None)  # type: ignore[arg-type]
        self.frame = object()
        self.saved: list[str] = []
        self.worker._chain_saver = type("S", (), {"save": lambda _s, path, _f, _p=None: self.saved.append(path.name) or True})()

    def test_nothing_kept_without_log(self) -> None:
        self.worker.trackers["self"].chain_active = True
        self.worker._remember_chain_frame(1.0, self.frame)
        self.worker._save_chain_evidence(1.0, "self", self.frame)
        self.assertEqual((len(self.worker._chain_frames), self.saved), (0, []))

    def test_frames_thinned_and_saved_up_to_limit(self) -> None:
        from uedock.games.puyo.worker import CHAIN_EVIDENCE_LIMIT

        self.worker.debug_log_path = Path("nul")
        self.worker._log = lambda _text: None
        self.worker.trackers["self"].chain_active = True
        for t in (1.0, 1.1, 1.6):
            self.worker._remember_chain_frame(t, self.frame)
        self.assertEqual([t for t, _ in self.worker._chain_frames], [1.0, 1.6])
        self.worker._save_chain_evidence(2.0, "self", self.frame)
        self.assertEqual(len(self.saved), 3)  # 覚えた2枚 + 捨てた瞬間
        self.worker._chain_evidence_saved = CHAIN_EVIDENCE_LIMIT
        self.worker._save_chain_evidence(3.0, "self", self.frame)
        self.assertEqual(len(self.saved), 3)
        self.worker.trackers["self"].chain_active = False
        self.worker._remember_chain_frame(4.0, self.frame)
        self.assertEqual(len(self.worker._chain_frames), 0)  # 連鎖が終われば手放す


if __name__ == "__main__":
    unittest.main()


class FifthColorDiagnosisTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_fifth_color_mid_game_logs_where_and_saves_frame(self) -> None:
        # 試合の途中で5色目が出て思考できなかった時、どこに未割当の色があるかを記録し画面を保存する(手番ごとに1回)
        worker = AssistWorker(calibration=None, client=None, debug_log_path=None)  # type: ignore[arg-type]
        logs: list[str] = []
        saved: list = []
        worker._log = logs.append  # type: ignore[method-assign]
        worker._saver.save = lambda path, frame, *a: saved.append(path)  # type: ignore[method-assign]
        me = _tracker(("green", "purple"), ("yellow", "blue"), ("green", "yellow"))
        for r in range(ROWS - 6, ROWS):
            me.settled[r] = ["green", "purple", "yellow", "blue", "green", "purple"]
        me.settled[ROWS - 7][0] = "red"  # 盤面に5色目
        worker.trackers["self"] = me
        worker.trackers["enemy"] = _tracker(("blue", "purple"), ("green", "yellow"), ("purple", "green"))
        for c in ("green", "purple", "yellow", "blue"):
            worker.mapper.slot(c)
        self.assertFalse(worker._think(1.0, _frame()))
        self.assertTrue(any("5色目の診断" in m and "未割当 ['red']" in m for m in logs), logs)
        self.assertEqual(len(saved), 1)
        self.assertIn("fifth_color_self_", str(saved[0]))
        worker._think(1.1, _frame())  # 同じ手番では二度は残さない
        self.assertEqual(len(saved), 1)


class TickStatsTest(unittest.TestCase):
    def test_reports_every_period(self) -> None:
        # 観測の間隔・画面取得の待ち・処理時間を一定時間ごとにまとめる
        from uedock.games.puyo.worker import TICK_STATS_SECONDS, TickStats

        st = TickStats()
        reports = []
        t = 0.0
        while t < TICK_STATS_SECONDS * 2 + 1:
            r = st.add(t, t, 0.002, 0.015)
            if r:
                reports.append(r)
            t += 0.03
        self.assertEqual(len(reports), 2)
        self.assertIn("間隔 中央 30ms", reports[0])
        self.assertIn("画面取得の待ち 中央 2ms", reports[0])
        self.assertIn("1回の処理(取得込み) 中央 15ms", reports[0])

