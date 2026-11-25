"""Steam(PC)版のウィンドウの中の、ぷよの盤面・ネクスト枠の位置

ゲームのウィンドウの検出と大きさの調整は共通基盤(uedock/capture/game_window.py)にある。ここは、その描画領域
(クライアント領域)の中の盤面・ネクスト枠の位置を、1920×1080 の画面での位置(録画で検証済みの
tests/puyo/fixtures/calibration_1080p.json と同じ値)を縮めて求める。
16:9 でないウィンドウでは、画面は縦横比を保って中央に描かれる(上下か左右に黒帯)とみなす。
ウィンドウの画像から盤面の暗い矩形を見つけられた時は(盤面が空に近い時)、その位置に合わせ直す。
手動のキャリブレーション(Switch 用)は要らない。

座標は物理ピクセル(PyQt6 のプロセスは DPI を認識するので、Win32 の座標も物理ピクセルで返る)。
"""

from __future__ import annotations

from typing import Callable

import numpy as np

from ....capture import game_window as _shared
from ....capture.game_window import (  # noqa: F401  ぷよのモジュールが今までどおりここから使えるように
    CLIENT_SIZE_PRESETS,
    GameWindow,
    SavedWindow,
    WindowEntry,
    client_rect,
    find_game_window,
    list_windows,
    resize_client,
    resized_window_rect,
    save_saved_window,
    title_bar_rect,
)
from ....capture.screen_capture import CaptureRegion
from .calibrate import CalibrationResult, PlayerCalibration, Rect
from .geometry import Quad, detect_board_quads

REFERENCE_SIZE = (1920, 1080)
# 1920×1080 の画面での盤面(可視12段の4隅)とネクスト枠。一人用(とことん)も自分の盤面・枠は対戦と同じ位置
REFERENCE_LAYOUT = CalibrationResult(
    self_player=PlayerCalibration(
        board_quad=((272.0, 159.0), (672.0, 159.0), (672.0, 882.0), (272.0, 882.0)),
        next_rects=[(720, 160, 60, 125), (775, 293, 46, 102)],
    ),
    enemy_player=PlayerCalibration(
        board_quad=((1248.0, 159.0), (1648.0, 159.0), (1648.0, 882.0), (1248.0, 882.0)),
        next_rects=[(1140, 160, 60, 125), (1099, 293, 46, 102)],
    ),
)
# 見つけた盤面の暗い矩形を採用する、計算した位置とのずれの上限(盤面の幅に対する割合)
DETECT_TOLERANCE = 0.08
# 見つけた矩形の大きさ(幅・高さ)と計算した盤面の大きさの差の上限(割合)。
# 大きさも位置と同じ 8% まで許していたので、盤面にぷよが
# ある時に見つけた幅 555px の矩形(本当は 533px。保存画像では 555・562・570px なども見つかる)を採用し、右の列ほど
# マスの中心が右へずれた(6列目で約20px)。読み取りも6列目の取り落としが増えた。
# 16:9 のウィンドウでは計算した大きさが正しいので、大きさはほぼ同じものだけ採用する
DETECT_SIZE_TOLERANCE = 0.015
# 配置の違うゲーム(ぷよぷよeスポーツなど)で見えた盤面を使う時の、縦横比の差の上限(割合。盤面は6列×12段で同じ形)
DETECT_ASPECT_TOLERANCE = 0.02


def content_rect(width: int, height: int) -> tuple[float, float, float]:
    """クライアント領域の中で画面が描かれる位置(左の余白, 上の余白, 1920×1080 に対する倍率)。"""
    return _shared.content_rect(width, height, REFERENCE_SIZE)


def _board_box(quad: Quad) -> tuple[float, float, float, float]:
    (x0, y0), _, (x1, y1), _ = quad
    return x0, y0, x1 - x0, y1 - y0


def _player_on_board(ref: PlayerCalibration, board: Quad) -> PlayerCalibration:
    """基準の盤面とネクスト枠の位置関係を保ったまま、盤面を board に置いた時の枠。"""
    rx, ry, rw, rh = _board_box(ref.board_quad)
    bx, by, bw, bh = _board_box(board)
    rects: list[Rect] = []
    for x, y, w, h in ref.next_rects:
        rects.append((round(bx + (x - rx) * bw / rw), round(by + (y - ry) * bh / rh), round(w * bw / rw), round(h * bh / rh)))
    return PlayerCalibration(board_quad=board, next_rects=rects)


