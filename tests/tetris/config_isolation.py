"""テトリスのテストで本番の設定を読み書きしないようにする。

共通の隔離(tests/config_isolation.py)に加えて、テトリスのモジュールが読み込み時に決める設定ファイルの置き場所を
一時フォルダに差し替える(画面を作るテストが本番の表示設定を読んで結果が変わったり、本番のファイルに書いたりしないように)。
"""

from __future__ import annotations

from tests import config_isolation as _shared

_DIR = _shared.ROOT / "config"

from uedock.games.tetris.education import gamepad, keybindings  # noqa: E402

keybindings.KEYS_PATH = _DIR / "tetris_education_keys.json"
keybindings.REPEAT_PATH = _DIR / "tetris_education_repeat.json"
keybindings.VIEW_PATH = _DIR / "tetris_education_view.json"
gamepad.PAD_PATH = _DIR / "tetris_education_pad.json"

from uedock.games.tetris import assist_view  # noqa: E402

assist_view.ASSIST_VIEW_PATH = _DIR / "tetris_assist_view.json"

from uedock.games.tetris.capture import calibrate  # noqa: E402

calibrate.CONFIG_PATH = _DIR / "tetris_calibration.json"
