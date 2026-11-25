"""テトリスの思考エンジンの選択。共通の登録(uedock/engine_registry.py)を見る。

選べるエンジンは、同梱の KasugAI、プラグインが足したエンジン、利用者が「エンジンの登録」で足したエンジン(Cold Clear 2 など)。
登録と、サイドごとの選択の保存は共通基盤が受け持つ。ここは、テトリスのモジュール(シミュレーターなど)が
今までどおり「選べるエンジンの一覧」「そのサイドで選んでいるエンジン」を聞けるようにするだけ。
"""

from __future__ import annotations

from pathlib import Path

from uedock import engine_registry
from uedock.game_mode import EngineSpec
from uedock.plugin_loader import current as current_plugins

GAME_ID = "tetris"
DEFAULT_ENGINE = "KasugAI"
NO_ENGINE = "使わない"
SIDES = ("1p", "2p", "solo")
Engine = tuple[Path, Path | None]


def _mode():  # noqa: ANN202
    from uedock.games.tetris import MODE

    return MODE


def _pair(spec: EngineSpec) -> Engine:
    return spec.exe, spec.weights


def available_engines() -> dict[str, Engine]:
    """選べるエンジン {名前: (実行ファイル, 重み)}。同梱の KasugAI が先頭。"""
    specs = engine_registry.available_engines(_mode(), plugins=current_plugins())
    return {name: _pair(spec) for name, spec in specs.items()}


def selected_engine(side: str = "1p") -> tuple[str, Engine | None]:
    """そのサイドで選んでいるエンジンの(名前, ColdClearClient に渡すもの)。

    そのサイドのゲーム種別がテトリスでない時(支援なし・ぷよ)は (NO_ENGINE, None)。選んだものが無くなっていれば KasugAI。
    """
    selection = engine_registry.SideSelection.load()
    if selection.game(side) != GAME_ID:
        return NO_ENGINE, None
    _mode_found, spec = selection.resolve(side, {GAME_ID: _mode()}, plugins=current_plugins())
    return (NO_ENGINE, None) if spec is None else (spec.name, _pair(spec))


def preferred_engine_name() -> str:
    """シミュレーターで使うエンジンの名前: 1P でテトリスに選んでいるもの(テトリスを選んでいなくても、前に選んだものが残っている)。"""
    return engine_registry.SideSelection.load().engine_name("1p", _mode())
