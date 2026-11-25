"""プラグインが、メイン画面と支援モードに処理を足すための窓口(拡張)。

プラグインは Plugin.extension(uedock/plugin_api.py)に Extension を作る関数を書く。メイン画面は読み込んだプラグインの
Extension を1つずつ作り、有効なものだけを次の時に呼ぶ。どれも既定は何もしない。

メイン画面(uedock/ui/main_window.py):
- install(window): メイン画面を作った後。window.add_side_widget(side, widget)・window.add_tool_widget(widget) で項目を足せる
- refresh(window, enabled): プラグインの有効・無効が変わった後(足した項目の見せ方を変える)
- prepare(window, requests): 支援モードを始める直前。始められない理由を返すと始めない
- started(window, sessions) / stopped(window): 支援モードを始めた後・終えた後

支援モード(ゲームモード)は、AssistRequest.extensions で同じ Extension を受け取り、次の時に呼ぶ。
- client_options(game, side): 思考エンジンを起動する時に足す設定
- create_worker(game, side, context): 観測ループを作る時。作ったものを返すと、そのサイドはそれで動く(None なら標準)
- session_ready(game, session): 観測ループを作り終えて、動かす直前
"""

from __future__ import annotations

from typing import Any


class Extension:
    """プラグインがメイン画面と支援モードに足す処理。必要なものだけを上書きする。"""

    # ---- メイン画面 ----

    def install(self, window: Any) -> None:
        """メイン画面を作った後に1回呼ぶ。"""

    def refresh(self, window: Any, enabled: bool) -> None:
        """プラグインの有効・無効が変わった後に呼ぶ(enabled はこのプラグインが有効か)。"""

    def prepare(self, window: Any, requests: list) -> str | None:
        """支援モードを始める直前。requests は [(GameMode, AssistRequest)]。始められない理由を返すと始めない。"""
        return None

    def started(self, window: Any, sessions: list) -> None:
        """支援モードを始めた後(sessions は AssistSession の一覧)。"""

    def stopped(self, window: Any) -> None:
        """支援モードを終えた後。"""

    # ---- 支援モード ----

    def client_options(self, game: str, side: str) -> dict:
        """思考エンジンを起動する時に足す設定(ゲームモードごとに意味が決まっている)。"""
        return {}

    def create_worker(self, game: str, side: str, context: dict) -> Any | None:
        """そのサイドの観測ループを作る。None なら標準のものを使う。context はゲームモードが渡す材料。"""
        return None

    def session_ready(self, game: str, session: Any) -> None:
        """支援モードが観測ループを作り終えて、動かす直前。"""
