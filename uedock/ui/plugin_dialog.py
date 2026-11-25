"""「プラグイン」画面: 入っているプラグインの一覧と、有効・無効の切り替え。"""

from __future__ import annotations

from PyQt6 import QtWidgets

from ..plugin_loader import PluginManager, plugin_dir
from .toggle_switch import ToggleSwitch


class PluginDialog(QtWidgets.QDialog):
    def __init__(self, manager: PluginManager, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("プラグイン")
        self.disabled = set(manager.disabled)
        self.switches: dict[str, ToggleSwitch] = {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(f"プラグインの置き場所: {plugin_dir()}"))
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
        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _set(self, plugin_id: str, on: bool) -> None:
        if on:
            self.disabled.discard(plugin_id)
        else:
            self.disabled.add(plugin_id)
