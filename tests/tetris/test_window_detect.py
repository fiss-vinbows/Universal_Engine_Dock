"""Steam 版ぷよぷよテトリス2のウィンドウ検出(src/capture/window_detect.py)のテスト。

Steam 版でウィンドウから盤面・HOLD・NEXT の位置を求める機能を標準にする。
位置は利用者のスクリーンショット(描画領域 2560×1440)で測った。tests/fixtures/steam_1440p_tetris_1p.png は
その描画領域の x 240〜1080・y 170〜1180(1P の盤面・HOLD・NEXT だけ)を切り出したもの。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests.tetris.config_isolation  # noqa: E402,F401  本番の設定・プラグインを使わない
import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6 import QtWidgets  # noqa: E402

from uedock.games.tetris.capture import window_detect  # noqa: E402,F401
from uedock.games.tetris.capture.window_detect import GameWindow, layout_calibration  # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "steam_1440p_tetris_1p.png"
CROP_ORIGIN = (240, 170)  # 切り出した範囲の左上(描画領域の座標)
REPO_PLUGINS = Path(__file__).resolve().parent.parent / "plugins"


class TestLayout(unittest.TestCase):
    def test_reference_size_is_the_measured_layout(self) -> None:
        cal = layout_calibration(GameWindow(1, "PuyoPuyoTetris2", 214, 237, 2560, 1440))
        self.assertEqual((cal.board_origin_x, cal.board_origin_y), (214 + 409, 237 + 211))
        self.assertEqual((cal.board_width, cal.board_height, cal.cell_size), (480, 960, 48))
        self.assertEqual(cal.hold_rect, (214 + 263, 237 + 263, 124, 77))
        self.assertEqual(len(cal.next_rects), 5)

    def test_scaled_to_1080p(self) -> None:
        cal = layout_calibration(GameWindow(1, "", 0, 0, 1920, 1080))
        self.assertEqual((cal.board_width, cal.board_height, cal.cell_size), (360, 720, 36))
        self.assertAlmostEqual(cal.board_origin_x, 409 * 0.75, delta=1)

    def test_letterbox_when_not_16_9(self) -> None:
        # 横に広いウィンドウ: 高さに合わせて縮め、左右に黒帯(中央に描かれる)
        cal = layout_calibration(GameWindow(1, "", 0, 0, 2000, 900))
        scale = 900 / 1440
        self.assertAlmostEqual(cal.board_origin_x, (2000 - 2560 * scale) / 2 + 409 * scale, delta=1)
        self.assertAlmostEqual(cal.board_origin_y, 211 * scale, delta=1)


class TestRecognitionOnScreenshot(unittest.TestCase):
    def test_reads_the_screenshot_with_the_layout(self) -> None:
        from uedock.games.tetris.worker import recognize

        crop = cv2.imdecode(np.fromfile(FIXTURE, dtype=np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]  # RGB
        client = np.zeros((1440, 2560, 3), dtype=np.uint8)
        x0, y0 = CROP_ORIGIN
        client[y0:y0 + crop.shape[0], x0:x0 + crop.shape[1]] = crop

        class ImageCapture:
            def grab(self, region):
                return client[region.top:region.top + region.height, region.left:region.left + region.width].copy()

        rec = recognize(layout_calibration(GameWindow(1, "PuyoPuyoTetris2", 0, 0, 2560, 1440)), ImageCapture())
        self.assertIsNotNone(rec)
        self.assertEqual(rec.current_piece, "S")
        self.assertTrue(rec.hold_known)
        self.assertIsNone(rec.hold_piece)
        self.assertEqual(tuple(rec.next_queue), ("L", "O", "I", "J", "T"))
        self.assertFalse(any(cell for row in rec.board.grid for cell in row), "着地予想の影をブロックと読んだ")


class TestRecognition2P(unittest.TestCase):
    """2P の位置のずれ(10px)で、おじゃまの穴が埋まった行と読まれ、2P の手が止まった。

    tests/fixtures/steam_1440p_tetris_2p_garbage.png は実機の録画の
    1コマ。直す前の 2P の位置で切り出した範囲なので、描画領域の (263 + 1269, 209) に貼る。
    """

    def test_garbage_holes_are_read(self) -> None:
        from uedock.games.tetris.worker import recognize

        frame = cv2.imdecode(np.fromfile(FIXTURE.with_name("steam_1440p_tetris_2p_garbage.png"), dtype=np.uint8), cv2.IMREAD_COLOR)
        client = np.zeros((1440, 2560, 3), dtype=np.uint8)
        x0, y0 = 263 + 1269, 209
        client[y0:y0 + frame.shape[0], x0:x0 + frame.shape[1]] = frame[:, :, ::-1]  # RGB

        class ImageCapture:
            def grab(self, region):
                return client[region.top:region.top + region.height, region.left:region.left + region.width].copy()

        rec = recognize(layout_calibration(GameWindow(1, "PuyoPuyoTetris2", 0, 0, 2560, 1440), "2p"), ImageCapture())
        holes = {r: [c for c in range(10) if rec.board.grid[r][c] is None] for r in range(11, 20)}
        # 画像のおじゃまの穴: 11段目は2列目、12・13段目は6列目、14段目は5列目、15〜19段目は10列目
        self.assertEqual(holes, {11: [1], 12: [5], 13: [5], 14: [4], 15: [9], 16: [9], 17: [9], 18: [9], 19: [9]})
        self.assertFalse(
            any(all(cell is not None for cell in row) for row in rec.board.grid), "おじゃまの行を揃った行と読んだ"
        )


class TestBadgePlacement(unittest.TestCase):
    """止めるボタンはゲームのタイトルバーの中央、状態の表示(情報パネル)は左下。"""

    def test_stop_button_on_title_bar_and_info_at_bottom_left(self) -> None:
        from uedock.games.tetris import worker as app

        game = GameWindow(5, "PuyoPuyoTetris2", 214, 237, 2560, 1440)
        badge = app.ControlBadge(on_stop=lambda: None)
        try:
            with patch.object(app, "title_bar_rect", return_value=(212, 192, 2564, 45)):
                badge.place(game)
            # Win32 の位置(物理ピクセル)を Qt の位置(論理ピクセル)に直して比べる
            dpr = QtWidgets.QApplication.primaryScreen().devicePixelRatio()
            self.assertEqual(badge.y(), round(192 / dpr), "タイトルバーに重なっていない")
            self.assertEqual(badge.height(), round(45 / dpr))
            self.assertAlmostEqual(badge.x() + badge.width() / 2, (212 + 2564 / 2) / dpr, delta=1, msg="タイトルバーの中央でない")
            self.assertEqual(badge.info.x(), round(214 / dpr) + 12, "情報パネルが左下にない")
            self.assertEqual(badge.info.y() + badge.info.height(), round((237 + 1440) / dpr) - 12)
        finally:
            badge.close()

    def test_info_beside_the_board_like_pba(self) -> None:
        # 情報パネルは自分の盤面の外側(1P は左、2P は右)に、
        # 下端を盤面の下端に揃えて置く
        from uedock.games.tetris import worker as app

        game = GameWindow(5, "PuyoPuyoTetris2", 214, 237, 2560, 1440)
        dpr = QtWidgets.QApplication.primaryScreen().devicePixelRatio()
        for side in ("1p", "2p"):
            cal = layout_calibration(game, side)
            badge = app.ControlBadge(on_stop=lambda: None, side=side)
            try:
                with patch.object(app, "title_bar_rect", return_value=(212, 192, 2564, 45)):
                    badge.place(game, cal)
                info = badge.info
                self.assertEqual(info.y() + info.height(), round((cal.board_origin_y + cal.board_height) / dpr), side)
                # 盤面の枠(1P は左の火力ゲージ)にかけない
                board_left, board_width = cal.board_origin_x / dpr, cal.board_width / dpr
                if side == "1p":
                    self.assertEqual(info.x() + info.width(), round(board_left - board_width * app.BOARD_FRAME_LEFT) - app.PANEL_GAP)
                else:
                    self.assertEqual(info.x(), round(board_left + board_width * (1 + app.BOARD_FRAME_RIGHT)) + app.PANEL_GAP)
                self.assertEqual(info.text_width, app.PANEL_TEXT_WIDTH, "余白が足りているのに縮めている")
            finally:
                badge.close()

    def test_info_shrinks_to_stay_inside_the_game(self) -> None:
        # ゲームのウィンドウが小さく、盤面の横の余白が
        # パネルの幅より狭い時は、パネルを縮めてゲームの描画領域(OBS に写る範囲)に収める
        from uedock.games.tetris import worker as app

        dpr = QtWidgets.QApplication.primaryScreen().devicePixelRatio()
        game = GameWindow(5, "PuyoPuyoTetris2", 0, 237, round(1100 * dpr), round(1100 * dpr * 9 / 16))
        for side in ("1p", "2p"):
            cal = layout_calibration(game, side)
            badge = app.ControlBadge(on_stop=lambda: None, side=side)
            try:
                with patch.object(app, "title_bar_rect", return_value=None):
                    badge.place(game, cal)
                info = badge.info
                self.assertLess(info.text_width, app.PANEL_TEXT_WIDTH, side)
                self.assertGreaterEqual(info.x(), 0, side)
                self.assertLessEqual(info.x() + info.width(), round((game.left + game.width) / dpr), side)
                board_left, board_width = cal.board_origin_x / dpr, cal.board_width / dpr
                if side == "1p":
                    self.assertLessEqual(info.x() + info.width(), board_left - board_width * app.BOARD_FRAME_LEFT)
                else:
                    self.assertGreaterEqual(info.x(), board_left + board_width * (1 + app.BOARD_FRAME_RIGHT))
                self.assertEqual(info.width(), info.text_width + app.PANEL_PADDING * 2)
            finally:
                badge.close()

    def test_info_grows_up_instead_of_eliding(self) -> None:
        # 細くしたパネルで
        # 1行に入らない文言は、行を足して全部出す。下端はそのまま上に伸び、文言が短くなっても縮めない
        from uedock.games.tetris import worker as app

        info = app.InfoPanel("支援モード中")
        self.addCleanup(info.close)
        info.move(100, 300)
        info.set_text_width(app.PANEL_MIN_WIDTH)
        bottom = info.y() + info.height()
        height = info.height()
        text = "置いた手数: この局 145手 / 合計 221手"
        info.set_slot("moves", text)
        self.assertGreater(info.height(), height)
        self.assertEqual(info.y() + info.height(), bottom, "下端が動いた")
        self.assertNotIn("…", info.text())
        self.assertIn(text, "".join(info.text().split("\n")), "文言が全部出ていない")
        grown = info.height()
        info.set_slot("moves", "短い")
        self.assertEqual(info.height(), grown, "支援モードの間に縮んだ(行の位置が動く)")

    def test_without_game_window_top_right(self) -> None:
        from uedock.games.tetris import worker as app

        badge = app.ControlBadge(on_stop=lambda: None)
        try:
            badge.place(None)  # Switch のキャプチャ等: 従来どおり画面の右上
            screen = QtWidgets.QApplication.primaryScreen().geometry()
            self.assertEqual(badge.x() + badge.width(), screen.width() - 20)
            self.assertGreater(badge.info.y(), badge.y())
        finally:
            badge.close()


if __name__ == "__main__":
    unittest.main()
