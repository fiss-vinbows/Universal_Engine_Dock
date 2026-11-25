"""テストで本番の設定(config/)・プラグイン(plugins/)・同梱エンジン(engines/)を読み書きしないようにする

このモジュールを読み込むと、利用者が書き換える場所だけを一時フォルダに差し替える。app_root() は替えないので、
同梱の素材(assets/・テンプレート画像)と、並べて置いたエンジンのビルド結果は今までどおり見える。
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

from uedock import paths

ROOT = Path(tempfile.mkdtemp(prefix="uedock_test_"))
atexit.register(shutil.rmtree, ROOT, ignore_errors=True)

paths._CONFIG_DIR = ROOT / "config"
paths._PLUGINS_DIR = ROOT / "plugins"
paths._ENGINES_DIR = ROOT / "engines"

from uedock.capture import game_window  # noqa: E402

# 読み込み時に決まる置き場所も差し替える(先にほかのテストが読み込んでいても本番を見ないように)
game_window.SAVED_WINDOW_PATH = ROOT / "config" / "game_window.json"
game_window.WINDOW_SIZE_PATH = ROOT / "config" / "window_size.json"


def reset() -> None:
    """一時フォルダの設定とプラグインを消す(テストの setUp で呼ぶ)。"""
    for name in ("config", "plugins"):
        shutil.rmtree(ROOT / name, ignore_errors=True)
