"""支援モードの上のバー(ゲームのタイトルバーの上)に置く、起動時間・CPU 使用率・観測の FPS の表示。ぷよ・テトリスで共通。

テトリスで作ったものを、ぷよでも同じ見た目で使う
"""

from __future__ import annotations

import sys
import time
from collections import deque

from PyQt6 import QtCore, QtWidgets


def format_uptime(seconds: float) -> str:
    """起動時間の表示。1時間までは 分:秒、それ以降は 時:分:秒。"""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"起動 {hours}:{minutes:02d}:{secs:02d}" if hours else f"起動 {minutes:02d}:{secs:02d}"


UPTIME_LABEL_STYLE = "QLabel { color: white; background: #111; font-weight: bold; border: 1px solid #555; padding: 2px 8px; }"


class UptimeLabel(QtWidgets.QLabel):
    """支援モードを起動してからの時間。"""

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(UPTIME_LABEL_STYLE)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        # 桁が増えてもボタンの位置が動かないよう、幅を先に確保する
        self.setMinimumWidth(self.fontMetrics().horizontalAdvance(format_uptime(36000)) + 20)
        self._started = time.monotonic()
        self.setText(format_uptime(0))
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._update)
        self._timer.start(1000)

    def _update(self) -> None:
        self.setText(format_uptime(time.monotonic() - self._started))


def read_cpu_times() -> tuple[int, int] | None:
    """PC 全体の (アイドルの時間, 全体の時間)。読めない時は None。"""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None

    def value(t: wintypes.FILETIME) -> int:
        return (t.dwHighDateTime << 32) | t.dwLowDateTime

    # kernel の時間にアイドルの時間が含まれる
    return value(idle), value(kernel) + value(user)


def format_cpu_usage(before: tuple[int, int] | None, after: tuple[int, int] | None) -> str:
    """2回の read_cpu_times の間の、PC 全体の CPU 使用率の表示。"""
    if before is None or after is None or after[1] <= before[1]:
        return "CPU --%"
    busy = 1 - (after[0] - before[0]) / (after[1] - before[1])
    return f"CPU {round(min(max(busy, 0.0), 1.0) * 100)}%"


class CpuUsageLabel(QtWidgets.QLabel):
    """PC 全体の CPU 使用率。1秒ごとに更新する。"""

    def __init__(self) -> None:
        super().__init__("CPU --%")
        self.setStyleSheet(UPTIME_LABEL_STYLE)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        # 桁が増えてもボタンの位置が動かないよう、幅を先に確保する
        self.setMinimumWidth(self.fontMetrics().horizontalAdvance("CPU 100%") + 20)
        self._last = read_cpu_times()
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._update)
        self._timer.start(1000)

    def _update(self) -> None:
        now = read_cpu_times()
        self.setText(format_cpu_usage(self._last, now))
        self._last = now


class FpsMeter:
    """直近 window 秒の観測の回数から FPS を出す(ぷよの perf_meter から移した。テトリスでも使う)"""

    def __init__(self, window: float = 1.0) -> None:
        self.window = window
        self._ticks: deque[float] = deque()

    def tick(self, t: float) -> None:
        self._ticks.append(t)
        while self._ticks and t - self._ticks[0] > self.window:
            self._ticks.popleft()

    def fps(self) -> float:
        if len(self._ticks) < 2:
            return 0.0
        span = self._ticks[-1] - self._ticks[0]
        return (len(self._ticks) - 1) / span if span > 0 else 0.0


def format_fps(values: dict[str, float | None], sides: list[str]) -> str:
    """上のバーの観測の FPS。1つの側なら「観測 32fps」、2つなら「観測 1P 32・2P 31fps」。まだ測れていない側は --。"""

    def num(side: str) -> str:
        value = values.get(side)
        return "--" if value is None else f"{value:.0f}"

    if len(sides) == 1:
        return f"観測 {num(sides[0])}fps"
    return "観測 " + "・".join(f"{side.upper()} {num(side)}" for side in sides) + "fps"


class FpsLabel(QtWidgets.QLabel):
    """観測の FPS(Universal Engine Dock が画面を読む回数。ゲーム自体の FPS ではない)。
    ワーカーが1秒ごとに set_fps で渡す。"""

    def __init__(self, sides: list[str]) -> None:
        super().__init__()
        self.sides = list(sides)
        self.values: dict[str, float | None] = {side: None for side in self.sides}
        self.setStyleSheet(UPTIME_LABEL_STYLE)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.setToolTip("Universal Engine Dock がゲームの画面を読んでいる回数(1秒あたり)。ゲーム自体の FPS ではありません")
        # 桁が増えてもボタンの位置が動かないよう、幅を先に確保する
        widest = format_fps({side: 100.0 for side in self.sides}, self.sides)
        self.setMinimumWidth(self.fontMetrics().horizontalAdvance(widest) + 20)
        self.setText(format_fps(self.values, self.sides))

    def set_fps(self, side: str, fps: float) -> None:
        if side not in self.values:
            return
        self.values[side] = fps
        self.setText(format_fps(self.values, self.sides))
