"""表示の言語。

Universal Engine Dock の文言はソースに日本語で書いてあり、ログ・設定ファイルにも同じ日本語が残る。言語を切り替えた時は、
画面に出す直前に日本語の文言をその言語の文言に置き換える(作る側を変えずに済み、ログは日本語のまま)。

- 訳(Catalog)は言語プラグインが持つ(plugins/english など)。日本語の時は何もしない
- tr(): 日本語の文言 → 今の言語の文言。完全に一致する訳、f-string の形({} の所は何でもよい)の訳、行ごと・文ごと、
  語句の置き換えの順に探す。訳の無い文言は日本語のまま出す
- install(): Qt の文言を設定する関数(QLabel.setText・ボタン・ツールチップ・タブ・コンボボックス・メッセージボックスなど)を
  包み、設定された日本語を覚えてから訳して渡す。retranslate_all() で、開いている画面を今の言語で書き直せる
- 自分で文字を描く部品(情報パネルなど)は、描く前に tr() を呼ぶ
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

JA = "ja"
_JAPANESE = re.compile(r"[぀-ヿ㐀-鿿！-｠　-〿]")
_CACHE_LIMIT = 20000  # 訳の覚えておく数(情報パネルの数字入りの文言で増え続けないよう、超えたら捨てる)
# f-string の形の訳は、決まった文字(空白を除く)がこれより少なければ使わない("{} {}手" のような形は、関係の無い文言にも合ってしまう)
MIN_PATTERN_LITERAL = 3
AUTO_PHRASE_MIN = 3  # 決まった訳を語句の置き換えにも使う時の、日本語の側の最小の文字数(短い語は別の語の一部に合ってしまう)
LONG_PHRASE = 8  # 文に分ける前に置き換える、長い語句の文字数


@dataclass
class Catalog:
    """ある言語の訳。

    entries: 日本語の文言 → 訳。日本語側に "{}" があれば、そこは何の文字列でもよい(f-string の形)。訳の側は
      "{}"(順に)か "{0}" "{1}"(並べ替え)で、合った部分(日本語ならそれも訳す)を入れる。"{{" "}}" は波かっこの文字
    phrases: 一致する訳が無い時に、文言の中で置き換える語句(長いものから)
    """

    entries: dict[str, str] = field(default_factory=dict)
    phrases: dict[str, str] = field(default_factory=dict)
    _exact: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _patterns: list[tuple[re.Pattern[str], str]] = field(default_factory=list, init=False, repr=False)
    _phrases: list[tuple[str, str]] = field(default_factory=list, init=False, repr=False)
    _ready: bool = field(default=False, init=False, repr=False)

    def update(self, other: Catalog) -> None:
        self.entries.update(other.entries)
        self.phrases.update(other.phrases)
        self._ready = False

    def _prepare(self) -> None:
        if self._ready:
            return
        self._exact = {}
        patterns: list[tuple[int, re.Pattern[str], str]] = []
        entries = dict(self.entries)
        # HTML の訳は、タグの間の文字ごとの訳も足す(組み立てた HTML をタグで分けて訳す時に使う)
        for src, dst in self.entries.items():
            if "<" in src and ">" in src:
                src_parts = re.split(r"<[^<>]*>", src)
                dst_parts = re.split(r"<[^<>]*>", dst)
                if len(src_parts) == len(dst_parts):
                    for a, b in zip(src_parts, dst_parts):
                        if _JAPANESE.search(a):
                            entries.setdefault(a, b)
        for src, dst in entries.items():
            if "{}" not in src:
                self._exact[src.replace("{{", "{").replace("}}", "}")] = dst
                continue
            literal = src.replace("{{", "\0L").replace("}}", "\0R")
            parts = literal.split("{}")
            if len("".join(parts).replace(" ", "").replace("　", "")) < MIN_PATTERN_LITERAL:
                continue
            regex = "(.*?)".join(re.escape(p.replace("\0L", "{").replace("\0R", "}")) for p in parts)
            patterns.append((len("".join(parts)), re.compile(regex, re.DOTALL), dst))
        # 決まった文字の多い形(より詳しい形)から試す
        self._patterns = [(r, d) for _n, r, d in sorted(patterns, key=lambda x: -x[0])]
        # 語句の置き換えには、決まった訳(3文字以上で、値の入る所・HTML・改行の無いもの)も使う。組み立てた文言
        # (「左へ移動:Left 右へ移動:Right …」など)の中の語句も訳せるように。語句として書いたものが優先
        phrases = {
            src: dst for src, dst in self._exact.items()
            if len(src) >= AUTO_PHRASE_MIN and _JAPANESE.search(src) and not any(c in src for c in "{<\n")
        }
        phrases.update(self.phrases)
        self._phrases = sorted(phrases.items(), key=lambda kv: -len(kv[0]))
        self._ready = True

    def translate(self, text: str, depth: int = 0) -> str:
        self._prepare()
        hit = self._exact.get(text)
        if hit is not None:
            return hit
        if depth > 6:
            return text
        # f-string の形に合えば、その訳。ただし値の所に日本語が残る時は、合い方が違う(別の文言を組み立てたもの)
        # かもしれないので、ほかの訳し方と比べて日本語の残りが少ない方にする
        candidates: list[str] = []
        for regex, dst in self._patterns:
            m = regex.fullmatch(text)
            if m is not None:
                values = [self.translate(g, depth + 1) if _JAPANESE.search(g) else g for g in m.groups()]
                try:
                    result = dst.format(*values)
                except (IndexError, KeyError, ValueError):
                    continue
                if not _JAPANESE.search(result):
                    return result
                candidates.append(result)
                break
        candidates.append(self._fallback(text, depth))
        return min(candidates, key=lambda c: len(_JAPANESE.findall(c)))

    def _fallback(self, text: str, depth: int) -> str:
        """決まった訳・形の訳が無い時: HTML のタグ・行・文に分けて訳すか、語句を置き換える。"""
        if "<" in text and ">" in text:  # HTML を組み立てた文言は、タグの間の文字ごとに訳す
            parts = re.split(r"(<[^<>]*>)", text)
            if len(parts) > 1:
                return "".join(p if p.startswith("<") or not _JAPANESE.search(p) else self.translate(p, depth + 1) for p in parts)
        if "\n" in text:
            return "\n".join(self.translate(line, depth + 1) if _JAPANESE.search(line) else line for line in text.split("\n"))
        # 文をまたぐ長い語句(訳のある文言のあとに値を足した文言など)は、文に分ける前に置き換える
        for src, dst in self._phrases:
            if len(src) >= LONG_PHRASE and src in text:
                text = text.replace(src, dst)
        if not _JAPANESE.search(text):
            return text
        sentences = [s for s in re.split(r"(?<=。)", text) if s]
        if len(sentences) > 1:
            return " ".join(self.translate(s, depth + 1).strip() for s in sentences)
        stripped = text.strip()
        if stripped != text and stripped:
            head = text[: len(text) - len(text.lstrip())]
            tail = text[len(text.rstrip()):]
            return head + self.translate(stripped, depth + 1) + tail
        for src, dst in self._phrases:
            if src in text:
                text = text.replace(src, dst)
        return text


@dataclass
class _State:
    language: str = JA
    catalog: Catalog | None = None
    word_break: bool = False
    cache: dict[str, str] = field(default_factory=dict)
    listeners: list[Callable[[], None]] = field(default_factory=list)


_state = _State()


def language() -> str:
    """今の表示の言語のキー("ja" か言語プラグインのキー)。"""
    return _state.language


def word_break() -> bool:
    """折り返しを空白で行うか(英語のように単語を切らない言語)。"""
    return _state.word_break


def set_language(key: str, catalog: Catalog | None = None, word_break: bool = False) -> None:
    """表示の言語を変える。catalog が無ければ画面の文言は日本語のまま(情報パネルの表記だけ変わる言語など)。"""
    _state.language = key or JA
    _state.catalog = catalog if key and key != JA else None
    _state.word_break = word_break if key and key != JA else False
    _state.cache.clear()
    for listener in list(_state.listeners):
        listener()


def add_listener(listener: Callable[[], None]) -> None:
    """言語が変わった時に呼ぶ関数を足す(描き直しなど)。"""
    _state.listeners.append(listener)


def remove_listener(listener: Callable[[], None]) -> None:
    if listener in _state.listeners:
        _state.listeners.remove(listener)


class Raw(str):
    """訳さない文言(言語の名前「日本語」など、どの言語でも同じに出すもの)。"""


def tr(text):  # noqa: ANN001, ANN201
    """日本語の文言を、今の言語の文言にする。日本語の時・訳が無い時はそのまま。文字列でなければそのまま返す。"""
    catalog = _state.catalog
    if catalog is None or not isinstance(text, str) or isinstance(text, Raw) or not text or not _JAPANESE.search(text):
        return text
    hit = _state.cache.get(text)
    if hit is None:
        hit = catalog.translate(text)
        if len(_state.cache) >= _CACHE_LIMIT:
            _state.cache.clear()
        _state.cache[text] = hit
    return hit


# ---- 選んだ言語の保存 ----

def _choice_path() -> Path:
    from .paths import config_dir

    return config_dir() / "language.json"


def load_choice(path: Path | None = None) -> str:
    """メイン画面で選んだ表示の言語のキー。保存が無い・読めない時は日本語。"""
    try:
        data = json.loads((path or _choice_path()).read_text(encoding="utf-8"))
        key = data.get("language") if isinstance(data, dict) else None
        return key if isinstance(key, str) and key else JA
    except (OSError, ValueError):
        return JA


def save_choice(key: str, path: Path | None = None) -> None:
    path = path or _choice_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"language": key}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# ---- Qt の文言を設定する関数を包む ----

_PROPERTY = "_i18n_src_{}"  # 設定された日本語を覚えておく動的プロパティ
# (クラス, 名前, クラスに書かれていた元の属性(継承していたなら None), 呼べる元の関数)
_installed: list[tuple[type, str, object, Callable]] = []


def _remember(obj, attr: str, text) -> None:  # noqa: ANN001
    if isinstance(text, str):
        try:
            # 訳さない文言は覚えない(言語を切り替えても書き直さない)
            obj.setProperty(_PROPERTY.format(attr), None if isinstance(text, Raw) else str(text))
        except (RuntimeError, TypeError):
            pass


def _source(obj, attr: str) -> str | None:  # noqa: ANN001
    value = obj.property(_PROPERTY.format(attr))
    return value if isinstance(value, str) else None


def _patch(cls: type, name: str, make: Callable[[Callable], Callable], static: bool = False) -> None:
    # 呼ぶのは getattr で取った関数(sip のクラスの __dict__ の中身はそのままでは呼べない)。
    # 戻す時のために、クラスに書かれていた元の属性も覚える(継承していたものなら None で、戻す時は消す)
    original = getattr(cls, name)
    wrapped = make(original)
    _installed.append((cls, name, cls.__dict__.get(name), original))
    setattr(cls, name, wrapped)


def _setter(attr: str) -> Callable[[Callable], Callable]:
    def make(original: Callable) -> Callable:
        def setter(self, text, *args, **kwargs):  # noqa: ANN001, ANN202
            _remember(self, attr, text)
            return original(self, tr(text), *args, **kwargs)

        return setter

    return make


def _init(attr: str, positions: tuple[int, ...] = (0, 1)) -> Callable[[Callable], Callable]:
    """コンストラクタの文言(最初の文字列の引数)を訳して渡し、日本語を覚える。"""

    def make(original: Callable) -> Callable:
        def init(self, *args, **kwargs):  # noqa: ANN001, ANN202
            args = list(args)
            src = None
            for i in positions:
                if i < len(args) and isinstance(args[i], str):
                    src = args[i]
                    args[i] = tr(src)
                    break
            original(self, *args, **kwargs)
            if src is not None:
                _remember(self, attr, src)

        return init

    return make


def _static(positions: tuple[int, ...]) -> Callable[[Callable], Callable]:
    """メッセージボックス・入力ダイアログ・ファイルダイアログの静的関数: 指定の位置の文字列の引数を訳す。"""

    def make(original: Callable) -> Callable:
        def call(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            args = [tr(a) if i in positions else a for i, a in enumerate(args)]
            for key in ("title", "text", "label", "caption", "filter", "informativeText"):
                if key in kwargs:
                    kwargs[key] = tr(kwargs[key])
            return original(*args, **kwargs)

        return staticmethod(call)

    return make


ITEM_ROLE = 0x0100 + 901  # コンボボックスの項目に日本語を覚えておく役割(Qt.ItemDataRole.UserRole + 901)


def install() -> None:
    """Qt の文言を設定する関数を包む(何度呼んでも1回だけ)。日本語の時は、覚えるだけで文言は変えない。"""
    if _installed:
        return
    from PyQt6 import QtGui, QtWidgets

    W = QtWidgets  # noqa: N806
    for cls, name, attr in (
        (W.QLabel, "setText", "text"),
        (W.QAbstractButton, "setText", "text"),
        (W.QGroupBox, "setTitle", "title"),
        (W.QWidget, "setWindowTitle", "windowTitle"),
        (W.QWidget, "setToolTip", "toolTip"),
        (W.QLineEdit, "setPlaceholderText", "placeholderText"),
        (W.QTextEdit, "setPlaceholderText", "placeholderText"),
        (W.QPlainTextEdit, "setPlaceholderText", "placeholderText"),
        (W.QMenu, "setTitle", "title"),
        (QtGui.QAction, "setText", "text"),
        (QtGui.QAction, "setToolTip", "toolTip"),
        (W.QMessageBox, "setText", "text"),
        (W.QMessageBox, "setInformativeText", "informativeText"),
        (W.QSpinBox, "setSuffix", "suffix"),
        (W.QDoubleSpinBox, "setSuffix", "suffix"),
    ):
        _patch(cls, name, _setter(attr))
    for cls, attr in ((W.QLabel, "text"), (W.QPushButton, "text"), (W.QCheckBox, "text"), (W.QRadioButton, "text"),
                      (W.QGroupBox, "title"), (W.QMenu, "title")):
        _patch(cls, "__init__", _init(attr, (0,)))
    _patch(QtGui.QAction, "__init__", _init("text", (0, 1)))
    _patch(W.QListWidgetItem, "__init__", _init("text", (0, 1)))
    for name in ("information", "warning", "critical", "question", "about"):
        _patch(W.QMessageBox, name, _static((1, 2)), static=True)
    for name in ("getText", "getInt", "getDouble", "getItem", "getMultiLineText"):
        _patch(W.QInputDialog, name, _static((1, 2)), static=True)
    for name in ("getOpenFileName", "getSaveFileName", "getOpenFileNames", "getExistingDirectory"):
        _patch(W.QFileDialog, name, _static((1, 3)), static=True)

    def combo_add(original: Callable) -> Callable:
        def add_item(self, *args, **kwargs):  # noqa: ANN001, ANN202
            args = list(args)
            i = 0 if args and isinstance(args[0], str) else 1  # addItem(text, data) / addItem(icon, text, data)
            src = args[i] if i < len(args) and isinstance(args[i], str) else None
            if src is not None:
                args[i] = tr(src)
            original(self, *args, **kwargs)
            if src is not None and not isinstance(src, Raw):
                self.setItemData(self.count() - 1, str(src), ITEM_ROLE)

        return add_item

    def combo_add_items(original: Callable) -> Callable:
        def add_items(self, texts, *args, **kwargs):  # noqa: ANN001, ANN202
            start = self.count()
            texts = list(texts)
            original(self, [tr(t) for t in texts], *args, **kwargs)
            for k, t in enumerate(texts):
                self.setItemData(start + k, t, ITEM_ROLE)

        return add_items

    def combo_insert(original: Callable) -> Callable:
        def insert_item(self, index, *args, **kwargs):  # noqa: ANN001, ANN202
            args = list(args)
            i = 0 if args and isinstance(args[0], str) else 1
            src = args[i] if i < len(args) and isinstance(args[i], str) else None
            if src is not None:
                args[i] = tr(src)
            original(self, index, *args, **kwargs)
            if src is not None:
                self.setItemData(min(index, self.count() - 1) if index >= 0 else 0, src, ITEM_ROLE)

        return insert_item

    def combo_set_text(original: Callable) -> Callable:
        def set_item_text(self, index, text):  # noqa: ANN001, ANN202
            original(self, index, tr(text))
            self.setItemData(index, text, ITEM_ROLE)

        return set_item_text

    _patch(W.QComboBox, "addItem", combo_add)
    _patch(W.QComboBox, "addItems", combo_add_items)
    _patch(W.QComboBox, "insertItem", combo_insert)
    _patch(W.QComboBox, "setItemText", combo_set_text)

    def tab_add(original: Callable) -> Callable:
        def add_tab(self, page, *args):  # noqa: ANN001, ANN202
            args = list(args)
            i = 0 if args and isinstance(args[0], str) else 1
            if i < len(args) and isinstance(args[i], str):
                _remember(page, "tab", args[i])
                args[i] = tr(args[i])
            return original(self, page, *args)

        return add_tab

    def tab_insert(original: Callable) -> Callable:
        def insert_tab(self, index, page, *args):  # noqa: ANN001, ANN202
            args = list(args)
            i = 0 if args and isinstance(args[0], str) else 1
            if i < len(args) and isinstance(args[i], str):
                _remember(page, "tab", args[i])
                args[i] = tr(args[i])
            return original(self, index, page, *args)

        return insert_tab

    def tab_set_text(original: Callable) -> Callable:
        def set_tab_text(self, index, text):  # noqa: ANN001, ANN202
            page = self.widget(index)
            if page is not None:
                _remember(page, "tab", text)
            return original(self, index, tr(text))

        return set_tab_text

    _patch(W.QTabWidget, "addTab", tab_add)
    _patch(W.QTabWidget, "insertTab", tab_insert)
    _patch(W.QTabWidget, "setTabText", tab_set_text)

    def list_add(original: Callable) -> Callable:
        def add_item(self, item, *args):  # noqa: ANN001, ANN202
            return original(self, tr(item) if isinstance(item, str) else item, *args)

        return add_item

    _patch(W.QListWidget, "addItem", list_add)

    # QFormLayout.addRow("文言", 部品) は Qt の中でラベルを作るので、包んだ QLabel を作って渡す(言語の切り替えでも書き直せる)
    def form_add(original: Callable) -> Callable:
        def add_row(self, *args):  # noqa: ANN001, ANN202
            args = list(args)
            if len(args) >= 2 and isinstance(args[0], str):
                args[0] = W.QLabel(args[0])
            return original(self, *args)

        return add_row

    def form_insert(original: Callable) -> Callable:
        def insert_row(self, row, *args):  # noqa: ANN001, ANN202
            args = list(args)
            if len(args) >= 2 and isinstance(args[0], str):
                args[0] = W.QLabel(args[0])
            return original(self, row, *args)

        return insert_row

    # QDialogButtonBox.addButton("文言", 役割) も Qt の中でボタンを作るので、訳して渡し、作られたボタンに日本語を覚える
    def box_add(original: Callable) -> Callable:
        def add_button(self, *args):  # noqa: ANN001, ANN202
            if args and isinstance(args[0], str):
                button = original(self, tr(args[0]), *args[1:])
                _remember(button, "text", args[0])
                return button
            return original(self, *args)

        return add_button

    _patch(W.QDialogButtonBox, "addButton", box_add)
    _patch(W.QFormLayout, "addRow", form_add)
    _patch(W.QFormLayout, "insertRow", form_insert)


def uninstall() -> None:
    """包んだ関数を元に戻す(テスト用)。"""
    while _installed:
        cls, name, own, _original = _installed.pop()
        if own is None:
            delattr(cls, name)
        else:
            setattr(cls, name, own)


def retranslate_all() -> None:
    """開いている画面の文言を、覚えておいた日本語から今の言語で書き直す。自分で文字を描く部品は描き直す。"""
    from PyQt6 import QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None or not _installed:
        return
    originals = {(cls, name): original for cls, name, _own, original in _installed}

    def call(cls: type, name: str, obj, *args) -> None:  # noqa: ANN001
        originals.get((cls, name), getattr(cls, name))(obj, *args)

    W = QtWidgets  # noqa: N806
    for widget in app.allWidgets():
        for cls, name, attr in (
            (W.QLabel, "setText", "text"),
            (W.QAbstractButton, "setText", "text"),
            (W.QGroupBox, "setTitle", "title"),
            (W.QMenu, "setTitle", "title"),
            (W.QLineEdit, "setPlaceholderText", "placeholderText"),
            (W.QWidget, "setWindowTitle", "windowTitle"),
            (W.QWidget, "setToolTip", "toolTip"),
            (W.QSpinBox, "setSuffix", "suffix"),
            (W.QDoubleSpinBox, "setSuffix", "suffix"),
        ):
            if isinstance(widget, cls):
                src = _source(widget, attr)
                if src is not None:
                    call(cls, name, widget, tr(src))
        if isinstance(widget, W.QComboBox):
            for i in range(widget.count()):
                src = widget.itemData(i, ITEM_ROLE)
                if isinstance(src, str):
                    call(W.QComboBox, "setItemText", widget, i, tr(src))
        if isinstance(widget, W.QTabWidget):
            for i in range(widget.count()):
                page = widget.widget(i)
                src = _source(page, "tab") if page is not None else None
                if src is not None:
                    call(W.QTabWidget, "setTabText", widget, i, tr(src))
        for action in widget.actions():
            for name, attr in (("setText", "text"), ("setToolTip", "toolTip")):
                src = _source(action, attr)
                if src is not None:
                    from PyQt6 import QtGui

                    call(QtGui.QAction, name, action, tr(src))
        widget.updateGeometry()
        widget.update()
