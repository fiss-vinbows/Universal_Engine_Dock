"""plugins/ フォルダのプラグインを読み込み、有効・無効を管理する。

各プラグインは plugins/<名前>/plugin.py に PLUGIN = Plugin(...) を書く。有効・無効は「プラグイン」画面で切り替え、
config/plugins.json に無効にしたものだけを保存する(新しく置いたプラグインは有効で始まる)。読み込めないプラグインは理由を残して飛ばす。

Universal Engine Dock のフォルダの外に置いたプラグインも、「ほかの置き場所」(config/plugin_folders.json)に足すと読む。
置き場所に plugin.py があればそのフォルダを1つのプラグインとして、無ければ中のフォルダを plugins/ と同じように探す。
配布物の作成(tools/build_exe.py)は plugins/ だけを見るので、ほかの置き場所のプラグインは配布物に入らない。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from .game_mode import EngineSpec, GameMode
from .paths import app_root, config_dir, plugins_dir
from .i18n import Catalog
from .plugin_api import Language, Plugin


def plugin_dir() -> Path:
    return plugins_dir()


def config_path() -> Path:
    return config_dir() / "plugins.json"


def extra_dirs_path() -> Path:
    return config_dir() / "plugin_folders.json"


def load_extra_dirs(path: Path | None = None) -> list[Path]:
    """ほかの置き場所(Universal Engine Dock のフォルダの外のプラグイン)。相対パスは本体のフォルダから見る。"""
    path = extra_dirs_path() if path is None else path
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        items = data.get("folders", [])
    except (OSError, ValueError, AttributeError):
        return []
    out: list[Path] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str) and item.strip():
            folder = Path(item)
            out.append(folder if folder.is_absolute() else app_root() / folder)
    return out


def save_extra_dirs(folders: list[Path], path: Path | None = None) -> None:
    path = Path(extra_dirs_path() if path is None else path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"folders": [Path(f).as_posix() for f in folders]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _plugin_folders(folder: Path) -> list[Path]:
    """そのフォルダの中のプラグインのフォルダ(中に plugin.py があるもの)。"""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_dir() and (p / "plugin.py").exists())


def candidate_folders(folder: Path | None = None) -> list[Path]:
    """読み込むプラグインのフォルダ。folder を指定すればその中だけ、省略すると plugins/ とほかの置き場所。"""
    if folder is not None:
        return _plugin_folders(folder)
    out = _plugin_folders(plugin_dir())
    for extra in load_extra_dirs():
        out += [extra] if (extra / "plugin.py").exists() else _plugin_folders(extra)
    return out


def discover(folder: Path | None = None) -> tuple[list[Plugin], dict[str, str]]:
    """プラグインを読み込む。(読み込めたプラグイン, {フォルダ名: 読み込めなかった理由})。"""
    found, errors = discover_folders(folder)
    return [plugin for _folder, plugin in found], errors


def discover_folders(folder: Path | None = None) -> tuple[list[tuple[Path, Plugin]], dict[str, str]]:
    """プラグインを読み込む。([(フォルダ, プラグイン)], {フォルダ名: 読み込めなかった理由})。配布物の作成でも使う
    (配布物の作成は folder に plugins/ を指定するので、ほかの置き場所は見ない)。"""
    plugins: list[tuple[Path, Plugin]] = []
    errors: dict[str, str] = {}
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for sub in candidate_folders(folder):
        name = f"uedock_plugin_{sub.name}"
        if sub.name in seen_names:  # 同じ名前のフォルダが2か所にある時は、先に見つけた方(plugins/)を使う
            errors[str(sub)] = f"同じ名前のフォルダが、ほかの置き場所にもあります: {sub}"
            continue
        seen_names.add(sub.name)
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
            for plugin in found:
                if plugin.id in seen_ids:  # 同じプラグインが2か所にある時は、先に見つけた方を使う
                    errors[str(sub)] = f"「{plugin.id}」は、ほかの置き場所でもう読み込んでいます"
                    continue
                seen_ids.add(plugin.id)
                plugins.append((sub, plugin))
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


def plugin_files(folder: Path | None = None) -> dict[str, float]:
    """置いてあるプラグインのファイル {場所: 書き換えた時刻}。読み込んだ後に足した・書き換えたかを見る用。
    folder を省略すると、plugins/ とほかの置き場所の両方を見る。"""
    out: dict[str, float] = {}
    for sub in candidate_folders(folder):
        for path in sub.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                out[path.as_posix()] = path.stat().st_mtime
            except OSError:
                pass
    return out


@dataclass
class PluginManager:
    plugins: list[Plugin] = field(default_factory=list)
    disabled: set[str] = field(default_factory=set)
    errors: dict[str, str] = field(default_factory=dict)
    # 読み込んだ時のプラグインのファイル(plugin_files)。None は plugins/ から読み込んでいない(テストなど)
    files: dict[str, float] | None = None
    folder: Path | None = None

    @classmethod
    def load(cls, folder: Path | None = None, config: Path | None = None) -> "PluginManager":
        files = plugin_files(folder)  # 読み込む前に見る(読み込み中に書き換えられても、次に見た時に気づける)
        plugins, errors = discover(folder)
        return cls(plugins, load_disabled(config), errors, files, folder)

    def changed_on_disk(self) -> bool:
        """読み込んだ後に、プラグインを足した・消した・書き換えたか(再起動すると反映される)。"""
        return self.files is not None and plugin_files(self.folder) != self.files

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
