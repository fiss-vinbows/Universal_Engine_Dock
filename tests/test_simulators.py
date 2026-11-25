"""シミュレーターの登録(移植の段階4)のテスト。ぷよ・テトリス・無限中あけRENのウィンドウを画面に出さずに開いて閉じる。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests.config_isolation  # noqa: E402, F401
from PyQt6 import QtCore, QtWidgets, sip  # noqa: E402

from uedock.games import puyo, tetris  # noqa: E402
from uedock.games.puyo.engine.registry import BUILTIN_BOOK, Registry  # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)




class RegistrationTest(unittest.TestCase):
    def test_buttons(self) -> None:
        self.assertEqual([e.label for e in puyo.MODE.simulators], ["ぷよのシミュレーター"])
        self.assertEqual([e.label for e in tetris.MODE.simulators], ["テトリスのシミュレーター", "無限中あけREN"])


class OpenCloseTest(unittest.TestCase):
    """どれも親を持たない独立したウィンドウで開き、閉じると片付く(メイン画面が隠れても残るように)。"""

    def _open_and_close(self, entry) -> QtWidgets.QWidget:  # noqa: ANN001
        parent = QtWidgets.QWidget()
        window = entry.open(parent)
        self.assertIsNone(window.parentWidget())
        window.show()
        window.close()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete.value)
        self.assertTrue(sip.isdeleted(window), "閉じたウィンドウが残っている")
        return window

    def test_puyo(self) -> None:
        self._open_and_close(puyo.MODE.simulators[0])

    def test_tetris(self) -> None:
        # 思考エンジンは起動しない(Advisor の作り直しは開いた時ではなく考える時に起こる)
        for entry in tetris.MODE.simulators:
            with self.subTest(entry.label):
                self._open_and_close(entry)


class PuyoBookTest(unittest.TestCase):
    """ぷよのシミュレーターの定跡は、1P で選んだ定跡を最初に選んでおく。ここで切り替えられる。"""

    def _window(self, side_books: dict[str, str]):  # noqa: ANN202
        from uedock.games.puyo.simulator.window import SimulatorWindow

        Registry(side_books=side_books).save()
        window = SimulatorWindow()
        self.addCleanup(window.close)
        return window

    def test_follows_1p(self) -> None:
        window = self._window({"1p": BUILTIN_BOOK, "2p": ""})
        self.assertEqual(window.book_combo.currentData(), BUILTIN_BOOK)
        self.assertIsNotNone(window.opening_book)

    def test_none_when_1p_unset(self) -> None:
        window = self._window({"2p": BUILTIN_BOOK})
        self.assertEqual(window.book_combo.currentData(), "")
        self.assertIsNone(window.opening_book)
        self.assertIsNone(window._book_move())

    def test_switch_and_unreadable(self) -> None:
        window = self._window({})
        window.book_combo.setCurrentIndex(window.book_combo.findData(BUILTIN_BOOK))
        self.assertIsNotNone(window.opening_book)
        with patch("uedock.games.puyo.simulator.window._load_book_by_name", return_value=None):
            window.book_combo.setCurrentIndex(0)
            window.book_combo.setCurrentIndex(window.book_combo.findData(BUILTIN_BOOK))
        self.assertIsNone(window.opening_book)
        self.assertFalse(window.book_error.isHidden())


if __name__ == "__main__":
    unittest.main()
