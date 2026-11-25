"""表示の言語(uedock/i18n.py)と英語表示プラグイン(plugins/english)のテスト。

「UED の画面も全部」。
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests.config_isolation as isolation  # noqa: E402
from PyQt6 import QtWidgets  # noqa: E402

from uedock import i18n  # noqa: E402
from uedock.i18n import Catalog  # noqa: E402
from uedock.plugin_api import Language, Plugin  # noqa: E402
from uedock.plugin_loader import PluginManager  # noqa: E402
from uedock.ui import main_window  # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
ROOT = Path(__file__).resolve().parent.parent


def load_english() -> Plugin:
    """plugins/english/plugin.py を、プラグインの読み込みと同じ形(パッケージ)で読む。"""
    folder = ROOT / "plugins" / "english"
    spec = importlib.util.spec_from_file_location("uedock_plugin_english_test", folder / "plugin.py",
                                                  submodule_search_locations=[str(folder)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.PLUGIN


class CatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = Catalog(
            entries={
                "閉じる": "Close",
                "置いた手数: この局 {}手 / 合計 {}手": "Moves: {} this game / {} total",
                "狙い: {}": "Aim: {}",
                "開幕テンプレ": "Opener template",
                "{1}を{0}に": "x",  # 並べ替えの訳
                "{} {}手": "{} {} moves",  # 決まった文字が少なすぎる形は使わない
                "<b>推奨: {}</b>": "<b>Recommended: {}</b>",
            },
            phrases={"巡目": " bag"},
        )

    def tr(self, text: str) -> str:
        return self.catalog.translate(text)

    def test_exact_and_pattern(self) -> None:
        self.assertEqual(self.tr("閉じる"), "Close")
        self.assertEqual(self.tr("置いた手数: この局 12手 / 合計 30手"), "Moves: 12 this game / 30 total")

    def test_values_are_translated_too(self) -> None:
        self.assertEqual(self.tr("狙い: 開幕テンプレ"), "Aim: Opener template")

    def test_lines_html_and_phrases(self) -> None:
        self.assertEqual(self.tr("閉じる\n狙い: 開幕テンプレ"), "Close\nAim: Opener template")
        self.assertEqual(self.tr("<ul><li>閉じる</li></ul>"), "<ul><li>Close</li></ul>")
        self.assertEqual(self.tr("2巡目"), "2 bag")
        # HTML の訳のタグの間の文字だけでも訳せる(組み立てた HTML をタグで分けた時)
        self.assertEqual(self.tr("推奨: J"), "Recommended: J")

    def test_too_generic_pattern_is_not_used(self) -> None:
        self.assertEqual(self.tr("置きミス 1回 確認 3手"), "置きミス 1回 確認 3手")

    def test_unknown_stays_japanese(self) -> None:
        self.assertEqual(self.tr("訳の無い文言"), "訳の無い文言")


class StateTest(unittest.TestCase):
    def tearDown(self) -> None:
        i18n.set_language(i18n.JA)

    def test_japanese_does_nothing(self) -> None:
        self.assertEqual(i18n.tr("閉じる"), "閉じる")
        i18n.set_language("en", Catalog({"閉じる": "Close"}))
        self.assertEqual(i18n.tr("閉じる"), "Close")
        self.assertEqual(i18n.tr(i18n.Raw("閉じる")), "閉じる", "訳さない印の文言を訳した")
        self.assertEqual(i18n.tr(3), 3)

    def test_choice_is_saved(self) -> None:
        isolation.reset()
        self.assertEqual(i18n.load_choice(), i18n.JA)
        i18n.save_choice("en")
        self.assertEqual(i18n.load_choice(), "en")


class QtHookTest(unittest.TestCase):
    """Qt の文言を設定する関数を包み、設定された日本語を覚えて訳す。言語を切り替えたら書き直す。"""

    def setUp(self) -> None:
        i18n.install()
        self.catalog = Catalog({"閉じる": "Close", "名前:": "Name:", "保存": "Save", "一覧": "List", "説明": "Help"})

    def tearDown(self) -> None:
        i18n.set_language(i18n.JA)
        i18n.retranslate_all()

    def test_widgets_follow_the_language(self) -> None:
        window = QtWidgets.QWidget()
        self.addCleanup(window.close)
        layout = QtWidgets.QVBoxLayout(window)
        label = QtWidgets.QLabel("閉じる")
        button = QtWidgets.QPushButton("保存")
        button.setToolTip("説明")
        combo = QtWidgets.QComboBox()
        combo.addItem("一覧", "list")
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(QtWidgets.QWidget(), "一覧")
        form = QtWidgets.QFormLayout()
        form.addRow("名前:", QtWidgets.QLineEdit())
        box = QtWidgets.QDialogButtonBox()
        saved = box.addButton("保存", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        for w in (label, button, combo, tabs, box):
            layout.addWidget(w)
        layout.addLayout(form)
        form_label = form.labelForField(form.itemAt(0, QtWidgets.QFormLayout.ItemRole.FieldRole).widget())

        def texts() -> tuple:
            return (label.text(), button.text(), button.toolTip(), combo.itemText(0), tabs.tabText(0), form_label.text(), saved.text())

        self.assertEqual(texts(), ("閉じる", "保存", "説明", "一覧", "一覧", "名前:", "保存"))
        i18n.set_language("en", self.catalog)
        i18n.retranslate_all()
        self.assertEqual(texts(), ("Close", "Save", "Help", "List", "List", "Name:", "Save"))
        self.assertEqual(combo.itemData(0), "list", "項目のデータが変わった")
        label.setText("閉じる")
        self.assertEqual(label.text(), "Close", "英語の時に設定した文言を訳していない")
        i18n.set_language(i18n.JA)
        i18n.retranslate_all()
        self.assertEqual(texts(), ("閉じる", "保存", "説明", "一覧", "一覧", "名前:", "保存"))

    def test_message_box_is_translated(self) -> None:
        # メッセージボックスなどの静的関数を包んだものは、文言の引数(題・本文)を訳して元の関数を呼ぶ
        i18n.set_language("en", self.catalog)
        seen = []
        wrapped = i18n._static((1, 2))(lambda *args: seen.append(args))
        wrapped.__func__(None, "閉じる", "保存", 3)
        self.assertEqual(seen, [(None, "Close", "Save", 3)])
        self.assertIn((QtWidgets.QMessageBox, "warning"), [(cls, name) for cls, name, _own, _o in i18n._installed])


class EnglishPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = load_english()
        self.lang = self.plugin.languages["en"]

    def tearDown(self) -> None:
        i18n.set_language(i18n.JA)

    def test_panel_lines(self) -> None:
        i18n.set_language("en", self.lang.catalog, self.lang.word_break)
        self.assertEqual(i18n.tr("狙い: 開幕テンプレ はちみつ砲 2巡目"), "Aim: Opener template Honey Cup 2 bag")
        self.assertEqual(i18n.tr("提案: T S Z L\nHOLDして 4ライン消し(テトリス)"), "Move: T S Z L\nHold, then 4-line clear (Tetris)")
        self.assertEqual(i18n.tr("エンジン: cold-clear-2"), "Engine: cold-clear-2")
        self.assertEqual(i18n.tr("最大REN: 3REN"), "Max REN: 3 REN")

    def test_every_key_is_still_in_the_source(self) -> None:
        # 訳の一覧の日本語は、ソースにある文言(f-string は値の所を {} にした形)。ソースの文言を変えたら訳も直す
        found: set[str] = set()
        for path in [*(ROOT / "uedock").rglob("*.py"), *(ROOT / "plugins").glob("[!_]*/*.py")]:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    found.add(node.value)
                elif isinstance(node, ast.JoinedStr):
                    found.add("".join(v.value.replace("{", "{{").replace("}", "}}") if isinstance(v, ast.Constant) else "{}"
                                      for v in node.values))
        entries = sys.modules[type(self.lang.catalog).__module__].ENTRIES
        missing = [k for k in entries if k not in found]
        self.assertEqual(missing, [], "ソースに無い文言の訳(ソースの文言が変わった?)")

    def test_hooks_do_not_touch_placeholders(self) -> None:
        for key, value in sys.modules[type(self.lang.catalog).__module__].ENTRIES.items():
            self.assertEqual(key.count("{}"), value.count("{}"), key)
            self.assertFalse(re.search(r"\\n", value), f"改行が \\n の文字のまま: {key}")


class MainWindowLanguageTest(unittest.TestCase):
    def setUp(self) -> None:
        isolation.reset()
        patcher = patch.object(main_window, "find_game_window", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(i18n.set_language, i18n.JA)

    def test_switch_needs_plugin_and_is_remembered(self) -> None:
        window = main_window.MainWindow(PluginManager())
        self.addCleanup(window.close)
        self.assertTrue(window.language_combo.isHidden(), "言語プラグインが無いのに出ている")
        window.plugins.plugins.append(load_english())
        window._rebuild_mode_buttons()
        self.assertFalse(window.language_combo.isHidden())
        self.assertEqual(window.language_combo.itemText(0), "日本語")
        window.language_combo.setCurrentIndex(window.language_combo.findData("en"))
        self.assertEqual(i18n.language(), "en")
        self.assertEqual(window.assist_btn.text(), "Start assist mode")
        self.assertEqual(window.language_combo.itemText(0), "日本語", "言語の名前まで訳した")
        self.assertEqual(i18n.load_choice(), "en")
        # 次に起動した時も英語
        again = main_window.MainWindow(PluginManager([load_english()]))
        self.addCleanup(again.close)
        self.assertEqual(again.assist_btn.text(), "Start assist mode")
        # プラグインを無効にしたら日本語に戻す
        window.plugins.disabled.add("english")
        window._rebuild_mode_buttons()
        self.assertEqual(i18n.language(), i18n.JA)
        self.assertEqual(window.assist_btn.text(), "支援モード開始")
        self.assertTrue(window.language_combo.isHidden())

    def test_other_plugins_add_translations(self) -> None:
        extra = Plugin(id="extra", name="x", translations={"en": Catalog({"ほかの画面": "Other screen"})})
        manager = PluginManager([load_english(), extra])
        lang = manager.languages()["en"]
        i18n.set_language("en", lang.catalog)
        self.assertEqual(i18n.tr("ほかの画面"), "Other screen")
        self.assertEqual(i18n.tr("閉じる"), "Close")
        self.assertEqual(list(PluginManager([extra]).languages()), [], "言語プラグインが無いのに言語が出た")
        self.assertIsInstance(Language(label="x", transform=str), Language)


if __name__ == "__main__":
    unittest.main()
