"""Steam 版のウィンドウの中の、ぷよの盤面・ネクスト枠の位置の計算のテスト(ウィンドウ探しは tests/test_game_window.py)"""

import json
import os
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from uedock.games.puyo.capture.calibrate import CalibrationResult  # noqa: E402
from uedock.games.puyo.capture.window_detect import (  # noqa: E402
    GameWindow,
    content_rect,
    detect_calibration,
    layout_calibration,
)

FIXTURES = Path(__file__).parent / "fixtures"


class LayoutTests(unittest.TestCase):
    def test_1080p_window_matches_reference(self) -> None:
        # 1920×1080 のウィンドウでは、録画で検証済みのキャリブレーションと同じ(ウィンドウの位置だけずれる)
        ref = json.loads((FIXTURES / "calibration_1080p.json").read_text(encoding="utf-8"))
        cal = layout_calibration(GameWindow(1, "", 100, 50, 1920, 1080)).to_dict()
        for player in ("self", "enemy"):
            self.assertEqual(cal[player]["board_quad"], [[x + 100, y + 50] for x, y in ref[player]["board_quad"]])
            self.assertEqual(cal[player]["next_rects"], [[x + 100, y + 50, w, h] for x, y, w, h in ref[player]["next_rects"]])

    def test_real_window_2516x1415(self) -> None:
        # 利用者の実機の録画: クライアント領域 (12, 51) 2516×1415 で、
        # 画像から見つけた盤面は 自分 (369, 259)-(892, 1208)、相手 (1647, 259)-(2172, 1208)。計算した位置が1px 以内で合う
        cal = layout_calibration(GameWindow(1, "PuyoPuyoTetris2", 12, 51, 2516, 1415))
        for got, want in ((cal.self_player.board_quad, (369, 259, 892, 1208)), (cal.enemy_player.board_quad, (1647, 259, 2172, 1208))):
            for a, b in zip((got[0][0], got[0][1], got[2][0], got[2][1]), want):
                self.assertLessEqual(abs(a - b), 1)

    def test_esports_same_layout(self) -> None:
        # ぷよぷよeスポーツ(タイトル Puyo Puyo Champions)の録画: 配置はテトリス2と同じ。クライアント領域 (3, 49)
        # 2515×1414 で、画像から見つけた盤面は 自分 (360, 257)-(884, 1204)、相手 (1638, 257)-(2162, 1204)
        cal = layout_calibration(GameWindow(1, "Puyo Puyo Champions", 3, 49, 2515, 1414))
        for got, want in ((cal.self_player.board_quad, (360, 257, 884, 1204)), (cal.enemy_player.board_quad, (1638, 257, 2162, 1204))):
            for a, b in zip((got[0][0], got[0][1], got[2][0], got[2][1]), want):
                self.assertLessEqual(abs(a - b), 1)

    def test_scaled_window(self) -> None:
        # 1280×720 なら 2/3 倍
        cal = layout_calibration(GameWindow(1, "", 0, 0, 1280, 720))
        (x0, y0), _, (x1, y1), _ = cal.self_player.board_quad
        self.assertAlmostEqual(x0, 272 * 2 / 3, delta=1)
        self.assertAlmostEqual(x1 - x0, 400 * 2 / 3, delta=1)
        self.assertAlmostEqual(y1 - y0, 723 * 2 / 3, delta=1)
        CalibrationResult.from_dict(cal.to_dict())  # 読み込み時の検査を通る

    def test_letterbox(self) -> None:
        # 横長のウィンドウでは左右に黒帯(高さで倍率が決まり、中央に描かれる)
        ox, oy, scale = content_rect(2000, 1080)
        self.assertEqual((ox, oy, scale), (40, 0, 1.0))
        ox, oy, scale = content_rect(1920, 1200)
        self.assertEqual((ox, oy, scale), (0, 60, 1.0))

    def test_solo_copies_self(self) -> None:
        cal = layout_calibration(GameWindow(1, "", 0, 0, 1920, 1080), solo=True)
        self.assertEqual(cal.enemy_player, cal.self_player)


