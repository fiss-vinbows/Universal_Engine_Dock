"""メイン画面のサイドの枠に入れる、ぷよの設定項目。

思考エンジンは共通のメイン画面で選ぶ。
設定は変わるたびに side_options.py が保存し、支援モードを始める時に session.py が読む。
"""

from __future__ import annotations

import weakref

from PyQt6 import QtWidgets

from ...ui.toggle_switch import ToggleSwitch
from . import side_options
from .engine.registry import BUILTIN_BOOK, Registry
from .registry_dialogs import BookRegistryDialog
from .side_options import CPU_USE_CHOICES, SideOptions

YAMANEKO_NAME = "YamaNEKO"
# 開いている設定項目の画面(定跡の登録を変えた時に、ほかのサイドの一覧も作り直す)
_panels: "weakref.WeakSet[PuyoSidePanel]" = weakref.WeakSet()


class PuyoSidePanel(QtWidgets.QWidget):
    def __init__(self, side: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.side = side
        self._loading = True
        self._engine = YAMANEKO_NAME
        options = side_options.load(side)
        form = QtWidgets.QVBoxLayout(self)
        form.setContentsMargins(0, 0, 0, 0)

        # 情報パネルの表記は、メイン画面の「表示の言語」(言語プラグイン)にまとめた。保存してある値はそのまま残す
        self._language = options.language

        # 定跡。使うと AI の思考より定跡の手を優先し、定跡に無い手順になったら AI に戻る
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("定跡:"))
        self.book_combo = QtWidgets.QComboBox()
        self.book_combo.setToolTip("選んだ定跡の手を AI の思考より優先します。定跡に無い手順になったら AI の思考に戻ります")
        self.book_combo.currentIndexChanged.connect(self._save)
        row.addWidget(self.book_combo, 1)
        self.book_btn = QtWidgets.QPushButton("定跡の登録…")
        self.book_btn.clicked.connect(self._open_book_registry)
        row.addWidget(self.book_btn)
        form.addLayout(row)
        self._fill_books(options.book)

        # 思考エンジンの負荷。探索スレッド数は YamaNEKO だけ(起動時に思考時間を測って探索の幅を決めるので、減らすと幅が狭まる)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("CPU の使用:"))
        self.threads_combo = QtWidgets.QComboBox()
        for text, key in CPU_USE_CHOICES:
            self.threads_combo.addItem(text, key)
        self.threads_combo.setToolTip("YamaNEKO の探索スレッド数。減らすと CPU の使用率が下がるが、探索の幅が狭まり手の質が少し落ちる。\n"
                                      "「自動」は1つの時は全コア、1P・2P の両方の時は半分ずつ")
        self.threads_combo.setCurrentIndex(max(0, self.threads_combo.findData(options.cpu_use)))
        self.threads_combo.currentIndexChanged.connect(self._save)
        row.addWidget(self.threads_combo, 1)
        form.addLayout(row)

        self.low_priority = ToggleSwitch("思考エンジンの優先度を下げる")
        self.low_priority.setToolTip("思考エンジンのプロセスの優先度を「通常以下」にします。使用率は下がりませんが、\n"
                                     "ゲームが CPU を必要とする時はゲームが先に動きます")
        self.low_priority.setChecked(options.low_priority)
        form.addWidget(self.low_priority)

        self.haipuyo = ToggleSwitch("配ぷよ読み")
        self.haipuyo.setToolTip(
            "試合の最初からの組ぷよで配ぷよを照合し、先のツモで組みます。\n"
            "他の方の AI との対戦で使うかどうかは、相手との取り決めで決めてください。"
        )
        self.haipuyo.setChecked(options.haipuyo)
        form.addWidget(self.haipuyo)

        self.peek = ToggleSwitch("相手のツモを覗く")
        self.peek.setToolTip(
            "相手も同じ配ぷよなら、相手が先に進んでいる時に相手のネクストが自分の先のツモになるので、それを使って組みます。\n"
            "画面に見えている相手のネクストを使うだけです。配ぷよが相手と違うと分かったら、その試合では使いません。"
        )
        self.peek.setChecked(options.peek)
        form.addWidget(self.peek)

        # 相手の盤面を見る(凝視)。一人用には相手がいないので出さない
        self.gaze = ToggleSwitch("相手の盤面を見る(相手もぷよの時)")
        self.gaze.setToolTip(
            "相手の盤面とネクストも読み、潰し・受け・本線発火を判断します。\n"
            "相手がテトリスの時は切ってください(相手のサイドのゲーム種別がテトリスなら、切り替えに関わらず見ません)。"
        )
        self.gaze.setChecked(options.gaze)
        self.gaze.setVisible(side != "solo")
        form.addWidget(self.gaze)

        for switch in (self.low_priority, self.haipuyo, self.peek, self.gaze):
            switch.toggled.connect(self._save)
        self._loading = False
        self._apply_engine()
        _panels.add(self)

    def options(self) -> SideOptions:
        return SideOptions(
            language=self._language,
            book=self.book_combo.currentData() or "",
            cpu_use=self.threads_combo.currentData() or "auto",
            low_priority=self.low_priority.isChecked(),
            haipuyo=self.haipuyo.isChecked(),
            peek=self.peek.isChecked(),
            gaze=self.gaze.isChecked(),
        )

    def _save(self) -> None:
        if not self._loading:
            side_options.save(self.side, self.options())

    def set_engine(self, name: str) -> None:
        """このサイドの思考エンジンが変わった(共通のメイン画面が呼ぶ)。YamaNEKO だけの項目を暗転させる。"""
        self._engine = name
        self._apply_engine()

    def _apply_engine(self) -> None:
        for w in (self.haipuyo, self.peek, self.threads_combo):
            w.setEnabled(self._engine == YAMANEKO_NAME)  # YamaNEKO 以外では設定できなくする

    def _fill_books(self, selected: str) -> None:
        """定跡の一覧(使わない・標準の定跡・登録した定跡)。selected が無くなっていれば「使わない」。"""
        loading, self._loading = self._loading, True
        self.book_combo.clear()
        self.book_combo.addItem("使わない", "")
        self.book_combo.addItem(BUILTIN_BOOK, BUILTIN_BOOK)
        for book in Registry.load().books:
            self.book_combo.addItem(book.name, book.name)
        self.book_combo.setCurrentIndex(max(0, self.book_combo.findData(selected)))
        self._loading = loading
        self._save()  # 無くなった定跡を選んでいたら「使わない」で保存し直す

    def _open_book_registry(self) -> None:
        registry = Registry.load()
        dialog = BookRegistryDialog(registry, self)
        if dialog.exec():
            registry.books = dialog.books
            if registry.selected_book not in [BUILTIN_BOOK, *[b.name for b in dialog.books]]:
                registry.selected_book = BUILTIN_BOOK
            registry.save()
            for panel in list(_panels):
                panel._fill_books(panel.book_combo.currentData() or "")
