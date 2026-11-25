"""ゲームのウィンドウの検出と大きさの調整(uedock/capture/game_window.py)のテスト。ゲームによらない共通部分を移したもの。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import tests.config_isolation  # noqa: F401  本番の設定を使わない
from uedock.capture import game_window
from uedock.capture.game_window import (
    CLIENT_SIZE_PRESETS,
    GameWindow,
    SavedWindow,
    WindowEntry,
    content_rect,
    find_game_window,
    load_saved_window,
    load_window_size,
    parse_client_size,
    resized_window_rect,
    save_saved_window,
    save_window_size,
)


def _entry(title: str, exe: str, width: int = 1920, height: int = 1080, hwnd: int = 1) -> WindowEntry:
    return WindowEntry(GameWindow(hwnd, title, 100, 50, width, height), exe)


class TestFindGameWindow(unittest.TestCase):
    def setUp(self) -> None:
        game_window.SAVED_WINDOW_PATH.unlink(missing_ok=True)

    def test_by_title_or_exe_and_the_largest(self) -> None:
        entries = [
            _entry("Universal Engine Dock", "pythonw.exe", hwnd=1),
            _entry("PuyoPuyoTetris2", "puyopuyotetris2.exe", 1280, 720, hwnd=2),
            _entry("ぷよぷよテトリス2", "", 2560, 1440, hwnd=3),
        ]
        self.assertEqual(find_game_window(windows=entries).hwnd, 3)
        self.assertIsNone(find_game_window(windows=entries[:1]), "Universal Engine Dock 自身をゲームのウィンドウと見なした")

    def test_esports_title(self) -> None:
        # ぷよぷよeスポーツの PC 版はタイトルが「Puyo Puyo Champions」のことがある
        self.assertEqual(find_game_window(windows=[_entry("Puyo Puyo Champions", "", hwnd=5)]).hwnd, 5)

    def test_saved_window_is_found(self) -> None:
        entries = [_entry("別の名前", "mygame.exe", hwnd=7)]
        self.assertIsNone(find_game_window(windows=entries))
        save_saved_window(SavedWindow(exe="mygame.exe"))
        self.assertEqual(load_saved_window(), SavedWindow(exe="mygame.exe"))
        self.assertEqual(find_game_window(windows=entries).hwnd, 7, "一覧から選んだウィンドウを次から探せない")

    def test_saved_title_ignores_marks_and_width(self) -> None:
        saved = SavedWindow(title="My Game™")
        self.assertTrue(saved.matches("ｍｙ　ｇａｍｅ", ""))
        self.assertFalse(saved.matches("Other", ""))


class TestContentRect(unittest.TestCase):
    def test_same_ratio_is_scaled_without_margin(self) -> None:
        self.assertEqual(content_rect(1920, 1080, (2560, 1440)), (0.0, 0.0, 0.75))

    def test_letterbox_when_not_16_9(self) -> None:
        # 16:10 のウィンドウでは上下に黒帯
        ox, oy, scale = content_rect(1920, 1200, (1920, 1080))
        self.assertEqual((ox, oy, scale), (0.0, 60.0, 1.0))
        # 横長のウィンドウでは左右に黒帯
        ox, oy, scale = content_rect(2560, 1080, (1920, 1080))
        self.assertEqual((ox, oy, scale), (320.0, 0.0, 1.0))


class TestResizeWindow(unittest.TestCase):
    def test_presets_are_16_9(self) -> None:
        for w, h in CLIENT_SIZE_PRESETS:
            self.assertEqual(w * 9, h * 16)

    def test_border_kept_and_fit_in_work_area(self) -> None:
        rect = resized_window_rect
        # 枠・タイトルバーの分(幅 +14、高さ +45)はそのまま足し、左上は動かさない
        window, client = (100, 50, 1014, 608), (107, 88, 1000, 563)
        self.assertEqual(rect(window, client, (1600, 900)), (100, 50, 1614, 945))
        # 作業領域からはみ出すなら左・上へずらす
        self.assertEqual(rect(window, client, (1920, 1080), (0, 0, 1920, 1040)), (0, 0, 1934, 1125))
        self.assertEqual(rect((1500, 800, 1014, 608), client, (1600, 900), (0, 0, 2560, 1400)), (946, 455, 1614, 945))

    def test_choice_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "window_size.json"
            self.assertEqual(load_window_size(path), "1920×1080")
            save_window_size("1600×900", path)
            self.assertEqual(load_window_size(path), "1600×900")

    def test_parse_client_size(self) -> None:
        self.assertEqual(parse_client_size("1600×900"), (1600, 900))
        self.assertIsNone(parse_client_size("大きい"))


if __name__ == "__main__":
    unittest.main()
