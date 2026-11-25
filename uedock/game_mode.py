"""ゲームモードの登録口。

Universal Engine Dock は「ぷよ」「テトリス」を同じ形のゲームモードとして扱う。標準の2つは uedock/games/ にあり、
プラグインも同じ GameMode を登録できる(スワップ・フュージョンは、この口を使うプラグインとして後から足す)。

1つのゲームモードが持つもの:
- 名前と、同梱する思考エンジン
- 支援モード(認識 → 追跡 → 思考 → オーバーレイ)の始め方
- サイドごとの設定項目(定跡・開幕テンプレなど)の画面
- シミュレーター(練習画面)

共通の枠(メイン画面・ウィンドウ検出・エンジン登録)は、ゲームモードの中身を知らずにこの口だけを使う。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from PyQt6 import QtWidgets

    from .capture.game_window import GameWindow
    from .plugin_loader import PluginManager

# サイド(枠)。対戦は 1P・2P、一人用(とことん等)は画面の配置が違うので別の枠にする
SIDES = ("1p", "2p", "solo")
SIDE_LABELS = {"1p": "1P", "2p": "2P", "solo": "一人用"}
# 画面の配置
LAYOUT_VERSUS = "versus"
LAYOUT_SOLO = "solo"
LAYOUT_SIDES = {LAYOUT_VERSUS: ("1p", "2p"), LAYOUT_SOLO: ("solo",)}
# ゲーム種別の「支援なし」
NO_GAME = ""
NO_GAME_LABEL = "支援なし"


@dataclass(frozen=True)
class EngineSpec:
    """思考エンジン1つ。"""

    name: str  # 一覧に出す名前
    exe: Path
    args: tuple[str, ...] = ()  # 起動引数
    weights: Path | None = None  # 評価の重みのファイル(渡し方はゲームモードが決める)

    def exists(self) -> bool:
        return self.exe.exists()


@dataclass(frozen=True)
class SimulatorEntry:
    """メイン画面に出すシミュレーターのボタン1つ。"""

    label: str  # ボタンの文字(「ぷよのシミュレーターを開く」など)
    open: Callable[["QtWidgets.QWidget | None"], "QtWidgets.QWidget"]  # ウィンドウを作って返す(表示は呼んだ側)


@dataclass(frozen=True)
class SideSetup:
    """支援モードを始める時の、1つのサイドの設定。"""

    side: str  # "1p" / "2p" / "solo"
    engine: EngineSpec | None  # 選んだ思考エンジン(見つからなければ None)


@dataclass(frozen=True)
class AssistRequest:
    """支援モードを始める時にゲームモードへ渡すもの。sides はそのゲームモードを選んだサイドだけ。

    opponents は、対戦で相手のサイドが選んでいるゲームモードの ID(支援なしは NO_GAME)。v0.1 では相手が別種なら
    凝視しないので、ゲームモードは相手が自分と同じ ID の時だけ相手の盤面を読む。
    """

    layout: str  # LAYOUT_VERSUS / LAYOUT_SOLO
    sides: tuple[SideSetup, ...]
    game_window: "GameWindow | None"  # 見つかったゲームのウィンドウ(無ければ手動キャリブレーションを使う)
    plugins: "PluginManager"
    opponents: Mapping[str, str] = field(default_factory=dict)  # {サイド: 相手のゲームモードの ID}
    debug_log: bool = False  # 認識結果と提案をログに出力する(ログ出力プラグインが有効で、利用者がオンにした時)
    # 同時に始める支援モードのうち最初の1つか。1P がぷよ・2P がテトリスのように2つのゲームモードが同時に動く時、
    # ゲームのタイトルバーに置く終了ボタンは最初の1つだけが出す(重ならないように)。どれか1つが止まれば、
    # メイン画面が残りも止める
    primary: bool = True
    # 有効なプラグインの拡張(uedock/extension.py)。観測ループを作る時などに呼ぶ
    extensions: tuple = ()


class AssistSession(Protocol):
    """支援モード1つ(1つのゲームモードが受け持つサイドの分)。始めるのは2段階:

    1. GameMode.start_assist(request) が思考エンジンを起動して AssistSession を返す。時間がかかることがあり、
       この間メイン画面は見えている(起動中の表示を出している)。始められなければ例外を出す
    2. メイン画面を隠した後で begin() を呼ぶ。ここで画面を読み、オーバーレイを出し、観測を始める
       (メイン画面がゲームに重なったままだと、盤面の位置合わせに写り込む)

    その後、メイン画面は止めることと、動いているかを聞くことだけをする。利用者が終了バッジで止めた時や、
    ゲームのウィンドウが動いて自分で止まった時は、is_running() が False になる。
    """

    def begin(self) -> None: ...

    def stop(self) -> None: ...

    def is_running(self) -> bool: ...


@dataclass(frozen=True)
class GameMode:
    id: str  # "puyo" / "tetris"。設定ファイルに残すので変えない
    name: str  # 画面に出す名前
    # 登録できる思考エンジンの説明(「エンジンの登録」画面に出す。例: 「TBP 互換」)
    engine_protocol: str = ""
    # 同梱する思考エンジン(先頭が既定)。実行ファイルが無いものは一覧に「(見つかりません)」と出す
    builtin_engines: tuple[EngineSpec, ...] = ()
    # 支援モードを始める。None はまだ用意が無い(メイン画面は始められない理由を出す)
    start_assist: Callable[[AssistRequest], AssistSession] | None = None
    # サイドごとの設定項目(定跡・開幕テンプレなど)の画面を作る。引数はサイドと親。
    # 画面が set_engine(名前) を持っていれば、そのサイドの思考エンジンが変わるたびにメイン画面が呼ぶ
    # (エンジンによって使えない項目を暗転させる用)。設定はゲームモードが自分で保存し、start_assist で読む
    side_panel: Callable[[str, "QtWidgets.QWidget | None"], "QtWidgets.QWidget"] | None = None
    # 手動キャリブレーション(Switch のキャプチャボード用)を開く。引数は画面の配置(LAYOUT_*)と親。
    # ゲームのウィンドウが見つからない時は、これで決めた位置を使う
    calibrate: Callable[[str, "QtWidgets.QWidget | None"], None] | None = None
    simulators: tuple[SimulatorEntry, ...] = ()

    def default_engine(self) -> str:
        return self.builtin_engines[0].name if self.builtin_engines else ""


def builtin_modes() -> list[GameMode]:
    """標準のゲームモード(ぷよ・テトリス)。"""
    from .games import puyo, tetris

    return [puyo.MODE, tetris.MODE]


def all_modes(plugins: "PluginManager | None" = None) -> dict[str, GameMode]:
    """選べるゲームモード {ID: GameMode}。標準のもの、続いて有効なプラグインが足したもの(同じ ID は標準が優先)。"""
    out = {mode.id: mode for mode in builtin_modes()}
    if plugins is not None:
        for mode in plugins.game_modes():
            out.setdefault(mode.id, mode)
    return out
