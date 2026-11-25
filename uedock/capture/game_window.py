"""ゲームのウィンドウの検出と大きさの調整(ゲームによらない共通部分)。

ゲームのウィンドウ(Steam 版ぷよぷよテトリス2・ぷよぷよeスポーツ)を探し、その描画領域(クライアント領域。タイトルバー・枠を除く)を
求める。描画領域の中の盤面・ネクスト枠の位置はゲームモードごとに違うので、ここでは扱わない(各ゲームモードが、
自分の基準の配置を content_rect() の余白と倍率で縮めて求める)。

座標は物理ピクセル(PyQt6 のプロセスは DPI を認識するので、Win32 の座標も物理ピクセルで返る。手動のキャリブレーションと同じ)。
"""

from __future__ import annotations

import ctypes
import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ..paths import config_dir
from .screen_capture import CaptureRegion

GAME_EXE_NAMES = ("puyopuyotetris2.exe",)
# ウィンドウのタイトル(実機では「PuyoPuyoTetris2」。ぷよぷよeスポーツは「Puyo Puyo Champions」のことがある)
GAME_TITLE_KEYS = ("puyopuyotetris2", "ぷよぷよテトリス2", "puyopuyochampions", "ぷよぷよeスポーツ")
# メイン画面の「ゲームのウィンドウを検出」で一覧から選んだウィンドウ(実行ファイル名とタイトル)。次からはこれでも探す
SAVED_WINDOW_PATH = config_dir() / "game_window.json"
# 一覧に出すウィンドウの最小の大きさ(小さい道具のウィンドウを除く)
MIN_LIST_SIZE = (320, 180)


@dataclass(frozen=True)
class GameWindow:
    hwnd: int
    title: str
    left: int  # クライアント領域の左上(画面の物理ピクセル)
    top: int
    width: int
    height: int

    def region(self) -> CaptureRegion:
        return CaptureRegion(left=self.left, top=self.top, width=self.width, height=self.height)


@dataclass(frozen=True)
class SavedWindow:
    """利用者が選んだゲームのウィンドウ。exe(小文字のファイル名)が合えばそれ、無ければ title(正規化して一致)で探す。"""

    exe: str = ""
    title: str = ""

    def matches(self, title: str, exe: str) -> bool:
        if self.exe:
            return exe == self.exe
        return bool(self.title) and normalize_title(title) == normalize_title(self.title)


def load_saved_window(path: Path | None = None) -> SavedWindow | None:
    path = SAVED_WINDOW_PATH if path is None else path  # 呼んだ時点の置き場所(テストで差し替えられる)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        saved = SavedWindow(exe=str(data.get("exe", "")).lower(), title=str(data.get("title", "")))
    except (OSError, ValueError, AttributeError):
        return None
    return saved if saved.exe or saved.title else None


def save_saved_window(saved: SavedWindow, path: Path | None = None) -> None:
    path = Path(SAVED_WINDOW_PATH if path is None else path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"exe": saved.exe, "title": saved.title}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_title(title: str) -> str:
    """比べやすいタイトル(™・®・記号・空白を除き、全角を半角に、小文字に)。"""
    title = "".join(c for c in title if c not in "™®©")
    return "".join(c for c in unicodedata.normalize("NFKC", title).lower() if c.isalnum())


def is_game_title(title: str) -> bool:
    norm = normalize_title(title)
    return any(key in norm for key in GAME_TITLE_KEYS)


def is_game_window(title: str, exe: str, saved: SavedWindow | None = None) -> bool:
    """ゲームのウィンドウか(決め打ちのタイトル・実行ファイル名か、利用者が選んだもの)。"""
    return is_game_title(title) or exe in GAME_EXE_NAMES or (saved is not None and saved.matches(title, exe))


def content_rect(width: int, height: int, reference: tuple[int, int]) -> tuple[float, float, float]:
    """クライアント領域の中で画面が描かれる位置(左の余白, 上の余白, reference に対する倍率)。

    ゲームは縦横比を保って中央に描く(16:9 でないウィンドウでは上下か左右に黒帯)とみなす。
    reference は、ゲームモードが配置を測った時の描画領域の大きさ(ぷよは 1920×1080、テトリスは 2560×1440)。
    """
    rw, rh = reference
    scale = min(width / rw, height / rh)
    return (width - rw * scale) / 2, (height - rh * scale) / 2, scale


def parse_client_size(text: str) -> tuple[int, int] | None:
    """"1920×1080" の形の大きさ。読めなければ None。"""
    try:
        w, h = text.replace("x", "×").split("×")
        return int(w), int(h)
    except ValueError:
        return None


# ---- Win32(Windows だけ) ----

def _exe_name(pid: int) -> str:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p,
                                                    ctypes.POINTER(ctypes.c_uint32)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_uint32(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    finally:
        kernel32.CloseHandle(handle)


# ウィンドウの大きさのプリセット(描画領域。ゲームは 16:9 で描く)
CLIENT_SIZE_PRESETS = ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440))
DEFAULT_CLIENT_SIZE = "1920×1080"
WINDOW_SIZE_PATH = config_dir() / "window_size.json"


