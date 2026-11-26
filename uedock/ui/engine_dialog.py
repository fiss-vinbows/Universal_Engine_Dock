"""「エンジンの登録」画面。

ゲーム種別を選び、実行ファイルに名前(と、省略できる起動引数・重みのファイル)を付けて登録する。
同梱の YamaNEKO・KasugAI も、初めて起動した時からここに並ぶ(uedock/engine_registry.py の Registry.add_bundled)。
エンジンの新しい版が出た時は「変更…」で実行ファイルを差し替える。名前が変わらないので、サイドごとの選択はそのまま残る。
「同梱に戻す」で、配布物に入っている同梱のエンジンに戻せる(消した時も戻る)。
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from PyQt6 import QtWidgets

from ..engine_registry import EngineEntry, Registry, bundled_key, resolve_path
from ..game_mode import GameMode
from ..paths import resource_root

# 重みの質問で、エンジンごとに添える案内(実行ファイルの名前に含まれる文字 → 案内)
WEIGHTS_HINTS = {
    "cold-clear": "Cold Clear 2 を初心者向けの打ち方にするなら、assets/cold_clear_beginner.json を選んでください。",
}
BUNDLED_MARK = " (同梱)"  # 一覧で、配布物に入っている同梱のエンジンを使っている登録に付ける


def weights_question(game: str, exe: str) -> str:
    """重みのファイルを指定するかの質問の文。"""
    text = ("評価の重み(エンジンの打ち方の好みを決める設定ファイル)を指定しますか?\n"
            "分からなければ「いいえ」でかまいません。エンジンの標準の打ち方で考えます。")
    name = Path(exe).name.lower()
    hints = [hint for key, hint in WEIGHTS_HINTS.items() if key in name]
    return text + ("\n\n" + "\n".join(hints) if hints else "")


def _same_file(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


class EngineRegistryDialog(QtWidgets.QDialog):
    def __init__(self, registry: Registry, modes: dict[str, GameMode], reserved: dict[str, set[str]] | None = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        """reserved: {ゲームモードの ID: 登録に使えない名前(プラグインのエンジンなど)}。"""
        super().__init__(parent)
        self.setWindowTitle("思考エンジンの登録")
        self.modes = modes
        self.engines = [EngineEntry(e.game, e.name, e.exe, list(e.args), e.weights) for e in registry.engines]
        # 「同梱に戻す」で登録へ入れた同梱のエンジン(bundled_key)。保存する時に登録の印へ足す
        self.restored: set[str] = set()
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
        change = QtWidgets.QPushButton("変更…")
        change.setToolTip("選んだエンジンの実行ファイル・起動引数・重みを差し替えます(エンジンの新しい版を使う時など)。\n"
                          "名前は変わらないので、メイン画面の選択はそのまま残ります")
        change.clicked.connect(self._change)
        row.addWidget(change)
        remove = QtWidgets.QPushButton("削除")
        remove.clicked.connect(self._remove)
        row.addWidget(remove)
        row.addStretch(1)
        self.restore_button = QtWidgets.QPushButton("同梱に戻す")
        self.restore_button.setToolTip("このゲーム種別の同梱のエンジンを、配布物に入っているものに戻します(消した時も戻ります)")
        self.restore_button.clicked.connect(self._restore)
        row.addWidget(self.restore_button)
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

    def is_bundled(self, entry: EngineEntry) -> bool:
        """配布物に入っている同梱のエンジン(差し替えていないもの)を使っている登録か。"""
        mode = self.modes.get(entry.game)
        if mode is None:
            return False
        return any(spec.name == entry.name and _same_file(spec.exe, entry.spec().exe) for spec in mode.builtin_engines)

    def _refresh(self) -> None:
        mode = self.modes.get(self.current_game())
        self.protocol_label.setText(
            "" if mode is None else f"登録できるエンジン: {mode.engine_protocol}\n"
            "登録したエンジンは、メイン画面の「思考エンジン」の一覧に並びます。"
        )
        self.restore_button.setEnabled(mode is not None and bool(mode.builtin_engines))
        self.list.clear()
        for e in self._shown():
            mark = BUNDLED_MARK if self.is_bundled(e) else ""
            extra = (f"  引数: {shlex.join(e.args)}" if e.args else "") + (f"  重み: {e.weights}" if e.weights else "")
            self.list.addItem(f"{e.name}{mark}  —  {e.exe}{extra}")

    def _check_files(self, exe: str, weights: str) -> str | None:
        if not resolve_path(exe).exists():
            return f"実行ファイルが見つかりません: {exe}"
        if weights and not resolve_path(weights).exists():
            return f"重みのファイルが見つかりません: {weights}"
        return None

    def add_engine(self, game: str, name: str, exe: str, args: list[str] | None = None, weights: str = "") -> str | None:
        """登録を足す。登録できなければ理由を返す。"""
        name = name.strip()
        if game not in self.modes:
            return "ゲーム種別を選んでください"
        if not name:
            return "名前を入れてください"
        if name in self._reserved.get(game, set()) or any(e.game == game and e.name == name for e in self.engines):
            return f"「{name}」はもう使われている名前です"
        error = self._check_files(exe, weights)
        if error:
            return error
        self.engines.append(EngineEntry(game, name, exe, list(args or []), weights))
        self._refresh()
        return None

    def change_engine(self, entry: EngineEntry, exe: str, args: list[str], weights: str) -> str | None:
        """登録の実行ファイル・起動引数・重みを差し替える(名前は変えない)。差し替えられなければ理由を返す。"""
        error = self._check_files(exe, weights)
        if error:
            return error
        entry.exe, entry.args, entry.weights = exe, list(args), weights
        self._refresh()
        return None

    def restore_bundled(self, game: str) -> list[str]:
        """そのゲーム種別の同梱のエンジンを、配布物に入っているものに戻す(無ければ先頭に入れる)。戻した名前を返す。"""
        mode = self.modes.get(game)
        if mode is None:
            return []
        for spec in reversed(mode.builtin_engines):  # 先頭に入れていくので逆順に
            entry = EngineEntry.from_spec(game, spec)
            index = next((i for i, e in enumerate(self.engines) if e.game == game and e.name == spec.name), None)
            if index is None:
                first = next((i for i, e in enumerate(self.engines) if e.game == game), len(self.engines))
                self.engines.insert(first, entry)
            else:
                self.engines[index] = entry
            self.restored.add(bundled_key(game, spec.name))
        self._refresh()
        return [spec.name for spec in mode.builtin_engines]

    def _ask_args(self, current: list[str]) -> list[str] | None:
        """起動引数を聞く。やめた時・書き方が違う時は None。"""
        text, ok = QtWidgets.QInputDialog.getText(self, "起動引数", "起動引数(無ければ空のまま):", text=shlex.join(current))
        if not ok:
            return None
        try:
            return shlex.split(text)
        except ValueError:
            QtWidgets.QMessageBox.warning(self, "登録できません", "起動引数の引用符が閉じていません")
            return None

    def _ask_weights(self, exe: str) -> str:
        if QtWidgets.QMessageBox.question(self, "重み", weights_question(self.current_game(), exe)) \
                != QtWidgets.QMessageBox.StandardButton.Yes:
            return ""
        weights, _ = QtWidgets.QFileDialog.getOpenFileName(self, "評価の重み", str(resource_root() / "assets"), "すべて (*)")
        return weights

    def _add(self) -> None:
        exe, _ = QtWidgets.QFileDialog.getOpenFileName(self, "思考エンジンの実行ファイル", "", "実行ファイル (*.exe);;すべて (*)")
        if not exe:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "名前", "一覧に出す名前:", text=Path(exe).stem)
        if not ok:
            return
        args = self._ask_args([])
        if args is None:
            return
        error = self.add_engine(self.current_game(), name, exe, args, self._ask_weights(exe))
        if error:
            QtWidgets.QMessageBox.warning(self, "登録できません", error)

    def _selected(self) -> EngineEntry | None:
        row = self.list.currentRow()
        shown = self._shown()
        return shown[row] if 0 <= row < len(shown) else None

    def _change(self) -> None:
        entry = self._selected()
        if entry is None:
            QtWidgets.QMessageBox.information(self, "変更", "変更するエンジンを一覧から選んでください")
            return
        current = entry.spec()
        exe, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, f"「{entry.name}」の実行ファイル", str(current.exe.parent), "実行ファイル (*.exe);;すべて (*)"
        )
        if not exe:
            return
        args = self._ask_args(entry.args)
        if args is None:
            return
        weights = entry.weights
        if weights and current.weights is not None:
            # 今の重みを残すのが普通(新しい版の実行ファイルに差し替えるだけの時)
            if QtWidgets.QMessageBox.question(
                self, "重み", f"評価の重みを選び直しますか?\n今の重み: {weights}\n「いいえ」なら今の重みのままにします。"
            ) == QtWidgets.QMessageBox.StandardButton.Yes:
                chosen, _ = QtWidgets.QFileDialog.getOpenFileName(self, "評価の重み", str(current.weights.parent), "すべて (*)")
                weights = chosen or weights
        else:
            weights = self._ask_weights(exe)
        error = self.change_engine(entry, exe, args, weights)
        if error:
            QtWidgets.QMessageBox.warning(self, "変更できません", error)

    def _remove(self) -> None:
        entry = self._selected()
        if entry is not None:
            self.engines.remove(entry)
            self._refresh()

    def _restore(self) -> None:
        mode = self.modes.get(self.current_game())
        if mode is None or not mode.builtin_engines:
            return
        names = "・".join(spec.name for spec in mode.builtin_engines)
        if QtWidgets.QMessageBox.question(
            self, "同梱に戻す", f"{names} を、配布物に入っている同梱のものに戻しますか?\n差し替えた実行ファイル・起動引数・重みは消えます。"
        ) == QtWidgets.QMessageBox.StandardButton.Yes:
            self.restore_bundled(self.current_game())
