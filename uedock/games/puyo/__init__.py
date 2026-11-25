"""ぷよのゲームモード。

支援モード(認識・追跡・思考・オーバーレイ)と、シミュレーター(移植の段階4)を持つ。
"""

from __future__ import annotations

from ...game_mode import LAYOUT_SOLO, AssistRequest, EngineSpec, GameMode, SimulatorEntry
from .engine.ama_client import YAMANEKO_BRIDGE_EXE

# 同梱する自作AI YamaNEKO(ama のブリッジと同じ標準入出力 JSON で話す)
YAMANEKO = EngineSpec("YamaNEKO", YAMANEKO_BRIDGE_EXE)


# 画面・画像処理のモジュールは重いので、使う時に読み込む
def _start_assist(request: AssistRequest):  # noqa: ANN202
    from .session import start_assist

    return start_assist(request)


def _side_panel(side: str, parent):  # noqa: ANN001, ANN202
    from .side_panel import PuyoSidePanel

    return PuyoSidePanel(side, parent)


def _calibrate(layout: str, parent) -> None:  # noqa: ANN001, ARG001
    """手動キャリブレーション(Switch 用)。一人用は画面の配置が対戦と違うので、配置ごとに別のファイルに保存する。"""
    from .capture.calibrate import run_calibration

    run_calibration(solo=layout == LAYOUT_SOLO)


def _open_simulator(parent):  # noqa: ANN001, ANN202, ARG001
    """ぷよのシミュレーター。メイン画面は支援モードの間隠れるので、親を持たない独立したウィンドウにする。"""
    from PyQt6 import QtCore

    from .simulator.window import SimulatorWindow

    window = SimulatorWindow()
    window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
    return window


MODE = GameMode(
    id="puyo",
    name="ぷよ",
    engine_protocol="ぷよの規格(docs/ENGINE_SPEC.md)の標準入出力 JSON で話す実行ファイル",
    builtin_engines=(YAMANEKO,),
    start_assist=_start_assist,
    side_panel=_side_panel,
    calibrate=_calibrate,
    simulators=(SimulatorEntry("ぷよのシミュレーター", _open_simulator),),
)