def load_window_size(path: Path | None = None) -> str:
    """前に選んだ大きさ("1920×1080" の形)。無ければ 1920×1080。"""
    path = WINDOW_SIZE_PATH if path is None else path
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("size", DEFAULT_CLIENT_SIZE))
    except (OSError, ValueError, AttributeError):
        return DEFAULT_CLIENT_SIZE


def save_window_size(text: str, path: Path | None = None) -> None:
    path = WINDOW_SIZE_PATH if path is None else path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"size": text}, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass


def resized_window_rect(
    window: tuple[int, int, int, int], client: tuple[int, int, int, int], size: tuple[int, int],
    work_area: tuple[int, int, int, int] | None = None,
) -> tuple[int, int, int, int]:
    """クライアント領域を size にする時のウィンドウの位置と大きさ(左, 上, 幅, 高さ)。枠・タイトルバーの分は今の差をそのまま足す。
    左上はそのままにし、作業領域(左, 上, 幅, 高さ)からはみ出す時は収まるよう左・上へずらす(入りきらなければ左上に合わせる)。"""
    wx, wy, ww, wh = window
    _, _, cw, ch = client
    new_w, new_h = size[0] + (ww - cw), size[1] + (wh - ch)
    x, y = wx, wy
    if work_area is not None:
        ax, ay, aw, ah = work_area
        x = max(ax, min(x, ax + aw - new_w))
        y = max(ay, min(y, ay + ah - new_h))
    return x, y, new_w, new_h


def resize_client(hwnd: int, size: tuple[int, int]) -> tuple[int, int] | None:
    """ウィンドウのクライアント領域を size(物理ピクセル)にする。結果のクライアント領域の大きさを返す(できなければ None)。
    ゲームが大きさを丸めることがあるので、1回ずれたらもう1回だけ合わせ直す。"""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.UINT]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    for _ in range(2):
        client = client_rect(hwnd)
        rect = wintypes.RECT()
        if client is None or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        if client[2:] == size:
            return size
        work = None
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.rcWork
            work = (r.left, r.top, r.right - r.left, r.bottom - r.top)
        window = (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        x, y, w, h = resized_window_rect(window, client, size, work)
        if not user32.SetWindowPos(hwnd, None, x, y, w, h, 0x0004 | 0x0010):  # SWP_NOZORDER | SWP_NOACTIVATE
            return None
    client = client_rect(hwnd)
    return None if client is None else client[2:]


def client_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """ウィンドウのクライアント領域(左, 上, 幅, 高さ。画面の物理ピクセル)。最小化中・閉じた時は None。"""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        return None
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return origin.x, origin.y, width, height


def title_bar_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """ウィンドウのタイトルバー(左, 上, 幅, 高さ。画面の物理ピクセル)。枠なし・全画面・読めない時は None。"""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    client = client_rect(hwnd)
    if client is None:
        return None
    rect = wintypes.RECT()
    try:  # 見えている枠(Windows 10 以降は GetWindowRect に見えない縁が入るので DWM に聞く)
        dwmapi = ctypes.WinDLL("dwmapi")
        dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        ok = dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0  # DWMWA_EXTENDED_FRAME_BOUNDS
    except OSError:
        ok = False
    if not ok:
        user32 = ctypes.WinDLL("user32")
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
    height = client[1] - rect.top
    if height < 16:
        return None
    return rect.left, rect.top, rect.right - rect.left, height


@dataclass(frozen=True)
class WindowEntry:
    """表示中のウィンドウ(一覧から選ぶ用)。"""

    window: GameWindow
    exe: str


def list_windows() -> list[WindowEntry]:
    """表示中でタイトルのある、ある程度大きいウィンドウ(画面の手前から順)。Windows 以外は空。"""
    if sys.platform != "win32":
        return []
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    found: list[WindowEntry] = []

    def visit(hwnd, _lparam):  # noqa: ANN001, ANN202
        # 例外を外に出すと EnumWindows が途中で止まるので、1つのウィンドウの失敗は飛ばして続ける
        try:
            if not hwnd or not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            rect = client_rect(hwnd)
            if rect is None or rect[2] < MIN_LIST_SIZE[0] or rect[3] < MIN_LIST_SIZE[1]:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            try:
                exe = _exe_name(pid.value)
            except OSError:
                exe = ""
            found.append(WindowEntry(GameWindow(int(hwnd), buf.value, *rect), exe))
        except Exception:  # noqa: BLE001
            pass
        return True

    user32.EnumWindows(enum_proc(visit), 0)
    return found


def find_game_window(saved: SavedWindow | None = None, windows: list[WindowEntry] | None = None) -> GameWindow | None:
    """表示中のぷよぷよテトリス2 のウィンドウ(実行ファイル名・タイトル、または利用者が選んだもの)。複数あれば一番大きいもの。
    Windows 以外・見つからない時は None。windows はテスト用(既定は list_windows())。"""
    if saved is None:
        saved = load_saved_window()
    entries = list_windows() if windows is None else windows
    found = [e.window for e in entries if is_game_window(e.window.title, e.exe, saved)]
    if not found:
        return None
    return max(found, key=lambda w: w.width * w.height)
