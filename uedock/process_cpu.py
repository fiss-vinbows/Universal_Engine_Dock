"""プロセスごとの CPU 使用率。

PC 全体の CPU 使用率だけでは、だれが CPU を使っているか分からない。Universal Engine Dock(画面の読み取り・オーバーレイ)、
思考エンジン(子のプロセスを含む)、ゲームのそれぞれが、PC 全体の何 % を使ったかを測る。

値は「PC 全体(全コア)に占める割合」で、上のバーの CPU 使用率と同じ物差し(足すと PC 全体の使用率に近くなる)。
Windows の GetProcessTimes・CreateToolhelp32Snapshot を使う(追加のライブラリは使わない)。Windows 以外では測らない。
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002


def _windows() -> bool:
    return sys.platform == "win32"


def process_cpu_time(pid: int) -> int | None:
    """そのプロセスがこれまでに使った CPU の時間(カーネル + ユーザー、100ns 単位)。読めない時は None。"""
    if not _windows():
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        creation, exit_, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            return None

        def value(t: wintypes.FILETIME) -> int:
            return (t.dwHighDateTime << 32) | t.dwLowDateTime

        return value(kernel) + value(user)
    finally:
        kernel32.CloseHandle(handle)


def child_map() -> dict[int, list[int]]:
    """{親の pid: [子の pid]}(今動いているプロセス全部)。読めない時は空。"""
    if not _windows():
        return {}
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return {}
    children: dict[int, list[int]] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            children.setdefault(int(entry.th32ParentProcessID), []).append(int(entry.th32ProcessID))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return children


def with_descendants(pid: int, children: dict[int, list[int]]) -> list[int]:
    """そのプロセスと、その子・孫のプロセス(思考エンジンが別のプロセスを起動している時も数える)。"""
    out, stack, seen = [], [pid], set()
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        stack.extend(children.get(p, []))
    return out


@dataclass
class ProcessGroup:
    """測る相手。pid は起動していない・分からない時 None(その回は数えない)。"""

    label: str
    pid: Callable[[], int | None]
    descendants: bool = False  # 子・孫のプロセスも足す(思考エンジン)。自分(Universal Engine Dock)は足さない(エンジンが子なので)


@dataclass
class ProcessCpuMeter:
    """前回の sample からの、グループごとの CPU 使用率(PC 全体に占める %)。初回と測れないグループは入れない。"""

    groups: list[ProcessGroup]
    read_time: Callable[[int], int | None] = process_cpu_time
    read_children: Callable[[], dict[int, list[int]]] = child_map
    clock: Callable[[], float] = time.monotonic
    cpus: int = field(default_factory=lambda: os.cpu_count() or 1)
    _last_times: dict[int, int] = field(default_factory=dict)
    _last_clock: float | None = None

    def sample(self) -> dict[str, float]:
        now = self.clock()
        children = self.read_children() if any(g.descendants for g in self.groups) else {}
        times: dict[int, int] = {}
        usage: dict[str, float] = {}
        elapsed = None if self._last_clock is None else now - self._last_clock
        for group in self.groups:
            root = group.pid()
            if root is None:
                continue
            pids = with_descendants(root, children) if group.descendants else [root]
            used, known = 0, False
            for pid in pids:
                value = self.read_time(pid)
                if value is None:
                    continue
                times[pid] = value
                before = self._last_times.get(pid)
                if before is not None and value >= before:
                    used += value - before
                    known = True
            if known and elapsed and elapsed > 0:
                usage[group.label] = max(0.0, min(100.0, used / (elapsed * 1e7 * self.cpus) * 100.0))
        self._last_times, self._last_clock = times, now
        return usage


class CpuBreakdownStats:
    """1秒ごとの内訳をためて、ログに出す1行にまとめる。"""

    def __init__(self, labels: list[str]) -> None:
        self.labels = labels
        self.samples: dict[str, list[float]] = {label: [] for label in labels}

    def add(self, usage: dict[str, float]) -> None:
        for label, value in usage.items():
            self.samples.setdefault(label, []).append(value)

    def count(self) -> int:
        return max((len(v) for v in self.samples.values()), default=0)

    def summary(self) -> str:
        """「Universal Engine Dock 中央 12%・最大 20% / ...」。測れたものが無ければ空。"""
        parts = []
        for label in self.labels:
            values = sorted(self.samples.get(label, []))
            if values:
                parts.append(f"{label} 中央 {values[len(values) // 2]:.0f}%・最大 {values[-1]:.0f}%")
        return " / ".join(parts)

    def reset(self) -> None:
        self.samples = {label: [] for label in self.labels}


def window_pid(hwnd: int) -> int | None:
    """そのウィンドウのプロセス(ゲーム)の pid。分からない時は None。"""
    if not _windows() or not hwnd:
        return None
    import ctypes
    from ctypes import wintypes

    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), ctypes.byref(pid))
    return int(pid.value) or None
