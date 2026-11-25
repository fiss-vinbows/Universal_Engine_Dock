"""シミュレーションモードの複数候補の提示: 応答の変換・根拠の文・候補を選んだ時の盤面のドット。"""

from __future__ import annotations

import os
import sys
import json
import subprocess
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets  # noqa: E402

from uedock.games.puyo.engine.ama_client import AmaClient, CandidateList, NoValidPlacementError, parse_candidates  # noqa: E402
from uedock.games.puyo.engine.puyo_field import PuyoField  # noqa: E402
from uedock.games.puyo.simulator import candidate_text as text  # noqa: E402


def _raw(**over) -> dict:
    d = {
        "x": 2, "r": "UP", "axis": [2, 3], "child": [2, 4], "primary": False, "grow": False, "threat_best": True, "fire_best": False,
        "fire_chain": 0, "fire_score": 0, "value": 28000, "gap": 500, "gap_se": 900, "threat_hits": 7, "threat_average": 1284,
        "threat_best_chain": 5, "threat_best_score": 4840, "threat_best_pair": "BG", "threat_next": {"x": 3, "r": "DOWN"},
        "next": {"x": 0, "r": "DOWN"}, "next_share": 50,
    }
    d.update(over)
    return d


class ParseAndTextTest(unittest.TestCase):
    def _list(self, **over) -> CandidateList:
        return parse_candidates({"list": [_raw(**over)], "reads": 12, "real_future": False, "ms": 2000})

    def test_gtr_text(self) -> None:
        # 先折りGTRの手は、見出しと根拠に形の名前と役割を出し、次の手は形に合う置き方を使う
        result = self._list(gtr=True, gtr_form="緑のL字", gtr_roles=["折り返しの上のL字", "連鎖尾"], gtr_next={"x": 1, "r": "DOWN"})
        c = result.candidates[0]
        self.assertIn("先折りGTR", text.aim_text(c))
        self.assertEqual(text.follow_up_move(c), (1, "DOWN"))
        line = text.reason_lines(c, result, "GY")[0]
        self.assertIn("緑のL字", line)
        self.assertIn("折り返しの上のL字・連鎖尾", line)
        self.assertIn("2列目 縦置き", line)
        plain = self._list().candidates[0]
        self.assertFalse(plain.gtr)
        self.assertNotIn("先折りGTR", text.aim_text(plain))

    def test_parse(self) -> None:
        result = self._list()
        c = result.candidates[0]
        self.assertEqual((c.x, c.rotation), (2, "UP"))
        self.assertEqual(c.threat_next, (3, "DOWN"))
        self.assertEqual(result.reads, 12)
        self.assertIsNone(self._list(threat_next=None).candidates[0].threat_next)
        with self.assertRaises(NoValidPlacementError):
            parse_candidates({"error": "no valid placement"})

    def test_placement_text_uses_players_words(self) -> None:
        # 軸が緑・子が黄の組
        self.assertEqual(text.placement_text(2, "UP", "GY"), "3列目 縦置き(緑下・黄上)")
        self.assertEqual(text.placement_text(2, "DOWN", "GY"), "3列目 縦置き(黄下・緑上)")
        self.assertEqual(text.placement_text(4, "RIGHT", "GY"), "5列目緑・6列目黄")
        self.assertEqual(text.placement_text(5, "LEFT", "GY"), "5列目黄・6列目緑")

    def test_reason_lines(self) -> None:
        result = self._list()
        lines = text.reason_lines(result.candidates[0], result, "GR")
        joined = "".join(lines)
        self.assertIn("差は誤差の範囲", joined)  # 差 500 が誤差 900 の2倍以内
        self.assertIn("4列目 縦置き(赤下・緑上)", joined)
        self.assertIn("7/16 で催促を打てる", joined)
        self.assertIn("青緑で 5連鎖 4,840点", joined)
        result = self._list(gap=3000, gap_se=900, threat_hits=0, fire_chain=3, fire_score=2000)
        joined = "".join(text.reason_lines(result.candidates[0], result, "GR"))
        self.assertIn("3,000点低い", joined)
        self.assertIn("この手で 3連鎖 2,000点", joined)
        self.assertIn("すぐに打てる催促はまだ無い", joined)


