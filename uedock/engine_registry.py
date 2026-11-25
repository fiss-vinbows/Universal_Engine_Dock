"""思考エンジンの登録と、サイドごとの選択。

選べるエンジンはゲームモードごとに
- 同梱のエンジン(GameMode.builtin_engines。ぷよは YamaNEKO、テトリスは KasugAI)
- プラグインが足したエンジン
- 利用者が「エンジンの登録」で足したエンジン(ama・Cold Clear 2 など。config/engines.json)

サイド(1P・2P・一人用)ごとに、ゲーム種別と、ゲーム種別ごとの思考エンジンを覚える(config/sides.json)。
ゲーム種別を切り替えて戻した時に、前に選んだエンジンが残る。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .game_mode import LAYOUT_SOLO, LAYOUT_VERSUS, NO_GAME, SIDES, EngineSpec, GameMode
from .paths import config_dir, engines_dir, is_frozen

if TYPE_CHECKING:
    from .plugin_loader import PluginManager

# 何も設定していない時のゲーム種別。2P は支援なしで始める(自分の側だけ支援するのが普通の使い方)
DEFAULT_GAMES = {"1p": "puyo", "2p": NO_GAME, "solo": "puyo"}


def engines_path() -> Path:
    return config_dir() / "engines.json"


def sides_path() -> Path:
    return config_dir() / "sides.json"


def bundled_engine(relative: str, dev: Path | None = None) -> Path:
    """同梱する思考エンジンのファイルの場所。配布物では UniversalEngineDock.exe の隣の engines/<relative>。

    ソースから起動した時は、engines/ に無ければ dev(並べて置いたエンジンのリポジトリのビルド結果)を使う。
    """
    path = engines_dir() / relative
    if path.exists() or is_frozen() or dev is None:
        return path
    return dev


@dataclass
class EngineEntry:
    """利用者が登録した思考エンジン。"""

    game: str  # ゲームモードの ID
    name: str
    exe: str
    args: list[str] = field(default_factory=list)
    weights: str = ""

    def spec(self) -> EngineSpec:
        return EngineSpec(self.name, Path(self.exe), tuple(self.args), Path(self.weights) if self.weights else None)


@dataclass
class Registry:
    engines: list[EngineEntry] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> "Registry":
        path = engines_path() if path is None else path
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            items = data.get("engines", [])
        except (OSError, ValueError, AttributeError):
            return cls()
        engines = [
            EngineEntry(str(e["game"]), str(e["name"]), str(e["exe"]), [str(a) for a in e.get("args", [])],
                        str(e.get("weights", "")))
            for e in items
            if isinstance(e, dict) and e.get("game") and e.get("name") and e.get("exe")
        ]
        return cls(engines)

    def save(self, path: Path | None = None) -> None:
        path = Path(engines_path() if path is None else path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"engines": [vars(e) for e in self.engines]}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def for_game(self, game: str) -> list[EngineEntry]:
        return [e for e in self.engines if e.game == game]


def available_engines(mode: GameMode, registry: Registry | None = None,
                      plugins: "PluginManager | None" = None) -> dict[str, EngineSpec]:
    """そのゲームモードで選べるエンジン {名前: EngineSpec}。同梱 → プラグイン → 登録の順(同じ名前は先のものが優先)。"""
    registry = Registry.load() if registry is None else registry
    out: dict[str, EngineSpec] = {e.name: e for e in mode.builtin_engines}
    if plugins is not None:
        for e in plugins.engines(mode.id):
            out.setdefault(e.name, e)
    for entry in registry.for_game(mode.id):
        out.setdefault(entry.name, entry.spec())
    return out


@dataclass
class SideSelection:
    """画面の配置(対戦・一人用)と、サイドごとのゲーム種別・思考エンジンの選択。"""

    layout: str = LAYOUT_VERSUS
    games: dict[str, str] = field(default_factory=dict)  # {サイド: ゲームモードの ID(NO_GAME は支援なし)}
    engines: dict[str, dict[str, str]] = field(default_factory=dict)  # {サイド: {ゲームモードの ID: エンジンの名前}}

    @classmethod
    def load(cls, path: Path | None = None) -> "SideSelection":
        path = sides_path() if path is None else path
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            sides = data.get("sides", {})
            layout = data.get("layout", LAYOUT_VERSUS)
        except (OSError, ValueError, AttributeError):
            return cls()
        out = cls(layout if layout in (LAYOUT_VERSUS, LAYOUT_SOLO) else LAYOUT_VERSUS)
        if not isinstance(sides, dict):
            return out
        for side in SIDES:
            item = sides.get(side)
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("game"), str):
                out.games[side] = item["game"]
            engines = item.get("engines")
            if isinstance(engines, dict):
                out.engines[side] = {str(k): str(v) for k, v in engines.items()}
        return out

    def save(self, path: Path | None = None) -> None:
        path = Path(sides_path() if path is None else path)
        path.parent.mkdir(parents=True, exist_ok=True)
        sides = {side: {"game": self.game(side), "engines": self.engines.get(side, {})} for side in SIDES}
        data = {"layout": self.layout, "sides": sides}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def game(self, side: str) -> str:
        return self.games.get(side, DEFAULT_GAMES.get(side, NO_GAME))

    def set_game(self, side: str, game: str) -> None:
        self.games[side] = game

    def engine_name(self, side: str, mode: GameMode) -> str:
        """そのサイドで、そのゲームモードに選んでいるエンジンの名前(選んでいなければ同梱の既定)。"""
        return self.engines.get(side, {}).get(mode.id, mode.default_engine())

    def set_engine(self, side: str, game: str, name: str) -> None:
        self.engines.setdefault(side, {})[game] = name

    def resolve(self, side: str, modes: dict[str, GameMode], registry: Registry | None = None,
                plugins: "PluginManager | None" = None) -> tuple[GameMode | None, EngineSpec | None]:
        """そのサイドの(ゲームモード, 思考エンジン)。支援なし・ゲームモードが無くなっていれば (None, None)。

        選んだエンジンが無くなっていれば同梱の既定に戻す。それも無ければエンジンは None。
        """
        mode = modes.get(self.game(side))
        if mode is None:
            return None, None
        engines = available_engines(mode, registry, plugins)
        name = self.engine_name(side, mode)
        return mode, engines.get(name) or engines.get(mode.default_engine())
