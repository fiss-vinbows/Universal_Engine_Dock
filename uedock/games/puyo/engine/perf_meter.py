"""CPU 使用率と観測の FPS を測る。

CPU は PC 全体の使用率(Windows の GetSystemTimes。追加のライブラリは使わない)。FPS はこのツールが画面を取り込んで読む回数で、
ゲーム自体の FPS ではない(外からは測れない)。
"""

from __future__ import annotations

import ctypes
import sys


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    def value(self) -> int:
        return (self.high << 32) | self.low


def _system_times() -> tuple[int, int, int] | None:
    """(待機, カーネル, ユーザー) の累計時間。カーネルには待機を含む。Windows 以外・失敗時は None"""
    if sys.platform != "win32":
        return None
    idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
    try:
        ok = ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    except (OSError, AttributeError):
        return None
    return (idle.value(), kernel.value(), user.value()) if ok else None


class CpuMeter:
    """前回の sample からの PC 全体の CPU 使用率(%)。初回と測れない時は None"""

    def __init__(self, read=_system_times) -> None:
        self._read = read
        self._last = read()

    def sample(self) -> float | None:
        now = self._read()
        last, self._last = self._last, now
        if now is None or last is None:
            return None
        idle = now[0] - last[0]
        total = (now[1] - last[1]) + (now[2] - last[2])
        if total <= 0:
            return None
        return max(0.0, min(100.0, (total - idle) * 100.0 / total))


# 観測の FPS は上のバー(uedock/ui/status_labels.py)と共通の部品
from uedock.ui.status_labels import FpsMeter  # noqa: E402,F401


def vision_text(fps: float) -> str:
    """盤面の上に出す1行"""
    return f"観測 {fps:.0f}fps"


def perf_text(cpu: float | None, fps: float) -> str:
    """オーバーレイに出す1行"""
    return f"CPU {'--' if cpu is None else f'{cpu:.0f}'}% / 観測 {fps:.0f}fps"
