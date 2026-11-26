"""「プラグイン」画面: 入っているプラグインの一覧と、有効・無効の切り替え。

プラグインを足した・書き換えた時は、Universal Engine Dock を起動し直すと読み込む(起動した時に1回だけ読み込むため)。
「保存して再起動」で、切り替えを保存してから起動し直す。読み込んだ後に plugins/ が変わっていれば、そのことを出す。
"""

from __future__ import annotations

from PyQt6 import QtCore, QtGui, QtWidgets

from ..plugin_loader import PluginManager, plugin_dir
from .toggle_switch import ToggleSwitch

CHANGED_TEXT = "読み込んだ後に、プラグインが足された・書き換えられました。「保存して再起動」を押すと読み込みます。"


class PluginDialog(QtWidgets.QDialog):
    def __init__(self, manager: PluginManager, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("プラグイン")
        self.disabled = set(manager.disabled)
        self.restart_requested = False  # 「保存して再起動」を押した
        self.switches: dict[str, ToggleSwitch] = {}
        layout = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel(f"プラグインの置き場所: {plugin_dir()}"), 1)
        open_folder = QtWidgets.QPushButton("フォルダを開く")
        open_folder.clicked.connect(self._open_folder)
        row.addWidget(open_folder)
        layout.addLayout(row)
        if not manager.plugins:
            layout.addWidget(QtWidgets.QLabel("プラグインが入っていません"))
        for p in manager.plugins:
            switch = ToggleSwitch(p.name + ("(配布しない)" if not p.distributable else ""))
            switch.setChecked(p.id not in self.disabled)
            switch.toggled.connect(lambda on, pid=p.id: self._set(pid, on))
            self.switches[p.id] = switch
            layout.addWidget(switch)
            if p.description:
                desc = QtWidgets.QLabel(p.description)
                desc.setWordWrap(True)
                desc.setStyleSheet("color: gray; margin-left: 44px;")
                layout.addWidget(desc)
        for folder, error in manager.errors.items():
            err = QtWidgets.QLabel(f"読み込めませんでした: {folder}({error})")
            err.setWordWrap(True)
            err.setStyleSheet("color: #d04040;")
            layout.addWidget(err)
        self.changed_label = QtWidgets.QLabel(CHANGED_TEXT)
        self.changed_label.setWordWrap(True)
        self.changed_label.setStyleSheet("color: #c07000;")
        self.changed_label.setVisible(manager.changed_on_disk())
        layout.addWidget(self.changed_label)
        hint = QtWidgets.QLabel("プラグインを足した・書き換えた時は、起動し直すと読み込みます。")
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)
        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        restart = box.addButton("保存して再起動", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
        restart.setToolTip("切り替えを保存し、Universal Engine Dock を閉じて起動し直します")
        restart.clicked.connect(self._restart)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _set(self, plugin_id: str, on: bool) -> None:
        if on:
            self.disabled.discard(plugin_id)
        else:
            self.disabled.add(plugin_id)

    def _restart(self) -> None:
        self.restart_requested = True
        self.accept()

    def _open_folder(self) -> None:
        folder = plugin_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(folder)))