class BridgeEncodingTest(unittest.TestCase):
    def test_reads_utf8(self) -> None:
        # 日本語の入った応答を読めること(既定の cp932 で読むと、応答の途中で読み取りが止まり「プロセスが終了」と誤って出る)
        with tempfile.TemporaryDirectory() as d:
            answer = Path(d) / "answer.json"
            answer.write_text(json.dumps({"list": [_raw(gtr=True, gtr_form="緑のL字")]}, ensure_ascii=False), encoding="utf-8")
            script = Path(d) / "fake_bridge.py"
            script.write_text(
                "import pathlib, sys\n"
                "sys.stdout.reconfigure(encoding='utf-8')\n"
                "print(chr(123) + chr(34) + 'ready' + chr(34) + ':true' + chr(125), flush=True)\n"
                "for line in sys.stdin:\n"
                f"    print(pathlib.Path({str(answer)!r}).read_text(encoding='utf-8'), flush=True)\n",
                encoding="utf-8",
            )
            real = subprocess.Popen
            with mock.patch("uedock.games.puyo.engine.ama_client.resolve_bridge_exe", return_value=script), mock.patch(
                "uedock.games.puyo.engine.ama_client.subprocess.Popen", side_effect=lambda args, **kw: real([sys.executable, str(script)], **kw)
            ):
                client = AmaClient(bridge="yamaneko")
                try:
                    result = client.candidates(PuyoField.from_rows(["......"] * 13), ["RG"])
                finally:
                    client.close()
        self.assertEqual(result.candidates[0].gtr_form, "緑のL字")


class FollowUpTest(unittest.TestCase):
    def _candidate(self, **kw):  # noqa: ANN202
        base = dict(x=0, rotation="UP", threat_hits=1, threat_next=(1, "RIGHT"), next=(5, "UP"), fire_chain=0)
        base.update(kw)
        base.setdefault("next_share", 50)
        return SimpleNamespace(gtr=False, gtr_next=None, **base)

    def _empty(self):  # noqa: ANN202
        from uedock.games.puyo.simulator.rules import COLS, EMPTY, ROWS

        return [[EMPTY] * COLS for _ in range(ROWS)]

    def test_next_pair_lands_after_candidate(self) -> None:
        board = self._empty()
        cells = text.follow_up_cells(board, self._candidate(threat_next=(0, "UP")), "RG", "BY")
        # 候補が1列目の下2段を埋め、次の組はその上に乗る。元の盤面は変えない
        bottom = len(board) - 1
        self.assertEqual(cells, [((bottom - 2, 0), "B"), ((bottom - 3, 0), "Y")])
        self.assertEqual(board, self._empty())

    def test_horizontal_and_down(self) -> None:
        bottom = len(self._empty()) - 1
        cells = text.follow_up_cells(self._empty(), self._candidate(x=3), "RG", "BY")
        self.assertEqual(cells, [((bottom, 1), "B"), ((bottom, 2), "Y")])
        # 子が下: 子の色が下に来る
        cells = text.follow_up_cells(self._empty(), self._candidate(x=3, threat_next=(1, "DOWN")), "RG", "BY")
        self.assertEqual(cells, [((bottom, 1), "Y"), ((bottom - 1, 1), "B")])

    def test_prefers_threat_move_then_next(self) -> None:
        self.assertEqual(text.follow_up_move(self._candidate()), (1, "RIGHT"))
        self.assertEqual(text.follow_up_move(self._candidate(threat_hits=0)), (5, "UP"))
        self.assertIsNone(text.follow_up_move(self._candidate(threat_hits=0, next=None, threat_next=None)))
        # 読みでばらつく置き方(割合が低い)は構想として出さない
        self.assertIsNone(text.follow_up_move(self._candidate(threat_hits=0, next_share=16)))

    def test_candidate_chain_resolved_before_next(self) -> None:
        # 候補で消える時は、消えた後の盤面に次の組を置く
        board = self._empty()
        bottom = len(board) - 1
        for r in (bottom, bottom - 1):
            board[r][0] = "R"
        cells = text.follow_up_cells(board, self._candidate(fire_chain=1, threat_next=(0, "UP")), "RR", "BY")
        self.assertEqual(cells, [((bottom, 0), "B"), ((bottom - 1, 0), "Y")])


