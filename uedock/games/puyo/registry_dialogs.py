"""定跡ファイルの登録画面"""

from __future__ import annotations

from pathlib import Path

from PyQt6 import QtWidgets

from .engine.registry import BookEntry, Registry
from .simulator.opening_book import OpeningBook


class BookRegistryDialog(QtWidgets.QDialog):
    """定跡ファイルの登録。今の YamaNEKO の書き出し(opening_tree.exe --export)と同じ形式のファイルを選ぶ。"""

    def __init__(self, registry: Registry, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("定跡ファイルの登録")
        self.books = [BookEntry(b.name, b.path) for b in registry.books]
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("YamaNEKO の定跡の書き出し(opening_tree.exe --export)と同じ形式の JSON を登録できます。\n"
                                          "組み込みの「標準の定跡」はいつでも選べます。"))
        self.list = QtWidgets.QListWidget()
        layout.addWidget(self.list)
        row = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("追加…")
        add.clicked.connect(self._add)
        row.addWidget(add)
        remove = QtWidgets.QPushButton("削除")
        remove.clicked.connect(self._remove)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self._refresh()

    def _refresh(self) -> None:
        self.list.clear()
        for b in self.books:
            self.list.addItem(f"{b.name}  —  {b.path}")

    def add_book(self, name: str, path: str) -> str | None:
        """登録を足す。読めない・形式が違うファイルは登録しない(理由を返す)。"""
        name = name.strip()
        if not name:
            return "名前を入れてください"
        if name == "標準の定跡" or any(b.name == name for b in self.books):
            return f"「{name}」はもう使われている名前です"
        try:
            book = OpeningBook.load(Path(path))
        except (ValueError, KeyError, TypeError) as exc:
            return f"定跡ファイルの形式が違います: {exc}"
        if book is None:
            return f"ファイルが見つかりません: {path}"
        self.books.append(BookEntry(name, path))
        self._refresh()
        return None

    def _add(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "定跡ファイル", "", "定跡 (*.json);;すべてのファイル (*)")
        if not path:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "名前", "一覧に出す名前:", text=Path(path).stem)
        if not ok:
            return
        error = self.add_book(name, path)
        if error:
            QtWidgets.QMessageBox.warning(self, "登録できません", error)

    def _remove(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.books):
            del self.books[row]
            self._refresh()
