"""起動し直す仕組み(uedock/app.py の restart_command など)と、「プラグイン」画面の「保存して再起動」のテスト。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests.config_isolation as isolation  # noqa: E402
from PyQt6 import QtWidgets  # noqa: E402

from uedock import app  # noqa: E402
from uedock.paths import app_root  # noqa: E402
from uedock.plugin_loader import PluginManager  # noqa: E402
from uedock.ui import main_window  # noqa: E402
from uedock.ui.plugin_dialog import PluginDialog  # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


class TestRestartCommand(unittest.TestCase):
    def test_command_waits_for_this_process(self) -> None:
        self.assertEqual(app.restart_command(123), [sys.executable, "-m", "uedock", "--wait-pid", "123"])
        with patch.object(sys, "frozen", True, create=True):
            self.assertEqual(app.restart_command(123), [sys.executable, "--wait-pid", "123"])
        self.assertEqual(app.restart_command()[-1], str(os.getpid()))

    def test_wait_pid_is_taken_out_of_argv(self) -> None:
        argv = ["uedock", "--wait-pid", "42", "-platform", "offscreen"]
        self.assertEqual(app._take_wait_pid(argv), 42)
        self.assertEqual(argv, ["uedock", "-platform", "offscreen"], "Qt に渡す引数に残った")
        argv = ["uedock", "--wait-pid"]
        self.assertIsNone(app._take_wait_pid(argv))
        self.assertEqual(argv, ["uedock"])
        self.assertIsNone(app._take_wait_pid(["uedock"]))

    @unittest.skipUnless(sys.platform == "win32", "Windows だけ待つ")
    def test_waits_until_the_old_process_ends(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.6)"])
        start = time.monotonic()
        app._wait_for_exit(proc.pid)
        self.assertIsNotNone(proc.wait(timeout=5))
        self.assertGreater(time.monotonic() - start, 0.3, "終わる前に戻った")
        # 長く終わらなくても、決めた長さで諦める
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            start = time.monotonic()
            app._wait_for_exit(proc.pid, timeout_ms=200)
            self.assertLess(time.monotonic() - start, 5)
        finally:
            proc.kill()
            proc.wait()
        app._wait_for_exit(proc.pid)  # もう無いプロセスでは待たない

    def test_spawn_starts_an_independent_copy(self) -> None:
        with patch("subprocess.Popen") as popen:
            self.assertIsNone(app.spawn_restart())
        args, kwargs = popen.call_args
        if sys.platform == "win32":
            # 自分自身を直接起動せず、コマンドプロンプトの start に頼む
            self.assertEqual(args[0][:5], ["cmd.exe", "/d", "/c", "start", ""])
            self.assertEqual(args[0][5:], app.restart_command())
        else:
            self.assertEqual(args[0], app.restart_command())
        self.assertEqual(kwargs["cwd"], str(app_root()))
        self.assertEqual(kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        with patch("subprocess.Popen", side_effect=OSError("起動できない")):
            self.assertIn("起動できない", app.spawn_restart())


class TestPluginDialogRestart(unittest.TestCase):
    def setUp(self) -> None:
        isolation.reset()

    def test_restart_button_saves_and_asks_for_restart(self) -> None:
        dialog = PluginDialog(PluginManager())
        self.assertFalse(dialog.restart_requested)
        self.assertTrue(dialog.changed_label.isHidden(), "変わっていないのに再起動を勧めた")
        restart = next(b for b in dialog.findChildren(QtWidgets.QPushButton) if b.text() == "保存して再起動")
        restart.click()
        self.assertTrue(dialog.restart_requested)
        self.assertEqual(dialog.result(), QtWidgets.QDialog.DialogCode.Accepted, "切り替えを保存しない")

    def test_tells_when_plugins_changed_after_loading(self) -> None:
        manager = PluginManager.load()
        folder = isolation.ROOT / "plugins" / "added"
        folder.mkdir(parents=True)
        (folder / "plugin.py").write_text('from uedock.plugin_api import Plugin\nPLUGIN = Plugin(id="added", name="足した")\n',
                                          encoding="utf-8")
        dialog = PluginDialog(manager)
        self.assertFalse(dialog.changed_label.isHidden())


class TestMainWindowRestart(unittest.TestCase):
    def setUp(self) -> None:
        isolation.reset()
        for target, attr, kwargs in ((main_window, "find_game_window", {"return_value": None}),):
            patcher = patch.object(target, attr, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_restart_closes_after_starting_the_new_one(self) -> None:
        window = main_window.MainWindow(PluginManager())
        self.addCleanup(window.close)
        window.show()
        with patch.object(app, "spawn_restart", return_value=None) as spawn, \
                patch.object(QtWidgets.QApplication, "quit") as quit_app:
            self.assertTrue(window.restart())
            self.assertTrue(window.restart(), "続けて呼ばれた")
        spawn.assert_called_once()
        quit_app.assert_called_once()
        self.assertFalse(window.isVisible())

    def test_restart_failure_keeps_the_window(self) -> None:
        window = main_window.MainWindow(PluginManager())
        self.addCleanup(window.close)
        window.show()
        warnings: list[str] = []
        with patch.object(app, "spawn_restart", return_value="OSError: だめ"), \
                patch.object(QtWidgets.QApplication, "quit") as quit_app, \
                patch.object(QtWidgets.QMessageBox, "warning", side_effect=lambda _p, _t, text: warnings.append(text)):
            self.assertFalse(window.restart())
        quit_app.assert_not_called()
        self.assertTrue(window.isVisible())
        self.assertIn("起動し直せませんでした", warnings[-1])


if __name__ == "__main__":
    unittest.main()