class DetectTests(unittest.TestCase):
    def test_refines_to_detected_board(self) -> None:
        # 画像の盤面の暗い矩形が計算した位置から少しずれていれば、その位置に合わせ直す(ネクスト枠も一緒に動く)
        window = GameWindow(1, "", 10, 20, 1920, 1080)
        image = np.full((1080, 1920, 3), 220, np.uint8)
        cv2.rectangle(image, (280, 165), (280 + 399, 165 + 722), (30, 30, 30), -1)  # 自分: 右下へ (8, 6) ずれ
        cv2.rectangle(image, (1248, 159), (1248 + 399, 159 + 722), (30, 30, 30), -1)
        grab = mock.Mock(return_value=image)
        cal = detect_calibration(window, grab=grab)
        grab.assert_called_once_with(window.region())
        self.assertEqual(cal.self_player.board_quad[0], (290.0, 185.0))
        self.assertEqual(cal.self_player.next_rects[0][:2], (720 + 8 + 10, 160 + 6 + 20))
        self.assertEqual(cal.enemy_player.board_quad[0], (1258.0, 179.0))

    def test_other_layout_uses_two_detected_boards(self) -> None:
        # どちらの盤面も計算した位置に無くても、同じ大きさの盤面が2つ見えればそれに合わせる(ぷよぷよeスポーツなど配置が違うゲーム)
        window = GameWindow(1, "Puyo Puyo Champions", 0, 0, 1920, 1080)
        image = np.full((1080, 1920, 3), 220, np.uint8)
        cv2.rectangle(image, (400, 200), (400 + 359, 200 + 649), (30, 30, 30), -1)
        cv2.rectangle(image, (1160, 200), (1160 + 359, 200 + 649), (30, 30, 30), -1)
        cal = detect_calibration(window, grab=lambda _r: image)
        self.assertEqual(cal.self_player.board_quad[0], (400.0, 200.0))
        self.assertEqual(cal.enemy_player.board_quad[0], (1160.0, 200.0))

    def test_wider_dark_box_is_not_used(self) -> None:
        """位置が近くても、幅が違う矩形(ぷよのある盤面で、枠の外まで含めて見つけたもの)には合わせない。
       本当は 533px の盤面に 555px の矩形を採用し、右の列ほどドットが右にずれた。"""
        window = GameWindow(1, "", 0, 0, 1920, 1080)
        image = np.full((1080, 1920, 3), 220, np.uint8)
        cv2.rectangle(image, (272, 159), (272 + 416, 159 + 722), (30, 30, 30), -1)  # 自分: 幅 417(本当は 400。+4%)
        cv2.rectangle(image, (1250, 159), (1250 + 401, 159 + 722), (30, 30, 30), -1)  # 相手: 幅 402(+0.5%)は採用
        cal = detect_calibration(window, grab=lambda _r: image)
        self.assertEqual(cal.self_player, layout_calibration(window).self_player)
        self.assertEqual(cal.enemy_player.board_quad[0], (1250.0, 159.0))

    def test_far_or_missing_board_keeps_layout(self) -> None:
        window = GameWindow(1, "", 0, 0, 1920, 1080)
        image = np.full((1080, 1920, 3), 220, np.uint8)
        cv2.rectangle(image, (600, 100), (1000, 823), (30, 30, 30), -1)  # どちらの盤面とも離れている
        self.assertEqual(detect_calibration(window, grab=lambda _r: image), layout_calibration(window))

        def fail(_r):  # noqa: ANN001, ANN202
            raise OSError("capture")

        self.assertEqual(detect_calibration(window, grab=fail), layout_calibration(window))


class BadgeTests(unittest.TestCase):
    def test_badge_centered_on_title_bar(self) -> None:
        # ゲームのウィンドウのタイトルバーが分かれば、その中央にタイトルバーの高さで置く
        from PyQt6 import QtCore, QtWidgets

        from uedock.games.puyo.worker import ControlBadge

        _ = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        anchor = QtCore.QRect(100, 50, 1600, 32)
        badge = ControlBadge(on_stop=lambda: None, anchor=anchor)
        badge.set_lines(["1P: 思考中", "2P: 思考中"])
        self.assertEqual(badge.height(), 32)
        self.assertLessEqual(abs(badge.geometry().center().x() - anchor.center().x()), 1)
        self.assertEqual(badge.y(), 50)
        # 上のバーはテトリスと同じ項目: 検出したウィンドウ・状態の文言は置かない
        self.assertEqual(badge.toolTip(), "1P: 思考中\n2P: 思考中")
        self.assertFalse(hasattr(badge, "detect_label"))
        # 終了ボタンは白いタイトルバーの上でも見えるよう黒地。テトリスと同じ #111
        self.assertIn("background: #111", badge.stop_button.styleSheet())
        free = ControlBadge(on_stop=lambda: None)  # 分からなければ今までどおり画面の右上
        height = free.height()
        free.set_lines(["1P: a", "2P: b"])
        self.assertEqual(free.height(), height, "状態の文言はバーに出さないので、行が増えても高さは変わらない")
        screen = QtWidgets.QApplication.primaryScreen().geometry()
        self.assertEqual(free.x(), screen.width() - free.width() - 20)


if __name__ == "__main__":
    unittest.main()
