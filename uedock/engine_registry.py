"""思考エンジンの登録と、サイドごとの選択。

選べるエンジンはゲームモードごとに
- 「エンジンの登録」にあるエンジン(config/engines.json)
- プラグインが足したエンジン

同梱のエンジン(GameMode.builtin_engines。ぷよは YamaNEKO、テトリスは KasugAI)も、初めて起動した時に登録へ入れる
(Registry.add_bundled)。登録にあるので、エンジンの新しい版が出た時は、同じ名前のまま実行ファイルを差し替えられる。
入れた印(bundled)を残すので、利用者が消した同梱のエンジンは、次に起動しても戻さない。
同梱のエンジンの場所は本体のフォルダからの相対パスで残す。config/ を新しい版のフォルダへ写すと、新しい版の同梱のエンジンを使う。

サイド(1P・2P・一人用)ごとに、ゲーム種別と、ゲーム種別ごとの思考エンジンを覚える(config/sides.json)。
ゲーム種別を切り替えて戻した時に、前に選んだエンジンが残る。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .game_mode import LAYOUT_SOLO, LAYOUT_VERSUS, NO_GAME, SIDES, EngineSpec, GameMode
from .paths import app_root, config_dir, engines_dir, is_frozen

if TYPE_CHECKING:
    from collections.abc import Iterable

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


def resolve_path(text: str) -> Path:
    """登録に残したパス。相対パスは本体のフォルダ(app_root())から見る。"""
    path = Path(text)
    return path if path.is_absolute() else app_root() / path


def portable_path(path: Path) -> str:
    """登録に残すパス。本体のフォルダの中なら相対パスにする(フォルダを動かしても、新しい版へ config/ を写しても使える)。"""
    try:
        return Path(path).resolve().relative_to(app_root().resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def bundled_key(game: str, name: str) -> str:
    """登録へ入れた同梱のエンジンの印(engines.json の bundled)。"""
    return f"{game}/{name}"


@dataclass
class EngineEntry:
    """「エンジンの登録」にある思考エンジン1つ。"""

    game: str  # ゲームモードの ID
    name: str
    exe: str  # 相対パスは本体のフォルダから見る(resolve_path)
    args: list[str] = field(default_factory=list)
    weights: str = ""

    @classmethod
    def from_spec(cls, game: str, spec: EngineSpec) -> "EngineEntry":
        """同梱のエンジンを登録の形にする。"""
        return cls(game, spec.name, portable_path(spec.exe), list(spec.args),
                   portable_path(spec.weights) if spec.weights else "")

    def spec(self) -> EngineSpec:
        return EngineSpec(self.name, resolve_path(self.exe), tuple(self.args),
                          resolve_path(self.weights) if self.weights else None)


@dataclass
class Registry:
    engines: list[EngineEntry] = field(default_factory=list)
    # 登録へ入れたことのある同梱のエンジン(bundled_key)。消された同梱のエンジンを入れ直さないための印
    bundled: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> "Registry":
        path = engines_path() if path is None else path
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            items = data.get("engines", [])
            bundled = data.get("bundled", [])
        except (OSError, ValueError, AttributeError):
            return cls()
        engines = [
            EngineEntry(str(e["game"]), str(e["name"]), str(e["exe"]), [str(a) for a in e.get("args", [])],
                        str(e.get("weights", "")))
            for e in items
            if isinstance(e, dict) and e.get("game") and e.get("name") and e.get("exe")
        ]
        return cls(engines, [str(k) for k in bundled] if isinstance(bundled, list) else [])

    def save(self, path: Path | None = None) -> None:
        path = Path(engines_path() if path is None else path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"engines": [vars(e) for e in self.engines], "bundled": self.bundled}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def for_game(self, game: str) -> list[EngineEntry]:
        return [e for e in self.engines if e.game == game]

    def find(self, game: str, name: str) -> EngineEntry | None:
        return next((e for e in self.engines if e.game == game and e.name == name), None)

    def has_added(self, game: str, name: str) -> bool:
        return bundled_key(game, name) in self.bundled

    def add_bundled(self, modes: "Iterable[GameMode]") -> bool:
        """まだ入れていない同梱のエンジンを、そのゲーム種別の登録の先頭に入れる。変えたら True(呼んだ側が保存する)。

        同じ名前が登録にあれば、そちらを残す(利用者が先に新しい版を登録していた時)。
        """
        changed = False
        for mode in modes:
            for spec in reversed(mode.builtin_engines):  # 先頭に入れていくので逆順に
                if self.has_added(mode.id, spec.name):
                    continue
                self.bundled.append(bundled_key(mode.id, spec.name))
                changed = True
                if self.find(mode.id, spec.name) is None:
                    first = next((i for i, e in enumerate(self.engines) if e.game == mode.id), len(self.engines))
                    self.engines.insert(first, EngineEntry.from_spec(mode.id, spec))
        return changed


def available_engines(mode: GameMode, registry: Registry | None = None,
                      plugins: "PluginManager | None" = None) -> dict[str, EngineSpec]:
    """そのゲームモードで選べるエンジン {名前: EngineSpec}。登録 → プラグインの順(同じ名前は先のものが優先)。

    登録へまだ入れていない同梱のエンジン(add_bundled を呼ぶ前)は、先頭に出す。
    """
    registry = Registry.load() if registry is None else registry
    out: dict[str, EngineSpec] = {e.name: e for e in mode.builtin_engines if not registry.has_added(mode.id, e.name)}
    for entry in registry.for_game(mode.id):
        out.setdefault(entry.name, entry.spec())
    if plugins is not None:
        for e in plugins.engines(mode.id):
            out.setdefault(e.name, e)
    return out


def registered_engine(mode: GameMode, name: str) -> EngineSpec | None:
    """登録にある、その名前のエンジン。登録へまだ入れていなければ同梱のもの。消されていれば None。

    シミュレーターなど、サイドの選択を通らずに同梱のエンジンを使う所が、差し替えた版を使うためのもの。
    """
    registry = Registry.load()
    entry = registry.find(mode.id, name)
    if entry is not None:
        return entry.spec()
    if registry.has_added(mode.id, name):
        return None
    return next((e for e in mode.builtin_engines if e.name == name), None)


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

        選んだエンジンが無くなっていれば同梱の既定に、それも登録から消されていれば一覧の先頭に戻す。
        選べるものが無ければエンジンは None。
        """
        mode = modes.get(self.game(side))
        if mode is None:
            return None, None
        engines = available_engines(mode, registry, plugins)
        name = self.engine_name(side, mode)
        return mode, engines.get(name) or engines.get(mode.default_engine()) or next(iter(engines.values()), None)
