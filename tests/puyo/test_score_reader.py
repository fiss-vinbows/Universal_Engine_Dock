"""ゲームの点数表示(盤面の下の8桁)の読み取り(vision/score_reader.py)と、連鎖の点数の数え方(worker.py)。

ゲームの画面はリポジトリに入れないので、字体の代わりに OpenCV の文字で作った点数欄で確かめる(白い塗り・暗い縁は同じ)。
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets  # noqa: E402

from uedock.games.puyo.vision.score_reader import (  # noqa: E402
    CELL_H,
    CELL_W,
    CELLS,
    MARGIN,
    ScoreReader,
    ScoreReading,
    build_exemplars,
)


def _strip(label: str, shift: int = 0, background: int = 150) -> np.ndarray:
    """ゲームの点数欄に似せた帯(白い塗りに暗い縁の文字を、8つのマスの中央に描く)。shift は左右のずれ(px)。"""
    img = np.full((CELL_H, CELL_W * CELLS + 2 * MARGIN), background, np.uint8)
    for k, ch in enumerate(label):
        if ch == " ":
            continue
        x = MARGIN + k * CELL_W + 6 + shift
        cv2.putText(img, ch, (x, CELL_H - 10), cv2.FONT_HERSHEY_SIMPLEX, 1.5, 40, 9, cv2.LINE_AA)
        cv2.putText(img, ch, (x, CELL_H - 10), cv2.FONT_HERSHEY_SIMPLEX, 1.5, 255, 4, cv2.LINE_AA)
    return img


def _reader() -> ScoreReader:
    cells, names = build_exemplars([(_strip("01234567"), "01234567"), (_strip("89  x   "), "89  x   ")])
    return ScoreReader(cells, names)


class ScoreReaderTest(unittest.TestCase):
    def test_reads_the_total(self) -> None:
        self.assertEqual(_reader().read_strip(_strip("00098765")), ScoreReading(total=98765, text="00098765"))

    def test_reads_a_chain_step(self) -> None:
        # 連鎖の段では「40×  8」(消した個数×10 × 倍率)が出る
        reading = _reader().read_strip(_strip("  40x  8"))
        self.assertEqual(reading.step, (40, 8))
        self.assertIsNone(reading.total)

    def test_small_misalignment_is_tolerated(self) -> None:
        self.assertEqual(_reader().read_strip(_strip("00011606", shift=3)).total, 11606)

    def test_unreadable_strip_is_none(self) -> None:
        noise = np.random.default_rng(0).integers(0, 256, (CELL_H, CELL_W * CELLS + 2 * MARGIN)).astype(np.uint8)
        self.assertIsNone(_reader().read_strip(noise))

    def test_missing_digit_names_in_label_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_exemplars([(_strip("0123"), "0123")])


class FakeReader:
    """決まった順に読みを返す点数欄の読み取り。盤面(geometry に渡す名前)ごとに別の並びを返す。"""

    def __init__(self, readings: dict[str, list[ScoreReading | None]]) -> None:
        self.readings = {who: list(rs) for who, rs in readings.items()}
        self.names = ["0"]

    def read(self, frame_bgr, geometry, origin):  # noqa: ANN001, ANN201
        rs = self.readings.get(geometry, [])
        return rs.pop(0) if rs else None


PLAYERS = {"self": SimpleNamespace(geometry=lambda: "self"), "enemy": SimpleNamespace(geometry=lambda: "enemy")}


class ChainScoreTest(unittest.TestCase):
    """連鎖の前と後の合計の差を、ゲームの点数として数える(worker.py の _update_score)。"""

    def setUp(self) -> None:
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from uedock.games.puyo.overlay.renderer import OverlayDrawData
        from uedock.games.puyo.worker import AssistWorker

        self.worker = AssistWorker(calibration=None, client=None, debug_log_path=None)  # type: ignore[arg-type]
        self.logs: list[str] = []
        self.worker._log = self.logs.append
        self.shown: list = []
        self.worker.draw_data_ready.connect(self.shown.append)
        self.worker._last_data = OverlayDrawData(axis_cell=(12, 0), child_cell=(11, 0), axis_color="red", child_color="blue", label="build")

    def _feed(self, readings: list[ScoreReading | None], t0: float, who: str = "self") -> float:
        """who の盤面の読みを1つずつ流す(SCORE_READ_EVERY tick に1回読むので、その分 tick を進める)。"""
        from uedock.games.puyo.worker import SCORE_READ_EVERY

        self.worker._score_reader = FakeReader({who: readings})  # type: ignore[assignment]
        t = t0
        for _ in range(len(readings) * SCORE_READ_EVERY):
            t += 0.02
            self.worker._now = t
            self.worker._update_score(t, np.zeros((1, 1, 3), np.uint8), PLAYERS, (0, 0))
        return t

    def test_chain_score_is_the_difference_of_totals(self) -> None:
        # 録画 と同じ並び: 1055 → 40×1 → 1095 → 40×8 → 1415
        w = self.worker
        me = w.trackers["self"]
        t = self._feed([ScoreReading(total=1055)] * 2, 0.0)
        self.assertEqual(w._scores["self"].total, 1055)
        me.chain_expected = None
        w._on_self_chain_start()  # 再現できなかった連鎖(点数は分からない)
        t = self._feed([ScoreReading(step=(40, 1))] * 2 + [ScoreReading(total=1095)] * 2
                       + [ScoreReading(step=(40, 8))] * 2 + [ScoreReading(total=1415)] * 2, t)
        self.assertEqual(w._self_fire, (2, 360, True), "連鎖中に、ここまでの点数を出していない")
        me.chain_steps, me.chain_score = 2, 0
        w._now = t
        w._on_self_chain_end(me)
        w._on_self_spawn(me)  # 次の組ぷよが出たら確定する
        self.assertEqual(w._self_fire, (2, 360, False))
        self.assertTrue(any("連鎖の点数(ゲームの点数表示): 2段 360点" in text for text in self.logs), self.logs)
        self.assertEqual(self.shown[-1].self_firing, (2, 360, False))

    def test_predicted_score_is_replaced_by_the_game_score(self) -> None:
        w = self.worker
        me = w.trackers["self"]
        t = self._feed([ScoreReading(total=2000)] * 2, 0.0)
        w._on_self_chain_start()
        w._self_fire = (14, 107_100, True)  # 再現(外れていた)
        t = self._feed([ScoreReading(step=(40, 1))] * 2 + [ScoreReading(total=101_990)] * 2, t)
        self.assertEqual(w._self_fire, (14, 107_100, True), "再現できている間は再現の値を出す")
        me.chain_steps, me.chain_score = 14, 107_100
        w._now = t
        w._on_self_chain_end(me)
        from uedock.games.puyo.worker import CHAIN_SCORE_SETTLE_SEC

        self._feed([None] * int(CHAIN_SCORE_SETTLE_SEC / 0.02 + 2), t)  # 時間がたっても確定する
        self.assertEqual(w._self_fire, (14, 99_990, False))
        self.assertTrue(any("差 +7,110点" in text for text in self.logs), self.logs)

    def test_unstable_total_is_not_used(self) -> None:
        # 数字が切り替わる途中のフレーム(1回だけの読み)は採用しない
        w = self.worker
        self._feed([ScoreReading(total=100)] * 2 + [ScoreReading(total=180)] + [ScoreReading(total=100)], 0.0)
        self.assertEqual(w._scores["self"].total, 100)

    def test_chain_split_by_tracker_is_counted_as_one(self) -> None:
        # 8連鎖の途中で追跡が連鎖の終わりとみなし、最後の段を新しい連鎖(再現 1連鎖 40点)として
        # 数えた。次の組ぷよが出る前の発火は同じ連鎖の続きとして数え、連鎖全体(21,260点)を1つの連鎖として確定する
        from types import SimpleNamespace as NS

        w = self.worker
        me = w.trackers["self"]
        t = self._feed([ScoreReading(total=231)] * 2, 0.0)
        me.chain_expected = NS(steps=8, score=21_260)
        w._on_self_chain_start()
        t = self._feed([ScoreReading(total=2_511)] * 2 + [ScoreReading(total=15_091)] * 2, t)
        me.chain_steps, me.chain_score = 8, 21_260
        w._now = t
        w._on_self_chain_end(me)  # 追跡が早めに終わりとみなした
        me.chain_expected = NS(steps=1, score=40)
        w._on_self_chain_start()  # 最後の段を新しい発火と見た
        self.assertEqual(w._self_fire, (8, 21_260, True), "続きの段だけの再現に戻している")
        t = self._feed([ScoreReading(total=21_491)] * 2, t)
        me.chain_steps, me.chain_score = 1, 40
        w._now = t
        w._on_self_chain_end(me)
        self.assertEqual(w._self_fire, (8, 21_260, False))
        w._on_self_spawn(me)
        self.assertEqual(w._self_fire, (8, 21_260, False))
        finals = [text for text in self.logs if "連鎖の点数(ゲームの点数表示)" in text]
        self.assertEqual(len(finals), 1, finals)
        self.assertIn("21,260点 / 再現 8連鎖 21,260点 差 +0点", finals[0])

    def test_enemy_score_is_not_read_by_default(self) -> None:
        # 両方を支援している時(既定)は、相手の点数欄を読まない(相手側の支援が読んだ値をパネルに出す)
        w = self.worker
        self._feed([ScoreReading(total=500)] * 2, 0.0, who="enemy")
        self.assertIsNone(w._scores["enemy"].total)

    def test_enemy_chain_score_when_supporting_one_side(self) -> None:
        # 片側だけを支援している時は、相手の盤面の点数欄も読み、相手の連鎖の点数をパネルの相手の欄に出す
        w = self.worker
        w.enemy_score = True
        en = w.trackers["enemy"]
        t = self._feed([ScoreReading(total=300)] * 2, 0.0, who="enemy")
        self.assertEqual(w._scores["enemy"].total, 300)
        en.chain_expected = None
        w._on_enemy_chain(en, "chain_start")  # 再現できなかった連鎖
        t = self._feed([ScoreReading(step=(40, 1))] * 2 + [ScoreReading(total=340)] * 2
                       + [ScoreReading(step=(40, 8))] * 2 + [ScoreReading(total=660)] * 2, t, who="enemy")
        self.assertEqual(w._enemy_fire, (2, 360, True), "相手の連鎖中に、ここまでの点数を出していない")
        self.assertIsNone(w._self_fire)
        en.chain_steps, en.chain_score = 2, 0
        w._now = t
        w._on_enemy_chain(en, "chain_end")
        from uedock.games.puyo.worker import CHAIN_SCORE_SETTLE_SEC

        self._feed([None] * int(CHAIN_SCORE_SETTLE_SEC / 0.02 + 2), t, who="enemy")
        self.assertEqual(w._enemy_fire, (2, 360, False))
        self.assertTrue(any("相手の連鎖の点数(ゲームの点数表示): 2段 360点" in text for text in self.logs), self.logs)
        self.assertEqual(self.shown[-1].enemy_firing, (2, 360, False))


if __name__ == "__main__":
    unittest.main()
