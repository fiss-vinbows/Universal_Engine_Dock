"""OBS 出力(uedock/obs_output.py・uedock/ui/obs_dialog.py・メイン画面の切り替え)のテスト。画面は出さずに動かす。"""

from __future__ import annotations

import os
import socket
import sys
import unittest
import urllib.request
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests.config_isolation as isolation  # noqa: E402
from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: E402

from uedock import obs_output  # noqa: E402
from uedock.capture.game_window import GameWindow  # noqa: E402
from uedock.obs_output import (  # noqa: E402
    DEFAULT_PORT,
    SKIP_PROPERTY,
    ObsConfig,
    ObsOutput,
    ObsOverlayServer,
    compose_frame,
    content_area,
    letterbox,
    load_config,
    save_config,
)
from uedock.plugin_api import FEATURE_OBS_OVERLAY, Plugin  # noqa: E402
from uedock.plugin_loader import PluginManager  # noqa: E402
from uedock.ui import main_window  # noqa: E402
from uedock.ui.obs_dialog import ObsGuideDialog, tips_text  # noqa: E402
from uedock.ui.screen_map import to_logical, to_physical  # noqa: E402
from tests.test_screen_map import LOWER, MAIN  # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310  127.0.0.1 のテスト用サーバー
        return response.read()