def _scaled_quad(quad: Quad, ox: float, oy: float, scale: float) -> Quad:
    return tuple((round(ox + x * scale), round(oy + y * scale)) for x, y in quad)  # type: ignore[return-value]


def layout_calibration(window: GameWindow, solo: bool = False) -> CalibrationResult:
    """ウィンドウの大きさから求めた盤面・ネクスト枠(画面の物理ピクセル)。一人用は相手を自分の複製にする(使わない)。"""
    ox, oy, scale = content_rect(window.width, window.height)
    ox += window.left
    oy += window.top
    players = [
        _player_on_board(ref, _scaled_quad(ref.board_quad, ox, oy, scale))
        for ref in (REFERENCE_LAYOUT.self_player, REFERENCE_LAYOUT.enemy_player)
    ]
    if solo:
        players[1] = players[0]
    return CalibrationResult(self_player=players[0], enemy_player=players[1])


def refine_with_detected(calibration: CalibrationResult, quads: list[Quad], solo: bool = False) -> CalibrationResult:
    """見つけた盤面の暗い矩形(画面の物理ピクセル)のうち、計算した位置に近いものに盤面を合わせ直す。
    近いものが無い側は計算した位置のまま。"""

    def refine(player: PlayerCalibration) -> PlayerCalibration:
        x, y, w, h = _board_box(player.board_quad)
        tol = w * DETECT_TOLERANCE
        for quad in quads:
            qx, qy, qw, qh = _board_box(quad)
            if (abs(qx - x) <= tol and abs(qy - y) <= tol
                    and abs(qw - w) <= w * DETECT_SIZE_TOLERANCE and abs(qh - h) <= h * DETECT_SIZE_TOLERANCE):
                return _player_on_board(player, quad)
        return player

    self_player = refine(calibration.self_player)
    enemy_player = self_player if solo else refine(calibration.enemy_player)
    if not solo and self_player is calibration.self_player and enemy_player is calibration.enemy_player and len(quads) == 2:
        # どちらも計算した位置に無いが、同じ大きさの盤面が2つ見えている: 画面の配置が違うゲーム(ぷよぷよeスポーツなど)
        # とみなし、見えた盤面に合わせる(ネクスト枠は盤面との位置関係をぷよぷよテトリス2 と同じとみなす)
        (_, _, w0, h0), (_, _, w1, h1) = _board_box(quads[0]), _board_box(quads[1])
        _, _, rw, rh = _board_box(REFERENCE_LAYOUT.self_player.board_quad)
        aspect = rw / rh
        same_size = abs(w0 - w1) <= w0 * DETECT_SIZE_TOLERANCE and abs(h0 - h1) <= h0 * DETECT_SIZE_TOLERANCE
        board_shape = all(h > 0 and abs(w / h - aspect) <= aspect * DETECT_ASPECT_TOLERANCE for w, h in ((w0, h0), (w1, h1)))
        if same_size and board_shape:
            self_player = _player_on_board(REFERENCE_LAYOUT.self_player, quads[0])
            enemy_player = _player_on_board(REFERENCE_LAYOUT.enemy_player, quads[1])
    return CalibrationResult(self_player=self_player, enemy_player=enemy_player)


def detect_calibration(
    window: GameWindow, solo: bool = False, grab: Callable[[CaptureRegion], np.ndarray] | None = None
) -> CalibrationResult:
    """ウィンドウの盤面・ネクスト枠。grab(領域 → BGR 画像)を渡すと、その画像の盤面の矩形で合わせ直す。"""
    calibration = layout_calibration(window, solo)
    if grab is None:
        return calibration
    region = window.region()
    try:
        image = grab(region)
    except Exception:  # 取得できなくても、計算した位置で動かせる
        return calibration
    min_height = max(50, round(calibration.self_player.geometry().bounding_box()[3] * 0.6))
    quads = [
        tuple((x + region.left, y + region.top) for x, y in q)  # type: ignore[misc]
        for q in detect_board_quads(image, min_height=min_height)
    ]
    return refine_with_detected(calibration, quads, solo)  # type: ignore[arg-type]
