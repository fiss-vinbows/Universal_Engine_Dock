"""「プラグイン」画面: 入っているプラグインの一覧と、有効・無効の切り替え。

プラグインを足した・書き換えた時は、Universal Engine Dock を起動し直すと読み込む(起動した時に1回だけ読み込むため)。
「保存して再起動」で、切り替えを保存してから起動し直す。読み込んだ後に plugins/ が変わっていれば、そのことを出す。
「ほかの置き場所」に、Universal Engine Dock のフォルダの外のプラグインのフォルダを足せる(config/plugin_folders.json)。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

from ..plugin_loader import PluginManager, load_extra_dirs, plugin_dir
from .toggle_switch import ToggleSwitch

CHANGED_TEXT = "読み込んだ後に、プラグインが足された・書き換えられました。「保存して再起動」を押すと読み込みます。"
EXTRA_CHANGED_TEXT = "ほかの置き場所を変えました。「保存して再起動」を押すと読み込みます。"


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
        # ほかの置き場所(Universal Engine Dock のフォルダの外のプラグイン)
        self.extra_dirs: list[Path] = load_extra_dirs()
        self._loaded_extra_dirs = list(self.extra_dirs)
        layout.addWidget(QtWidgets.QLabel("ほかの置き場所(Universal Engine Dock のフォルダの外のプラグイン):"))
        self.extra_list = QtWidgets.QListWidget()
        self.extra_list.setMaximumHeight(80)
        layout.addWidget(self.extra_list)
        row = QtWidgets.QHBoxLayout()
        add_dir = QtWidgets.QPushButton("置き場所を足す…")
        add_dir.setToolTip("プラグインのフォルダ(中に plugin.py があるもの)か、それを並べたフォルダを選びます")
        add_dir.clicked.connect(self._add_dir)
        row.addWidget(add_dir)
        remove_dir = QtWidgets.QPushButton("外す")
        remove_dir.clicked.connect(self._remove_dir)
        row.addWidget(remove_dir)
        row.addStretch(1)
        layout.addLayout(row)
        self.changed_label = QtWidgets.QLabel(CHANGED_TEXT)
        self.changed_label.setWordWrap(True)
        self.changed_label.setStyleSheet("color: #c07000;")
        self.changed_label.setVisible(manager.changed_on_disk())
        layout.addWidget(self.changed_label)
        self.extra_changed_label = QtWidgets.QLabel(EXTRA_CHANGED_TEXT)
        self.extra_changed_label.setWordWrap(True)
        self.extra_changed_label.setStyleSheet("color: #c07000;")
        layout.addWidget(self.extra_changed_label)
        self._refresh_extra()
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

    def extra_dirs_changed(self) -> bool:
        return self.extra_dirs != self._loaded_extra_dirs

    def _refresh_extra(self) -> None:
        self.extra_list.clear()
        for folder in self.extra_dirs:
            self.extra_list.addItem(str(folder))
        self.extra_changed_label.setVisible(self.extra_dirs_changed())

    def add_extra_dir(self, folder: Path) -> str | None:
        """ほかの置き場所を足す。足せなければ理由を返す。"""
        folder = Path(folder)
        if not folder.is_dir():
            return f"フォルダが見つかりません: {folder}"
        if folder.resolve() == plugin_dir().resolve():
            return "plugins/ は、足さなくても読み込みます"
        if any(f.resolve() == folder.resolve() for f in self.extra_dirs):
            return "もう足してあります"
        self.extra_dirs.append(folder)
        self._refresh_extra()
        return None

    def _add_dir(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "プラグインの置き場所")
        if not folder:
            return
        error = self.add_extra_dir(Path(folder))
        if error:
            QtWidgets.QMessageBox.warning(self, "置き場所を足せません", error)

    def _remove_dir(self) -> None:
        row = self.extra_list.currentRow()
        if 0 <= row < len(self.extra_dirs):
            del self.extra_dirs[row]
            self._refresh_extra()

    def _open_folder(self) -> None:
        folder = plugin_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(folder)))
