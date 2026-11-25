"""プレイヤー名の帯に重ねるエンジン名(uedock/ui/engine_nameplate.py)のテスト。"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtGui, QtWidgets  # noqa: E402

from uedock.ui.engine_nameplate import (  # noqa: E402
    PLATE_COLORS,
    close_nameplates,
    nameplate_area,
    nameplate_rect,
    show_engine_nameplates,
)

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class NameplateRectTest(unittest.TestCase):
    def test_2560x1440_window(self) -> None:
        # 利用者の実機: クライアント領域 (11, 49) 2560×1440 は基準 1280×720 の2倍。
        # 色の付いた内側は 1P (168, 658)-(490, 682)、2P (790, 658)-(1112, 682)
        self.assertEqual(nameplate_area((11, 49, 2560, 1440), "1p"), (11 + 336, 49 + 1316, 11 + 980, 49 + 1364))
        self.assertEqual(nameplate_area((11, 49, 2560, 1440), "2p"), (11 + 1580, 49 + 1316, 11 + 2224, 49 + 1364))
        # ウィンドウは内側を 1px 広く囲む
        self.assertEqual(nameplate_rect((11, 49, 2560, 1440), "1p"), (11 + 335, 49 + 1315, 646, 50))

    def test_2p_mirrors_1p(self) -> None:
        x1, _, w1, _ = nameplate_rect((0, 0, 1280, 720), "1p")
        x2, _, w2, _ = nameplate_rect((0, 0, 1280, 720), "2p")
        self.assertEqual((w1, x2), (w2, 1280 - (x1 + w1)))

    def test_letterbox(self) -> None:
        # 縦に長いウィンドウは上下に黒帯(描画領域は中央の 16:9)
        _, y0, _, y1 = nameplate_area((0, 0, 1280, 800), "1p")
        self.assertEqual((y0, y1), (40 + 658, 40 + 682))


class PaintTest(unittest.TestCase):
    """動きを確かめる。"""

    def test_paints_measured_color_inside_only(self) -> None:
        client = (0, 0, 1600, 900)  # 基準の 1.25 倍。内側は 1P (210, 822.5)-(612.5, 852.5)
        plates = show_engine_nameplates(client, {"1p": "YamaNEKO_KasugAI"}, lambda _w: True)
        self.addCleanup(close_nameplates, plates)
        plate = plates[0]
        image = plate.grab().toImage()
        dpr = plate.devicePixelRatioF()

        def at(x: float, y: float) -> QtGui.QColor:  # 画面の物理ピクセル → 札の画像の画素
            return image.pixelColor(round((x - plate.x() * dpr)), round((y - plate.y() * dpr)))

        self.assertEqual(at(230, 837).rgb(), PLATE_COLORS["1p"].rgb(), "帯の色でない")
        self.assertEqual(at(400, 825).rgb(), PLATE_COLORS["1p"].rgb())
        self.assertEqual(at(209, 837).alpha(), 0, "色の付いた内側の外(顔の絵の枠)を塗っている")
        self.assertEqual(at(400, 821).alpha(), 0, "白い縁を塗っている")
        self.assertEqual(at(613, 837).alpha(), 0, "白い縁を塗っている")
        # 名前の文字(白)が、本家の名前の範囲(x 284〜526、y 828〜850)に書かれている
        white = [(x, y) for x in range(212, 611) for y in range(824, 852) if at(x, y).red() > 240 and at(x, y).blue() > 240]
        xs, ys = [x for x, _ in white], [y for _, y in white]
        self.assertAlmostEqual((min(xs) + max(xs)) / 2, 405, delta=6, msg="文字の中心が本家と違う")
        self.assertGreaterEqual(min(ys), 826)
        self.assertLessEqual(max(ys), 851)
        if "Arial" in QtGui.QFontDatabase.families():  # テストの画面(offscreen)にはフォントが無いことがある
            self.assertAlmostEqual(min(xs), 284, delta=4)
            self.assertAlmostEqual(max(xs), 526, delta=4)
            self.assertAlmostEqual(min(ys), 828, delta=2)

    def test_long_name_is_elided_inside(self) -> None:
        plates = show_engine_nameplates((0, 0, 1600, 900), {"2p": "W" * 60}, lambda _w: True)
        self.addCleanup(close_nameplates, plates)
        image = plates[0].grab().toImage()
        # 文字は色の付いた内側の端まで行かない(端の 4px は帯の色のまま)
        edge = round(1112 * 1.25 - plates[0].x() * plates[0].devicePixelRatioF()) - 3
        self.assertEqual(image.pixelColor(edge, image.height() // 2).rgb(), PLATE_COLORS["2p"].rgb())


class ShowTest(unittest.TestCase):
    def test_shows_only_when_hidden_from_capture(self) -> None:
        excluded = []

        def exclude(widget):  # noqa: ANN001, ANN202
            excluded.append(widget.isVisible())
            return True

        plates = show_engine_nameplates((0, 0, 1920, 1080), {"1p": "YamaNEKO", "2p": "", "solo": "x"}, exclude)
        self.addCleanup(close_nameplates, plates)
        self.assertEqual([(p.side, p.text) for p in plates], [("1p", "YamaNEKO")])
        self.assertEqual(excluded, [False], "取り込みから外す前に表示した")
        self.assertTrue(plates[0].isVisible())

    def test_not_shown_when_exclusion_fails(self) -> None:
        self.assertEqual(show_engine_nameplates((0, 0, 1920, 1080), {"1p": "YamaNEKO"}, lambda _w: False), [])

    def test_no_window(self) -> None:
        self.assertEqual(show_engine_nameplates(None, {"1p": "YamaNEKO"}, lambda _w: True), [])

    def test_close(self) -> None:
        plates = show_engine_nameplates((0, 0, 1920, 1080), {"1p": "A", "2p": "B"}, lambda _w: True)
        close_nameplates(plates)
        self.assertEqual(plates, [])


if __name__ == "__main__":
    unittest.main()
