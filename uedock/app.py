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


WAIT_PID_ARG = "--wait-pid"  # 再起動した時に渡す: 前の Universal Engine Dock が終わるのを待ってから始める
RESTART_WAIT_MS = 15000  # 前の Universal Engine Dock が終わるのを待つ長さ(思考エンジンを止めるのに時間がかかることがある)


def restart_command(pid: int | None = None) -> list[str]:
    """今の Universal Engine Dock をもう1つ起動するコマンド。前のもの(pid)が終わるのを待つ引数を付ける。"""
    pid = os.getpid() if pid is None else pid
    if getattr(sys, "frozen", False):
        return [sys.executable, WAIT_PID_ARG, str(pid)]
    return [sys.executable, "-m", "uedock", WAIT_PID_ARG, str(pid)]


def _take_wait_pid(argv: list[str]) -> int | None:
    """起動引数から、待つ相手(前の Universal Engine Dock)を取り出す(argv からは消す。Qt に渡さないため)。"""
    if WAIT_PID_ARG not in argv:
        return None
    i = argv.index(WAIT_PID_ARG)
    value = argv[i + 1] if i + 1 < len(argv) else ""
    del argv[i:i + 2]
    return int(value) if value.isdigit() else None


def _wait_for_exit(pid: int, timeout_ms: int = RESTART_WAIT_MS) -> None:
    """そのプロセスが終わるまで待つ(長くても timeout_ms)。多重起動の判定・OBS 出力の口・設定のファイルを取り合わないため。"""
    if sys.platform != "win32":
        return
    SYNCHRONIZE = 0x00100000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return  # もう終わっている
    try:
        kernel32.WaitForSingleObject(handle, timeout_ms)
    finally:
        kernel32.CloseHandle(handle)


def spawn_restart() -> str | None:
    """新しい Universal Engine Dock を起動する(新しいものは、この Universal Engine Dock が終わるのを待ってから始まる)。

    起動できなければ理由を返す。呼んだ側は、起動できたらこの Universal Engine Dock を閉じる。
    """
    import subprocess

    env = dict(os.environ)
    # 配布物(PyInstaller)の exe から同じ exe を起動する時、展開先などを受け継がず、別に起動したものとして始めさせる
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    command = restart_command()
    flags = 0
    if sys.platform == "win32":
        # exe が自分自身を直接起動すると、セキュリティソフトが起動を止めて調べることがある(署名の無い exe の自己起動は
        # 怪しい動きとして見られる)。コマンドプロンプトの start に起動を頼み、ふつうに起動した時と同じ形にする
        command = ["cmd.exe", "/d", "/c", "start", "", *command]
        flags = subprocess.CREATE_NO_WINDOW
    try:
        subprocess.Popen(command, cwd=str(app_root()), env=env, close_fds=True, creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def main() -> None:
    sys.excepthook = _log_uncaught_exception
    wait_pid = _take_wait_pid(sys.argv)
    if wait_pid is not None:
        _wait_for_exit(wait_pid)
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
