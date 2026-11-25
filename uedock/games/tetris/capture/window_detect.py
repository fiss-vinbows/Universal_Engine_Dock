"""Steam(PC)版のウィンドウの中の、テトリスの盤面・HOLD・NEXT枠の位置

ゲームのウィンドウの検出と大きさの調整は共通基盤(uedock/capture/game_window.py)にある。ここは、その描画領域
(クライアント領域。タイトルバー・枠を除く)の中の盤面・HOLD・NEXT枠の位置を、2560×1440 の描画領域での位置
(REFERENCE_LAYOUT)をその大きさに縮めて求める。16:9 でないウィンドウでは、画面は縦横比を保って中央に描かれる
(上下か左右に黒帯)とみなす。手動のキャリブレーション(Switch 用)は要らない。

座標は物理ピクセル(PyQt6 のプロセスは DPI を認識するので、Win32 の座標も物理ピクセルで返る。手動のキャリブレーションと同じ)。
"""

from __future__ import annotations

from uedock.capture import game_window as _shared
from uedock.capture.game_window import (  # noqa: F401  テトリスのモジュールが今までどおりここから使えるように
    CLIENT_SIZE_PRESETS,
    GameWindow,
    SavedWindow,
    WindowEntry,
    client_rect,
    find_game_window,
    list_windows,
    load_window_size,
    resize_client,
    resized_window_rect,
    save_saved_window,
    save_window_size,
    title_bar_rect,
)
from uedock.games.tetris.capture.calibrate import CalibrationResult

# 盤面は格子の線から(1マス48画素、可視20段)、HOLD・NEXTは黒い箱の中のミノの部分。この位置で、
# 実際の画像認識が 操作中S・HOLD空・NEXT L O I J T・盤面空 と正しく読めることを確かめた(tests/test_window_detect.py)。
REFERENCE_SIZE = (2560, 1440)
REFERENCE_LAYOUT = CalibrationResult(
    board_origin_x=409,
    board_origin_y=211,
    board_width=480,
    board_height=960,
    cell_size=48,
    board_cols=10,
    board_rows=20,
    hold_rect=(263, 263, 124, 77),
    next_rects=[(946, 209, 112, 76), (939, 312, 112, 76), (939, 413, 112, 76), (939, 514, 112, 76), (939, 616, 112, 76)],
)
# 2P の盤面・HOLD・NEXT は 1P をそのまま右へずらした位置。
# 最初は縮小されたスクリーンショットで 1269px と測ったが、実機の録画(2560×1440 の原寸)では 10px 右にずれていた。
# おじゃまの穴のマスに隣の明るい縁が入って埋まった行と読まれ、AI が1段ずれた盤面で考えて 2P が止まった
# 原寸の録画で盤面の左端を 1P と比べて 1259px に直した(tests/test_window_detect.py)
P2_OFFSET_X = 1259


def content_rect(width: int, height: int) -> tuple[float, float, float]:
    """クライアント領域の中で画面が描かれる位置(左の余白, 上の余白, REFERENCE_SIZE に対する倍率)。"""
    return _shared.content_rect(width, height, REFERENCE_SIZE)


def layout_calibration(window: GameWindow, side: str = "1p") -> CalibrationResult:
    """ウィンドウの大きさから求めた盤面・HOLD・NEXT枠(画面の物理ピクセル)。

    side: "1p"(左の盤面) / "2p"(右の盤面)。
    """
    ox, oy, scale = content_rect(window.width, window.height)
    ox += window.left
    oy += window.top
    ref = REFERENCE_LAYOUT
    if side == "2p":
        ox += P2_OFFSET_X * scale

    def rect(r: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x, y, w, h = r
        return round(ox + x * scale), round(oy + y * scale), round(w * scale), round(h * scale)

    left, top = round(ox + ref.board_origin_x * scale), round(oy + ref.board_origin_y * scale)
    return CalibrationResult(
        board_origin_x=left,
        board_origin_y=top,
        # 右端・下端を丸めてから幅を出す(幅を丸めると端が1px ずれることがある)
        board_width=round(ox + (ref.board_origin_x + ref.board_width) * scale) - left,
        board_height=round(oy + (ref.board_origin_y + ref.board_height) * scale) - top,
        cell_size=max(1, round(ref.cell_size * scale)),
        board_cols=ref.board_cols,
        board_rows=ref.board_rows,
        hold_rect=rect(ref.hold_rect),
        next_rects=[rect(r) for r in ref.next_rects],
    )
