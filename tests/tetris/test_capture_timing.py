"""取り込みの計測(src/recognize_process.py の summarize_timings)のテスト。

OBS の録画中に画面の読み取りが遅れる件で、撮影・認識・受け取りまでの時間をログに出す。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uedock.games.tetris.recognize_process import summarize_timings  # noqa: E402


class SummarizeTimingsTest(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(summarize_timings([]), "記録なし")

    def test_median_and_max(self) -> None:
        text = summarize_timings([(10, 70, 90), (30, 120, 500), (20, 80, 100)])
        self.assertEqual(
            text,
            "3回 / 撮影 中央20ms 最大30ms / 撮影と認識 中央80ms 最大120ms / 依頼から受け取り 中央100ms 最大500ms",
        )


if __name__ == "__main__":
    unittest.main()
