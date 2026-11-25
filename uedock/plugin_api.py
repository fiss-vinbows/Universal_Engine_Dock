"""Universal Engine Dock のプラグイン。

プラグインは plugins/<名前>/plugin.py に PLUGIN = Plugin(...)(複数なら PLUGINS = [...])を書いて置く
(uedock/plugin_loader.py が読む)。有効・無効はメイン画面の「プラグイン」で切り替える。

プラグインができること:
- game_modes: ゲームモードを足す(uedock/game_mode.py の GameMode。スワップ・フュージョンなど)
- engines: 思考エンジンを足す({ゲームモードの ID: [EngineSpec]})。メイン画面の思考エンジンの一覧に並ぶ
- languages: 表示の言語を足す(情報パネルの行ごとの置き換え関数と、画面の文言の訳。メイン画面の「表示の言語」で選ぶ)
- translations: ほかのプラグインの表示の言語に、自分の画面の文言の訳を足す
- features: 機能の許可。ゲームモードやほかの部品が has_feature() で確かめる名前の集まり
  (例: "debug_log"、"obs_overlay")。名前は使う側が決める
- extension: メイン画面と支援モードに処理を足す(uedock/extension.py)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .extension import Extension

from .game_mode import EngineSpec, GameMode
from .i18n import Catalog

# 共通の枠が使う機能の名前
FEATURE_DEBUG_LOG = "debug_log"  # 認識結果と提案をログに出力する切り替えを出す
FEATURE_OBS_OVERLAY = "obs_overlay"  # 支援手を OBS のブラウザソースに出す切り替えを出す


@dataclass(frozen=True)
class Language:
    label: str  # メイン画面の「表示の言語」に出す名前
    transform: Callable[[str], str]  # 日本語のぷよの情報パネルの1行 → この表記の1行
    word_break: bool = False  # 折り返しを空白で行う(英語のように単語を切らない)
    # 画面の文言の訳(uedock/i18n.py)。無ければ画面は日本語のままで、ぷよの情報パネルの表記だけ変わる
    catalog: Catalog | None = None


@dataclass(frozen=True)
class Plugin:
    id: str
    name: str
    description: str = ""
    # 配布してよいか。False のプラグインは配布物(exe)に入れない(別に配る)
    distributable: bool = True
    features: frozenset[str] = frozenset()
    game_modes: tuple[GameMode, ...] = ()
    engines: dict[str, tuple[EngineSpec, ...]] = field(default_factory=dict)
    languages: dict[str, Language] = field(default_factory=dict)
    # ほかのプラグインが出す表示の言語に足す訳(言語のキー → 訳)。その言語が無ければ使わない
    translations: dict[str, Catalog] = field(default_factory=dict)
    # メイン画面と支援モードに処理を足す拡張(uedock/extension.py の Extension)を作る関数
    extension: Callable[[], "Extension"] | None = None