def _move():  # noqa: ANN202
    from uedock.games.puyo.engine.ama_client import BattleMove

    return BattleMove(x=0, rotation="UP", eval=0, axis_cell=(12, 0), child_cell=(11, 0), label="build",
                      self_analysis=None, enemy_analysis=None, think_ms=0)


class WindowTest(unittest.TestCase):
    def test_no_ama_and_choosing_candidate_moves_dots(self) -> None:
        from uedock.games.puyo.simulator import window as sim

        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        w = sim.SimulatorWindow()
        try:
            self.assertEqual([name for name, _ in sim.ENGINES], ["yamaneko"])
            self.assertFalse(hasattr(w, "engine_toggle"))
            sent: list = []
            w.advisors["yamaneko"].request = sent.append
            w._request_advice()
            rid = w._request_id
            # 普通の依頼は第一候補だけ。第一候補が出るまで検討は押せない
            self.assertFalse(sent[-1].analyze)
            self.assertFalse(w.analyze_button.isEnabled())
            w._on_advice("yamaneko", rid, _move(), "")
            self.assertTrue(w.analyze_button.isEnabled())
            self.assertFalse(w.candidate_buttons[0].isVisibleTo(w))
            w.analyze_button.click()
            self.assertTrue(sent[-1].analyze)
            self.assertEqual(sent[-1].primary, (0, "UP"))
            self.assertFalse(w.analyze_button.isEnabled())
            n = len(sent)
            w._analyze()  # 結果を待っている間は二重に頼まない
            self.assertEqual(len(sent), n)
            raw = [_raw(x=0, r="UP", axis=[0, 0], child=[0, 1], primary=True, threat_best=False),
                   _raw(x=2, r="UP", axis=[2, 0], child=[2, 1])]
            w._on_candidates(rid, parse_candidates({"list": raw, "reads": 12}), "")
            self.assertTrue(w.candidate_buttons[0].isVisibleTo(w))
            self.assertTrue(w.candidate_buttons[0].isChecked())
            self.assertFalse(w.analyzing)
            self.assertFalse(w.candidate_buttons[2].isVisibleTo(w))
            self.assertEqual(w.candidate_status.text(), "")
            w._select_candidate(1)
            self.assertTrue(w.candidate_buttons[1].isChecked())
            self.assertFalse(w.candidate_buttons[0].isChecked())
            cols = {cell[1] for cell, _color in w.board_view.suggestion}
            self.assertEqual(cols, {2})
            # まともな手が1つだけなら1つだけ出し、そのことを書く。切り替えのキーを押しても落ちない
            w._on_candidates(rid, parse_candidates({"list": raw[:1], "reads": 12}), "")
            w.selected_candidate = None
            w._refresh()
            self.assertFalse(w.candidate_buttons[1].isVisibleTo(w))
            self.assertIn("この1つだけ", w.candidate_status.text())
            # 古い依頼の結果は捨て、新しい依頼で選択を戻す
            w._request_advice()
            self.assertIsNone(w.selected_candidate)
            self.assertFalse(w.candidate_buttons[0].isVisibleTo(w))
            w._on_candidates(rid, parse_candidates({"list": raw, "reads": 12}), "")
            self.assertIsNone(w.candidates)
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
