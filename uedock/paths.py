"""実行ファイル(PyInstaller)として配布する場合と、ソースから起動する場合のパスの違いを吸収する。

・app_root(): 利用者が触るファイル(設定 config/、プラグイン plugins/、思考エンジン engines/、ログ)を置く場所。
    - 実行ファイル形式: UniversalEngineDock.exe と同じフォルダ
    - ソースから起動:   リポジトリの直下
・resource_root(): 同梱する読み取り専用データ(テンプレート画像など)の場所。
    - 実行ファイル形式: PyInstaller が展開した一時フォルダ(sys._MEIPASS)
    - ソースから起動:   リポジトリの直下
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "Universal Engine Dock"
APP_TITLE = "Universal Engine Dock"

_SOURCE_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _SOURCE_ROOT


def resource_root() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_root()))
    return _SOURCE_ROOT


# テストで本番の設定・プラグイン・エンジンを使わないための差し替え(tests/config_isolation.py)。
# app_root() ごと差し替えると同梱の素材(resource_root())まで見えなくなるので、利用者が書き換える場所だけを替える
_CONFIG_DIR: Path | None = None
_PLUGINS_DIR: Path | None = None
_ENGINES_DIR: Path | None = None


def config_dir() -> Path:
    """設定の置き場所(config/)。"""
    return app_root() / "config" if _CONFIG_DIR is None else _CONFIG_DIR


def plugins_dir() -> Path:
    """プラグインの置き場所(plugins/)。"""
    return app_root() / "plugins" if _PLUGINS_DIR is None else _PLUGINS_DIR


def engines_dir() -> Path:
    """同梱する思考エンジンの置き場所(engines/)。"""
    return app_root() / "engines" if _ENGINES_DIR is None else _ENGINES_DIR
