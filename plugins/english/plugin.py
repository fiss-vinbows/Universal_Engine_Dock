"""英語表示プラグイン。

有効にすると、メイン画面に「表示の言語 (Language)」が出て、日本語と英語を切り替えられる。
切り替えるのは Universal Engine Dock の画面すべて(メイン画面・設定・案内・シミュレーター)と、ゲームの上の表示
(情報パネル・ボタン。OBS 出力にも同じものが出る)。ログ・設定ファイルは日本語のまま。

- 画面の文言の訳は catalog_en.py(日本語の文言 → 英語。uedock/i18n.py が画面に出す直前に置き換える)
- ぷよの情報パネルは、以前からある行ごとの英語表記(uedock/games/puyo/overlay/panel_text_en.py)を使う
- テトリスのテンプレの名前は、テンプレのデータの英語名(name_en)を使う。図の区切りの名前は語句で置き換える
"""

from uedock.games.puyo.overlay.panel_text_en import to_english
from uedock.i18n import Catalog
from uedock.plugin_api import Language, Plugin

try:
    from .catalog_en import ENTRIES
except ImportError:  # plugin.py だけを読み込んだ時(パッケージとして読まれていない)は、隣のファイルを直接読む
    import importlib.util as _util
    from pathlib import Path as _Path

    _spec = _util.spec_from_file_location("_catalog_en_" + _Path(__file__).parent.name, _Path(__file__).with_name("catalog_en.py"))
    _module = _util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    ENTRIES = _module.ENTRIES

# 訳の一覧に無い文言の中で置き換える語句(テトリスのテンプレの区切りの名前など)
PHRASES: dict[str, str] = {
    "巡目": " bag",
    "の場合": " case",
    "理想形": "ideal",
    "標準形": "standard",
    "妥協形": "compromise",
    "妥協系": "compromise",
    "妥協ルート": "compromise route",
    "通常ルート": "normal route",
    "通常形": "normal",
    "基本形": "basic",
    "基本系": "basic",
    "別パターン": "alternate pattern",
    "パターン確定": " pattern fixed",
    "パターン": " pattern",
    "パフェルート": "PC route",
    "パフェ狙い": "aiming for PC",
    "パフェ": "PC",
    "別形": " (alt)",
    "早の場合": " early case",
    "左置き": " placed left",
    "とミノ順": " and piece order",
    "組み方": "how to build",
    "ハイド形": "hidden",
    "インペリアルクロス": "Imperial Cross",
    "TSTドネイト後": "after TST donation",
    "TSTドネイト": "TST donation",
    "レベル": "level ",
    "くるま": "Kuruma ",
    "ペリカン": "Pelican ",
    "ダブルハート": "Double Heart ",
    "トラック": "Truck ",
    "ライム": "Lime ",
    "ドリーム": "Dream ",
    "開幕テンプレ": "Opener template",
    # テトリスの情報パネルの「提案」の2行目(HOLD と消し方を空白でつないだもの)
    "HOLDして": "Hold, then",
    "4ライン消し(テトリス)": "4-line clear (Tetris)",
    "ライン消し": "-line clear",
    "パーフェクトクリア": "Perfect Clear",
    "Tスピンシングル": "T-Spin Single",
    "Tスピンダブル": "T-Spin Double",
    "Tスピントリプル": "T-Spin Triple",
    "Tスピンミニ": "T-Spin Mini",
    "Tスピン": "T-Spin",
    "ライン)": " lines)",
    # シミュレーター・練習画面の、語句をつないだ行(状態の行・キーの一覧・操作手順)
    "思考中": "thinking",
    "置く:": "Place:",
    "検討:": "Analyze:",
    "推奨:": "Recommended:",
    "操作手順:": "Steps:",
    "手数:": "Moves:",
    "点数:": "Score:",
    "最大連鎖:": "Max chain:",
    "配列:": "Sequence:",
    "配ぷよ:": "Sequence:",
    "コントローラー:": "Controller:",
    "接続中": "connected",
    "未接続": "not connected",
    "：": ": ",
    "～": "-",
    "・": " / ",
}


def _template_names() -> dict[str, str]:
    """テトリスのテンプレの和名 → 英語名(テンプレのデータにある name_en)。"""
    from uedock.games.tetris.engine import openers

    names: dict[str, str] = {}
    for group in (openers.OPENER_TEMPLATES, openers.EDUCATION_TEMPLATES, openers.TD_EXTRA_TEMPLATES, openers.REN_TEMPLATES):
        for template in group:
            names[template.name_ja] = template.name_en
    return names


class EnglishCatalog(Catalog):
    """テンプレの名前は、初めて訳す時に読む(起動を遅くしない。テンプレのデータの読み込みに 0.2 秒ほどかかる)。"""

    _names_loaded = False

    def _prepare(self) -> None:
        if not self._names_loaded:
            self._names_loaded = True
            try:
                for ja, en in _template_names().items():
                    self.entries.setdefault(ja, en)
                    self.phrases.setdefault(ja, en)  # 「開幕テンプレ はちみつ砲 2巡目」のような行の中でも置き換える
            except Exception:  # noqa: BLE001  テンプレのデータが読めなくても、ほかの訳は使う
                pass
            self._ready = False
        super()._prepare()


# 訳の一覧(catalog_en.py。ソースから集めた文言)のほかに足す訳
EXTRA_ENTRIES: dict[str, str] = {
    "表示の言語 (Language):": "Language:",
    "画面の文言と、ゲームの上の表示(情報パネルなど)の言語を切り替えます。ログ・設定ファイルは日本語のままです":
        "Switches the language of the screens and of the overlays on the game (info panels etc.). Logs and settings files stay in Japanese",
    "英語表示 (English)": "English",
    "メイン画面に「表示の言語 (Language)」を出し、Universal Engine Dock の画面とゲームの上の表示を英語に切り替えられるようにします":
        'Adds "Language" to the main window so the Universal Engine Dock screens and the overlays on the game can be switched to English',
    "開幕テンプレ": "Opener template",
}

CATALOG = EnglishCatalog(entries={**ENTRIES, **EXTRA_ENTRIES}, phrases=PHRASES)

PLUGIN = Plugin(
    id="english",
    name="英語表示 (English)",
    description="メイン画面に「表示の言語 (Language)」を出し、Universal Engine Dock の画面とゲームの上の表示を英語に切り替えられるようにします",
    languages={"en": Language(label="English", transform=to_english, word_break=True, catalog=CATALOG)},
)
