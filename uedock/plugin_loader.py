"""plugins/ フォルダのプラグインを読み込み、有効・無効を管理する。

各プラグインは plugins/<名前>/plugin.py に PLUGIN = Plugin(...) を書く。有効・無効は「プラグイン」画面で切り替え、
config/plugins.json に無効にしたものだけを保存する(新しく置いたプラグインは有効で始まる)。読み込めないプラグインは理由を残して飛ばす。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from .game_mode import EngineSpec, GameMode
from .paths import config_dir, plugins_dir
from .i18n import Catalog
from .plugin_api import Language, Plugin


def plugin_dir() -> Path:
    return plugins_dir()


def config_path() -> Path:
    return config_dir() / "plugins.json"


def discover(folder: Path | None = None) -> tuple[list[Plugin], dict[str, str]]:
    """プラグインを読み込む。(読み込めたプラグイン, {フォルダ名: 読み込めなかった理由})。"""
    found, errors = discover_folders(folder)
    return [plugin for _folder, plugin in found], errors


def discover_folders(folder: Path | None = None) -> tuple[list[tuple[Path, Plugin]], dict[str, str]]:
    """プラグインを読み込む。([(フォルダ, プラグイン)], {フォルダ名: 読み込めなかった理由})。配布物の作成でも使う。"""
    folder = plugin_dir() if folder is None else folder
    plugins: list[tuple[Path, Plugin]] = []
    errors: dict[str, str] = {}
    if not folder.is_dir():
        return plugins, errors
    for sub in sorted(p for p in folder.iterdir() if p.is_dir() and (p / "plugin.py").exists()):
        name = f"uedock_plugin_{sub.name}"
        try:
            spec = importlib.util.spec_from_file_location(name, sub / "plugin.py", submodule_search_locations=[str(sub)])
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            # 1つのフォルダに複数のプラグインを置く時は PLUGINS = [...]
            found = getattr(module, "PLUGINS", None) or [getattr(module, "PLUGIN")]
            for plugin in found:
                if not isinstance(plugin, Plugin):
                    raise TypeError("PLUGIN が Plugin ではありません")
            plugins.extend((sub, plugin) for plugin in found)
        except Exception as exc:  # noqa: BLE001  1つのプラグインの不具合で Universal Engine Dock を止めない
            errors[sub.name] = f"{type(exc).__name__}: {exc}"
    return plugins, errors


def load_disabled(path: Path | None = None) -> set[str]:
    path = config_path() if path is None else path
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {str(x) for x in data.get("disabled", [])}
    except (OSError, ValueError, AttributeError, TypeError):
        return set()


def save_disabled(disabled: set[str], path: Path | None = None) -> None:
    path = Path(config_path() if path is None else path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"disabled": sorted(disabled)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass
class PluginManager:
    plugins: list[Plugin] = field(default_factory=list)
    disabled: set[str] = field(default_factory=set)
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, folder: Path | None = None, config: Path | None = None) -> "PluginManager":
        plugins, errors = discover(folder)
        return cls(plugins, load_disabled(config), errors)

    @property
    def enabled(self) -> list[Plugin]:
        return [p for p in self.plugins if p.id not in self.disabled]

    def has_feature(self, name: str) -> bool:
        """有効なプラグインのどれかが、その機能を許可しているか。"""
        return any(name in p.features for p in self.enabled)

    def make_extensions(self) -> dict:
        """読み込んだプラグインの拡張(uedock/extension.py)を作る({プラグインの ID: 拡張})。有効・無効にかかわらず作り、
        呼ぶ側が有効なものだけを使う。作れなかったものは理由を errors に残して飛ばす。"""
        out = {}
        for p in self.plugins:
            if p.extension is None:
                continue
            try:
                out[p.id] = p.extension()
            except Exception as exc:  # noqa: BLE001  1つのプラグインの不具合で Universal Engine Dock を止めない
                self.errors[p.id] = f"{type(exc).__name__}: {exc}"
        return out

    def game_modes(self) -> list[GameMode]:
        """有効なプラグインが足したゲームモード。"""
        return [mode for p in self.enabled for mode in p.game_modes]

    def engines(self, game: str) -> list[EngineSpec]:
        """有効なプラグインが、そのゲームモードに足した思考エンジン。"""
        return [e for p in self.enabled for e in p.engines.get(game, ())]

    def languages(self) -> dict[str, Language]:
        """有効なプラグインの表示の言語。ほかのプラグインの足した訳(Plugin.translations)は、その言語の訳に合わせる
。"""
        out: dict[str, Language] = {}
        for p in self.enabled:
            out.update(p.languages)
        for key, lang in list(out.items()):
            extra = [p.translations[key] for p in self.enabled if key in p.translations]
            if not extra or lang.catalog is None:
                continue
            try:  # 元の訳と同じ種類で作る(英語表示プラグインはテンプレの名前を初めて訳す時に読む)
                merged = type(lang.catalog)()
            except TypeError:
                merged = Catalog()
            merged.update(lang.catalog)
            for catalog in extra:
                merged.update(catalog)
            out[key] = replace(lang, catalog=merged)
        return out


# メイン画面が読み込んだプラグイン。ゲームモードの画面(サイドの設定項目など)が、読み込み直さずに同じものを使う
_current: PluginManager | None = None


def set_current(manager: PluginManager) -> None:
    global _current
    _current = manager


def current() -> PluginManager:
    """メイン画面が読み込んだプラグイン(まだ無ければ読み込む)。"""
    global _current
    if _current is None:
        _current = PluginManager.load()
    return _current
