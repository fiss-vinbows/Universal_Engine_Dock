"""支援モードを始める時の段取り(画面を持たない部分)。

サイドごとの選択(ゲーム種別・思考エンジン)を、ゲームモードごとの AssistRequest にまとめる。
1P がぷよ・2P がテトリスなら、ぷよのゲームモードに 1P だけ、テトリスのゲームモードに 2P だけを渡す。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .engine_registry import Registry, SideSelection
from .game_mode import LAYOUT_SIDES, LAYOUT_VERSUS, SIDE_LABELS, AssistRequest, GameMode, SideSetup

if TYPE_CHECKING:
    from .capture.game_window import GameWindow
    from .plugin_loader import PluginManager


def build_requests(
    selection: SideSelection, modes: dict[str, GameMode], registry: Registry, plugins: "PluginManager",
    game_window: "GameWindow | None", debug_log: bool = False, extensions: tuple = (),
) -> tuple[list[tuple[GameMode, AssistRequest]], str | None]:
    """今の選択で始める支援モード [(ゲームモード, 渡すもの)] と、始められない理由(始められるなら None)。

    extensions は有効なプラグインの拡張(uedock/extension.py)。そのまま支援モードへ渡す。
    """
    sides = LAYOUT_SIDES[selection.layout]
    grouped: dict[str, list[SideSetup]] = {}
    for side in sides:
        mode, engine = selection.resolve(side, modes, registry, plugins)
        if mode is None:
            continue
        label = SIDE_LABELS[side]
        if mode.start_assist is None:
            return [], f"{mode.name}の支援モードは、まだ用意されていません({label})"
        if engine is None:
            return [], f"{label} の思考エンジンが選ばれていません"
        if not engine.exists():
            return [], f"{label} の思考エンジン「{engine.name}」の実行ファイルが見つかりません: {engine.exe}"
        grouped.setdefault(mode.id, []).append(SideSetup(side, engine))
    if not grouped:
        return [], "支援するサイドがありません。ゲーム種別を選んでください"
    opponents: dict[str, str] = {}
    if selection.layout == LAYOUT_VERSUS:
        opponents = {"1p": selection.game("2p"), "2p": selection.game("1p")}
    out = []
    for game, setups in grouped.items():
        request = AssistRequest(
            layout=selection.layout,
            sides=tuple(setups),
            game_window=game_window,
            plugins=plugins,
            opponents={s.side: opponents[s.side] for s in setups if s.side in opponents},
            debug_log=debug_log,
            primary=not out,
            extensions=tuple(extensions),
        )
        out.append((modes[game], request))
    return out, None
