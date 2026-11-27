"""テトリスのゲームモード。

支援モード(認識・思考・オーバーレイ・情報パネル)と、シミュレーター・無限中あけREN(移植の段階4)を持つ。
別ウィンドウで開く(隠している間は思考しない)。
"""

from __future__ import annotations

from uedock.game_mode import AssistRequest, EngineSpec, GameMode, SimulatorEntry
from uedock.games.tetris.engine.cold_clear_client import KASUGAI_EXE, KASUGAI_WEIGHTS

# 同梱する自作AI KasugAI(TBP 互換)
KASUGAI = EngineSpec("KasugAI", KASUGAI_EXE, weights=KASUGAI_WEIGHTS)


# 画面・画像処理のモジュールは重いので、使う時に読み込む
def _start_assist(request: AssistRequest):  # noqa: ANN202
    from uedock.games.tetris.session import start_assist

    return start_assist(request)


def _side_panel(side: str, parent):  # noqa: ANN001, ANN202
    """支援の設定(テンプレ・DPC・TDTD・継続パフェ等)。切り替えるたびにサイドごとのファイルへ保存される。"""
    from PyQt6 import QtWidgets

    from uedock.games.tetris.assist_view import AssistSettingsPanel
    from uedock.games.tetris.session import side_view_path

    # 縦に長いのでスクロールできるようにする
    scroll = QtWidgets.QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    scroll.setWidget(AssistSettingsPanel(path=side_view_path(side)))
    return scroll


def _calibrate(layout: str, parent) -> None:  # noqa: ANN001, ARG001
    """手動キャリブレーション(Switch 用)。1P・一人用の盤面・HOLD・NEXT の位置を決める。"""
    from uedock.games.tetris.capture.calibrate import run_calibration

    run_calibration()


def _independent(window):  # noqa: ANN001, ANN202
    """メイン画面は支援モードの間隠れるので、親を持たない独立したウィンドウにし、閉じたら片付ける。"""
    from PyQt6 import QtCore

    window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
    return window


def _open_practice(parent):  # noqa: ANN001, ANN202, ARG001
    from uedock.games.tetris.education.window import PracticeWindow

    return _independent(PracticeWindow())


def _open_ren(parent):  # noqa: ANN001, ANN202, ARG001
    from uedock.games.tetris.education.ren import RenWindow

    return _independent(RenWindow())


def _unsupported_reason(window) -> str | None:  # noqa: ANN001
    """ぷよぷよeスポーツはぷよぷよだけのゲームなので、テトリスは使えない。"""
    from uedock.capture.game_window import is_esports_title

    return "ぷよぷよeスポーツにはテトリスがありません" if is_esports_title(window.title) else None


MODE = GameMode(
    id="tetris",
    name="テトリス",
    engine_protocol="TBP 互換(標準入出力)の実行ファイル(Cold Clear 2 など。詳しくは docs/ENGINE_SPEC.md)",
    builtin_engines=(KASUGAI,),
    start_assist=_start_assist,
    side_panel=_side_panel,
    calibrate=_calibrate,
    simulators=(
        SimulatorEntry("テトリスのシミュレーター", _open_practice),
        SimulatorEntry("無限中あけREN", _open_ren),
    ),
    unsupported_reason=_unsupported_reason,
)
