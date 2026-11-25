"""Universal Engine Dock の起動。ソースからは `python -m uedock`(start.bat)、配布物では launcher.py から main() を呼ぶ。"""

from __future__ import annotations

import ctypes
import os
import sys
import traceback
from datetime import datetime

# pythonw で起動すると sys.stdout / sys.stderr が None になり、print や traceback の出力で落ちる。
# その場合は捨てる先に差し替える
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

from PyQt6 import QtGui, QtWidgets  # noqa: E402

from .paths import APP_NAME, APP_TITLE, app_root, resource_root  # noqa: E402


def _log_uncaught_exception(exc_type, exc_value, exc_traceback) -> None:  # noqa: ANN001
    """メインスレッドの未処理の例外を crash_log.txt に残す(pythonw ではコンソールに出ないため)。"""
    try:
        with open(app_root() / "crash_log.txt", "a", encoding="utf-8") as f:
            f.write(f"\n===== クラッシュ(メインスレッド) {datetime.now().isoformat()} =====\n")
            traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)
    except OSError:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_traceback)


_instance_mutex = None  # 起動中ずっと持ち続ける(閉じると次に Universal Engine Dock を起動できる)


def _claim_first_instance(name: str = "Local\\Universal Engine Dock_PuyoTetrisEngineDock") -> bool:
    """この Universal Engine Dock が1つ目の起動か(Windows の名前付きミューテックスで判定)。判定できない環境では True。

    多重起動の防止に使う(2つ起動すると画面の取り込みが競合する。1P と 2P は1つの Universal Engine Dock から動かす)。
    """
    global _instance_mutex
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return False
    _instance_mutex = handle
    return True


def main() -> None:
    sys.excepthook = _log_uncaught_exception
    app = QtWidgets.QApplication(sys.argv)
    icon_path = resource_root() / "assets" / "icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))
    if not _claim_first_instance():
        QtWidgets.QMessageBox.warning(
            None, APP_TITLE, f"{APP_NAME} はすでに起動しています。\n1P と 2P を同時に動かす時は、メイン画面で両方のゲーム種別を選んでください。"
        )
        return
    from .ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
