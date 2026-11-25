"""物理ピクセル(Win32・mss の座標)と Qt の論理ピクセルの変換。ディスプレイごとに倍率が違ってもずれないようにする。

Qt(Windows)の論理座標では、ディスプレイの左上は物理ピクセルの位置のまま、ディスプレイの中だけがそのディスプレイの倍率で縮む。
例: 主ディスプレイ 5120×2160・150% は論理 (0, 0) 3413×1440、右の 1920×1080・100% は
物理 (5120, 1076) にあり、論理も (5120, 1076) 1920×1080。
以前は全部を主ディスプレイの倍率で割っていたので、ゲームを別のディスプレイに置くとオーバーレイ・情報パネル・終了ボタンが
違う所に出た。
"""

from __future__ import annotations

from typing import Protocol, Sequence

from PyQt6 import QtCore, QtWidgets


class ScreenLike(Protocol):
    def geometry(self) -> QtCore.QRect: ...

    def devicePixelRatio(self) -> float: ...  # noqa: N802


def native_rect(screen: ScreenLike) -> tuple[int, int, int, int]:
    """ディスプレイの物理ピクセルの範囲 (x, y, 幅, 高さ)。"""
    g = screen.geometry()
    dpr = screen.devicePixelRatio()
    return g.x(), g.y(), round(g.width() * dpr), round(g.height() * dpr)


def _screens(screens: Sequence[ScreenLike] | None) -> Sequence[ScreenLike]:
    if screens is not None:
        return screens
    app = QtWidgets.QApplication.instance()
    return app.screens() if app is not None else []


def screen_at(x: float, y: float, screens: Sequence[ScreenLike] | None = None) -> ScreenLike | None:
    """物理ピクセルの点 (x, y) があるディスプレイ。どれにも無ければ一番近いもの。ディスプレイが無ければ None。"""
    candidates = _screens(screens)
    best, best_dist = None, None
    for screen in candidates:
        sx, sy, sw, sh = native_rect(screen)
        dx = max(sx - x, 0, x - (sx + sw - 1))
        dy = max(sy - y, 0, y - (sy + sh - 1))
        dist = dx * dx + dy * dy
        if best_dist is None or dist < best_dist:
            best, best_dist = screen, dist
    return best


def screen_of_rect(x: float, y: float, w: float, h: float, screens: Sequence[ScreenLike] | None = None) -> ScreenLike | None:
    """物理ピクセルの範囲の中心があるディスプレイ(ゲームのウィンドウ・盤面がどのディスプレイにあるか)。"""
    return screen_at(x + w / 2, y + h / 2, screens)


def dpr_of(screen: ScreenLike | None) -> float:
    return screen.devicePixelRatio() if screen is not None else 1.0


def to_logical(x: float, y: float, screen: ScreenLike | None) -> tuple[float, float]:
    """物理ピクセルの点を、screen の倍率で Qt の論理ピクセルにする(screen の外の点も同じ倍率で延ばす)。"""
    if screen is None:
        return float(x), float(y)
    g = screen.geometry()
    dpr = screen.devicePixelRatio()
    return g.x() + (x - g.x()) / dpr, g.y() + (y - g.y()) / dpr


def to_physical(x: float, y: float, screen: ScreenLike | None) -> tuple[float, float]:
    """Qt の論理ピクセルの点を、screen の倍率で物理ピクセルに戻す(to_logical の逆)。"""
    if screen is None:
        return float(x), float(y)
    g = screen.geometry()
    dpr = screen.devicePixelRatio()
    return g.x() + (x - g.x()) * dpr, g.y() + (y - g.y()) * dpr


def to_logical_rect(x: float, y: float, w: float, h: float, screen: ScreenLike | None) -> QtCore.QRect:
    """物理ピクセルの範囲を、screen の倍率で Qt の論理ピクセルの QRect にする。"""
    lx, ly = to_logical(x, y, screen)
    dpr = dpr_of(screen)
    return QtCore.QRect(round(lx), round(ly), round(w / dpr), round(h / dpr))
