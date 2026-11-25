"""物理ピクセルと Qt の論理ピクセルの変換(uedock/ui/screen_map.py)のテスト。ディスプレイごとに倍率が違う時。"""

from __future__ import annotations

import unittest

from PyQt6 import QtCore

from uedock.ui.screen_map import native_rect, screen_at, screen_of_rect, to_logical, to_logical_rect


class FakeScreen:
    def __init__(self, name: str, x: int, y: int, w: int, h: int, dpr: float) -> None:
        self.name = name
        self._geometry = QtCore.QRect(x, y, w, h)
        self._dpr = dpr

    def geometry(self) -> QtCore.QRect:
        return self._geometry

    def devicePixelRatio(self) -> float:  # noqa: N802
        return self._dpr


# 利用者の PC: Qt が返した値そのまま。主 5120×2160・150%、下 1920×1080・125%、右 1920×1080・100%
MAIN = FakeScreen("main", 0, 0, 3413, 1440, 1.5)
LOWER = FakeScreen("lower", 3204, 2160, 1536, 864, 1.25)
RIGHT = FakeScreen("right", 5120, 1076, 1920, 1080, 1.0)
SCREENS = [MAIN, LOWER, RIGHT]


class ScreenMapTest(unittest.TestCase):
    def test_native_rect_matches_win32(self) -> None:
        # Win32 の EnumDisplayMonitors の値と同じ
        self.assertEqual(native_rect(MAIN), (0, 0, 5120, 2160))
        self.assertEqual(native_rect(LOWER), (3204, 2160, 1920, 1080))
        self.assertEqual(native_rect(RIGHT), (5120, 1076, 1920, 1080))

    def test_screen_at(self) -> None:
        self.assertIs(screen_at(100, 100, SCREENS), MAIN)
        self.assertIs(screen_at(5000, 2100, SCREENS), MAIN, "主ディスプレイの論理の幅(3413)より右でも物理では主ディスプレイ")
        self.assertIs(screen_at(5200, 1200, SCREENS), RIGHT)
        self.assertIs(screen_at(4000, 3000, SCREENS), LOWER)
        self.assertIs(screen_at(-50, 100, SCREENS), MAIN, "どれにも無ければ一番近いもの")
        self.assertIsNone(screen_at(0, 0, []))

    def test_game_on_main_screen_is_unchanged(self) -> None:
        """主ディスプレイだけなら、今までどおり主ディスプレイの倍率で割るのと同じ。"""
        rect = (374, 261, 533, 964)
        self.assertIs(screen_of_rect(*rect, SCREENS), MAIN)
        self.assertEqual(to_logical_rect(*rect, MAIN), QtCore.QRect(*(round(v / 1.5) for v in rect)))

    def test_game_on_other_screen(self) -> None:
        """右のディスプレイ(100%)のゲームは、主ディスプレイの 150% で割らない(割ると x=3480 で主ディスプレイの外に出た)。"""
        rect = (5300, 1200, 533, 964)
        screen = screen_of_rect(*rect, SCREENS)
        self.assertIs(screen, RIGHT)
        self.assertEqual(to_logical_rect(*rect, screen), QtCore.QRect(5300, 1200, 533, 964))
        # 125% の下のディスプレイ: 左上は物理の位置のまま、その中だけ 1/1.25
        self.assertEqual(to_logical(3204 + 500, 2160 + 250, LOWER), (3204 + 400, 2160 + 200))

    def test_no_screen_keeps_physical(self) -> None:
        self.assertEqual(to_logical_rect(10, 20, 30, 40, None), QtCore.QRect(10, 20, 30, 40))


if __name__ == "__main__":
    unittest.main()
