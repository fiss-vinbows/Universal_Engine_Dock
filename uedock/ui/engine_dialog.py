"""「エンジンの登録」画面。

ゲーム種別を選び、実行ファイルに名前(と、省略できる起動引数・重みのファイル)を付けて登録する。
同梱の YamaNEKO・KasugAI は登録しなくても選べる。ama・Cold Clear 2 などはここから足す。
"""

from __future__ import annotations

import shlex
from pathlib import Path

from PyQt6 import QtWidgets

from ..engine_registry import EngineEntry, Registry
from ..game_mode import GameMode
from ..paths import resource_root

# 重みの質問で、エンジンごとに添える案内(実行ファイルの名前に含まれる文字 → 案内)
WEIGHTS_HINTS = {
    "cold-clear": "Cold Clear 2 を初心者向けの打ち方にするなら、assets/cold_clear_beginner.json を選んでください。",
}


def weights_question(game: str, exe: str) -> str:
    """重みのファイルを指定するかの質問の文。"""
    text = ("評価の重み(エンジンの打ち方の好みを決める設定ファイル)を指定しますか?\n"
            "分からなければ「いいえ」でかまいません。エンジンの標準の打ち方で考えます。")
    name = Path(exe).name.lower()
    hints = [hint for key, hint in WEIGHTS_HINTS.items() if key in name]
    return text + ("\n\n" + "\n".join(hints) if hints else "")


class EngineRegistryDialog(QtWidgets.QDialog):
    def __init__(self, registry: Registry, modes: dict[str, GameMode], reserved: dict[str, set[str]] | None = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        """reserved: {ゲームモードの ID: 登録に使えない名前(同梱・プラグインのエンジン)}。"""
        super().__init__(parent)
        self.setWindowTitle("思考エンジンの登録")
        self.modes = modes
        self.engines = [EngineEntry(e.game, e.name, e.exe, list(e.args), e.weights) for e in registry.engines]
        self._reserved = reserved or {}
        layout = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("ゲーム種別:"))
        self.game_combo = QtWidgets.QComboBox()
        for mode in modes.values():
            self.game_combo.addItem(mode.name, mode.id)
        self.game_combo.currentIndexChanged.connect(self._refresh)
        row.addWidget(self.game_combo)
        row.addStretch(1)
        layout.addLayout(row)
        self.protocol_label = QtWidgets.QLabel()
        self.protocol_label.setWordWrap(True)
        layout.addWidget(self.protocol_label)
        self.list = QtWidgets.QListWidget()
        layout.addWidget(self.list, 1)
        row = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("追加…")
        add.clicked.connect(self._add)
        row.addWidget(add)
        remove = QtWidgets.QPushButton("削除")
        remove.clicked.connect(self._remove)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self.resize(640, 360)
        self._refresh()

    def current_game(self) -> str:
        return self.game_combo.currentData() or ""

    def _shown(self) -> list[EngineEntry]:
        game = self.current_game()
        return [e for e in self.engines if e.game == game]

    def _refresh(self) -> None:
        mode = self.modes.get(self.current_game())
        self.protocol_label.setText(
            "" if mode is None else f"登録できるエンジン: {mode.engine_protocol}\n"
            "登録したエンジンは、メイン画面の「思考エンジン」の一覧に並びます。"
        )
        self.list.clear()
        for e in self._shown():
            extra = (f"  引数: {shlex.join(e.args)}" if e.args else "") + (f"  重み: {e.weights}" if e.weights else "")
            self.list.addItem(f"{e.name}  —  {e.exe}{extra}")

    def add_engine(self, game: str, name: str, exe: str, args: list[str] | None = None, weights: str = "") -> str | None:
        """登録を足す。登録できなければ理由を返す。"""
        name = name.strip()
        if game not in self.modes:
            return "ゲーム種別を選んでください"
        if not name:
            return "名前を入れてください"
        if name in self._reserved.get(game, set()) or any(e.game == game and e.name == name for e in self.engines):
            return f"「{name}」はもう使われている名前です"
        if not Path(exe).exists():
            return f"実行ファイルが見つかりません: {exe}"
        if weights and not Path(weights).exists():
            return f"重みのファイルが見つかりません: {weights}"
        self.engines.append(EngineEntry(game, name, exe, list(args or []), weights))
        self._refresh()
        return None

    def _add(self) -> None:
        exe, _ = QtWidgets.QFileDialog.getOpenFileName(self, "思考エンジンの実行ファイル", "", "実行ファイル (*.exe);;すべて (*)")
        if not exe:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "名前", "一覧に出す名前:", text=Path(exe).stem)
        if not ok:
            return
        text, ok = QtWidgets.QInputDialog.getText(self, "起動引数", "起動引数(無ければ空のまま):")
        if not ok:
            return
        try:
            args = shlex.split(text)
        except ValueError:
            QtWidgets.QMessageBox.warning(self, "登録できません", "起動引数の引用符が閉じていません")
            return
        weights = ""
        if QtWidgets.QMessageBox.question(self, "重み", weights_question(self.current_game(), exe)) \
                == QtWidgets.QMessageBox.StandardButton.Yes:
            weights, _ = QtWidgets.QFileDialog.getOpenFileName(self, "評価の重み", str(resource_root() / "assets"), "すべて (*)")
        error = self.add_engine(self.current_game(), name, exe, args, weights)
        if error:
            QtWidgets.QMessageBox.warning(self, "登録できません", error)

    def _remove(self) -> None:
        row = self.list.currentRow()
        shown = self._shown()
        if 0 <= row < len(shown):
            self.engines.remove(shown[row])
            self._refresh()