class Box(QtWidgets.QWidget):
    """色を塗るだけのウィンドウ。clear なら、ぷよのオーバーレイのように自分の範囲を透明で塗りつぶしてから描く。"""

    def __init__(self, rect: QtCore.QRect, color: QtGui.QColor, clear: bool = False) -> None:
        super().__init__()
        self.color = color
        self.clear = clear
        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint | QtCore.Qt.WindowType.Tool)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(rect)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QtGui.QPainter(self)
        if self.clear:
            painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 0))
            painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceOver)
        # 右半分だけ塗る(左半分は透明のまま)
        painter.fillRect(QtCore.QRect(self.width() // 2, 0, self.width() - self.width() // 2, self.height()), self.color)
        painter.end()


def pixel(image: QtGui.QImage, x: int, y: int) -> tuple[int, int, int, int]:
    c = image.pixelColor(x, y)
    return c.red(), c.green(), c.blue(), c.alpha()


RED = QtGui.QColor(255, 0, 0)
BLUE = QtGui.QColor(0, 0, 255)


class GeometryTest(unittest.TestCase):
    def test_content_area_without_and_with_letterbox(self) -> None:
        self.assertEqual(content_area((100, 50, 1920, 1080)), (100, 50, 1920, 1080))
        # 横長: 左右に黒帯
        self.assertEqual(content_area((0, 0, 2000, 1080)), (40, 0, 1920, 1080))
        self.assertEqual(letterbox((2000, 1080)), (40, 0))
        self.assertEqual(letterbox((1920, 1200)), (0, 60))
        self.assertEqual(letterbox((2560, 1440)), (0, 0))

    def test_to_physical_is_inverse_of_to_logical(self) -> None:
        for screen, point in ((MAIN, (1000, 700)), (LOWER, (3204 + 500, 2160 + 250))):
            self.assertEqual(to_physical(*to_logical(*point, screen), screen), point)


class ComposeTest(unittest.TestCase):
    def test_window_is_scaled_into_1920x1080(self) -> None:
        # 描画領域 960×540 の (100, 100) にある 200×100 のウィンドウは、1920×1080 の画像では 2倍の位置・大きさ
        box = Box(QtCore.QRect(100, 100, 200, 100), RED)
        box.show()
        self.addCleanup(box.close)
        image = compose_frame((0, 0, 960, 540), [box])
        self.assertEqual((image.width(), image.height()), (1920, 1080))
        self.assertEqual(pixel(image, 2 * 260, 2 * 150), (255, 0, 0, 255), "右半分は赤")
        self.assertEqual(pixel(image, 2 * 140, 2 * 150)[3], 0, "左半分は透明のまま")
        self.assertEqual(pixel(image, 2 * 310, 2 * 150)[3], 0, "ウィンドウの外は透明")

    def test_window_that_clears_does_not_erase_others(self) -> None:
        """ぷよのオーバーレイは自分の範囲を透明で塗ってから描く。重なっていても、先に描いた札を消さない。"""
        under = Box(QtCore.QRect(0, 0, 400, 100), RED)  # 右半分 200〜400 が赤
        over = Box(QtCore.QRect(100, 0, 400, 100), BLUE, clear=True)  # 右半分 300〜500 が青。100〜300 は透明
        for w in (under, over):
            w.show()
            self.addCleanup(w.close)
        image = compose_frame((0, 0, 1920, 1080), [under, over])
        self.assertEqual(pixel(image, 250, 50), (255, 0, 0, 255))
        self.assertEqual(pixel(image, 350, 50), (0, 0, 255, 255))

    def test_skipped_and_hidden_windows_are_not_drawn(self) -> None:
        bar = Box(QtCore.QRect(0, 0, 200, 100), RED)
        bar.setProperty(SKIP_PROPERTY, True)
        bar.show()
        self.addCleanup(bar.close)
        hidden = Box(QtCore.QRect(0, 200, 200, 100), RED)
        image = compose_frame((0, 0, 1920, 1080), [bar, hidden])
        self.assertEqual(pixel(image, 150, 50)[3], 0)
        self.assertEqual(pixel(image, 150, 250)[3], 0)

    def test_window_offset_by_game_position(self) -> None:
        # ゲームの描画領域が (500, 300) から始まれば、(600, 400) のウィンドウは画像の (100, 100)
        box = Box(QtCore.QRect(600, 400, 200, 100), RED)
        box.show()
        self.addCleanup(box.close)
        image = compose_frame((500, 300, 1920, 1080), [box])
        self.assertEqual(pixel(image, 250, 150), (255, 0, 0, 255))
        self.assertEqual(pixel(image, 150, 150)[3], 0)

    def test_guide_marks_edges_and_name_bands(self) -> None:
        image = compose_frame((0, 0, 1920, 1080), [], guide=True)
        self.assertEqual(pixel(image, 1, 540)[:3], (255, 220, 0), "左端の枠")
        self.assertEqual(pixel(image, 1918, 540)[:3], (255, 220, 0), "右端の枠")
        # 2P の帯の左の辺(基準 1280×720 の x=789 → 1920 幅で 1183.5)
        self.assertEqual(pixel(image, 1184, 1010)[:3], (255, 220, 0))
        self.assertEqual(pixel(image, 960, 1010)[3], 0, "帯の間は透明")


class BlendingTest(unittest.TestCase):
    """OBS は半透明をリニアに混ぜるので、
    パネルの黒い下地が画面より薄くなった。配る画像では半透明の濃さを OBS の混ぜ方に合わせる。"""

    def test_dark_translucent_gets_denser_and_opaque_stays(self) -> None:
        image = QtGui.QImage(4, 1, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QtCore.Qt.GlobalColor.transparent)
        image.setPixelColor(0, 0, QtGui.QColor(0, 0, 0, 170))  # 情報パネルの下地
        image.setPixelColor(1, 0, QtGui.QColor(255, 255, 255, 170))  # 白い文字のふち
        image.setPixelColor(2, 0, QtGui.QColor(255, 0, 0, 255))  # 不透明
        out = obs_output.match_obs_blending(image)
        black = out.pixelColor(0, 0)
        self.assertEqual((black.red(), black.green(), black.blue()), (0, 0, 0))
        self.assertAlmostEqual(black.alpha(), round((1 - (1 - 170 / 255) ** obs_output.OBS_GAMMA) * 255), delta=1)
        self.assertLess(out.pixelColor(1, 0).alpha(), 170, "白っぽい半透明は少し薄くする")
        self.assertEqual(pixel(out, 2, 0), (255, 0, 0, 255))
        self.assertEqual(out.pixelColor(3, 0).alpha(), 0)

    def test_result_on_obs_matches_the_screen(self) -> None:
        # 黒の下地を緑の背景(203)に重ねた時、OBS(リニアに混ぜる)で画面(sRGB で混ぜる)と同じ暗さになる
        image = QtGui.QImage(1, 1, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        image.setPixelColor(0, 0, QtGui.QColor(0, 0, 0, 170))
        a = obs_output.match_obs_blending(image).pixelColor(0, 0).alpha() / 255
        g = obs_output.OBS_GAMMA
        on_obs = ((203 / 255) ** g * (1 - a)) ** (1 / g) * 255
        on_screen = 203 * (1 - 170 / 255)
        self.assertAlmostEqual(on_obs, on_screen, delta=3)


class ServerTest(unittest.TestCase):
    def test_serves_page_version_and_frame(self) -> None:
        server = ObsOverlayServer(0)
        server.start()
        self.addCleanup(server.stop)
        self.assertFalse(server.connected())
        self.assertIn(b"Universal Engine Dock", fetch(server.url))
        before = int(fetch(server.url + "version"))
        self.assertTrue(server.connected(), "ページが版の番号を聞いたら、つながっている")
        image = QtGui.QImage(1920, 1080, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(RED)
        server.publish(image)
        server.flush()
        self.assertEqual(int(fetch(server.url + "version")), before + 1)
        png = QtGui.QImage.fromData(fetch(server.url + "frame.png"))
        self.assertEqual((png.width(), png.height()), (1920, 1080))
        self.assertEqual(pixel(png, 10, 10), (255, 0, 0, 255))

    def test_connection_expires(self) -> None:
        server = ObsOverlayServer(0)
        server._last_request = 100.0
        self.assertTrue(server.connected(now=101.0))
        self.assertFalse(server.connected(now=103.0))


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        isolation.reset()

    def test_defaults_and_round_trip(self) -> None:
        self.assertEqual(load_config(), ObsConfig(False, DEFAULT_PORT))
        save_config(ObsConfig(True, 20000))
        self.assertEqual(load_config(), ObsConfig(True, 20000))
        save_config(ObsConfig(True, 80))  # 使えないポートは既定に戻す
        self.assertEqual(load_config().port, DEFAULT_PORT)


class ObsOutputTest(unittest.TestCase):
    def setUp(self) -> None:
        isolation.reset()
        save_config(ObsConfig(False, free_port()))

    def make(self, widgets=None, window=None) -> ObsOutput:  # noqa: ANN001
        output = ObsOutput(lambda: window, widgets_provider=lambda: list(widgets or []))
        self.addCleanup(output.stop)
        return output

    def test_needs_plugin_and_switch(self) -> None:
        output = self.make()
        output.set_enabled(True)
        self.assertIsNone(output.server, "プラグインが無効ならサーバーを立てない")
        self.assertTrue(load_config().enabled, "切り替えは保存する")
        output.set_allowed(True)
        self.assertIsNotNone(output.server)
        self.assertIn("まだ読まれていません", output.status_text())
        output.set_enabled(False)
        self.assertIsNone(output.server)
        self.assertEqual(output.status_text(), "")

    def test_port_in_use_is_reported(self) -> None:
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            save_config(ObsConfig(True, busy.getsockname()[1]))
            output = self.make()
            output.set_allowed(True)
            self.assertIsNone(output.server)
            self.assertIn("ほかのアプリで使われている", output.status_text())

    def test_renders_only_while_obs_reads(self) -> None:
        box = Box(QtCore.QRect(100, 100, 200, 100), RED)
        box.show()
        self.addCleanup(box.close)
        output = self.make([box], GameWindow(1, "", 0, 0, 960, 540))
        output.set_allowed(True)
        output.set_enabled(True)
        version = output.server._version
        output.tick()
        output.server.flush()
        self.assertEqual(output.server._version, version, "OBS が読みに来ていない間は描かない")
        fetch(output.url + "version")
        self.assertIn("つながっています", output.status_text())
        output.tick()
        output.server.flush()
        self.assertEqual(output.server._version, version + 1)
        png = QtGui.QImage.fromData(fetch(output.url + "frame.png"))
        self.assertEqual(pixel(png, 520, 300), (255, 0, 0, 255))
        output.tick()
        output.server.flush()
        self.assertEqual(output.server._version, version + 1, "変わっていなければ配り直さない")

    def test_without_game_window_uses_the_screen(self) -> None:
        box = Box(QtCore.QRect(100, 100, 200, 100), RED)
        box.show()
        self.addCleanup(box.close)
        output = self.make([box], None)
        screen = box.screen().geometry()
        self.assertEqual(output.frame_area([box])[2:], obs_output.content_area((0, 0, screen.width(), screen.height()))[2:])


class DialogTest(unittest.TestCase):
    def setUp(self) -> None:
        isolation.reset()
        save_config(ObsConfig(True, free_port()))

    def test_shows_address_copies_it_and_turns_guide_off_when_closed(self) -> None:
        output = ObsOutput(lambda: GameWindow(1, "", 0, 0, 2000, 1080))
        self.addCleanup(output.stop)
        output.set_allowed(True)
        dialog = ObsGuideDialog(output)
        self.assertEqual(dialog.url_edit.text(), output.url)
        self.assertIn("左右を 40px ずつ", dialog.tips.text(), "16:9 でないウィンドウは切り取る量を出す")
        dialog.copy_btn.click()
        self.assertEqual(QtWidgets.QApplication.clipboard().text(), output.url)
        dialog.guide_switch.setChecked(True)
        self.assertTrue(output.guide)
        dialog.reject()
        self.assertFalse(output.guide, "閉じたら位置合わせ用の枠を消す")

    def test_tips_for_16_9_window(self) -> None:
        self.assertNotIn("黒帯", tips_text((2560, 1440)))
        self.assertIn("上下を 60px ずつ", tips_text((1920, 1200)))


class MainWindowObsTest(unittest.TestCase):
    def setUp(self) -> None:
        isolation.reset()
        save_config(ObsConfig(False, free_port()))
        patcher = patch.object(main_window, "find_game_window", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(main_window.ObsGuideDialog, "exec", return_value=0)
        self.opened = patcher.start()
        self.addCleanup(patcher.stop)

    def test_row_needs_plugin(self) -> None:
        window = main_window.MainWindow(PluginManager())
        self.addCleanup(window.close)
        self.assertTrue(window.obs_row.isHidden())
        window.plugins.plugins.append(Plugin(id="obs_overlay", name="OBS 出力", features=frozenset({FEATURE_OBS_OVERLAY})))
        window._rebuild_mode_buttons()
        self.assertFalse(window.obs_row.isHidden())
        window.obs_switch.setChecked(True)
        self.assertIsNotNone(window.obs.server)
        self.opened.assert_called_once()  # オンにした時に手順を出す
        self.assertIn("まだ読まれていません", window.obs_status.text())
        # プラグインを無効にしたら止める
        window.plugins.disabled.add("obs_overlay")
        window._rebuild_mode_buttons()
        self.assertIsNone(window.obs.server)
        self.assertTrue(load_config().enabled, "切り替えは残す(プラグインを戻せばまた出る)")


if __name__ == "__main__":
    unittest.main()
