"""OBS のブラウザソースに、Universal Engine Dock の表示(ドット・情報パネル・エンジン名の札)を出す(OBS 出力プラグイン)。

Universal Engine Dock の表示は、自分のドットを盤面として読まないよう画面の取り込みから外している(WDA_EXCLUDEFROMCAPTURE)ので、
OBS の画面・ウィンドウ・ゲームの取り込みには写らない。そこで、取り込みから外したウィンドウを Qt の描画(QWidget.render)で
透明な画像に写し、この PC だけに見える小さな Web サーバー(127.0.0.1)で配る。

以前の作りとの違い(設定を簡単にするため):
- 画像はいつも 1920×1080。ゲームの描画領域(16:9。黒帯を除く)を画像いっぱいに対応させる。OBS ではブラウザソースを
  1920×1080 にして「画面に合わせる」(Ctrl+F)だけで、同じく画面に合わせたゲームの取り込みに重なる。
  ゲームのウィンドウの大きさ・置いたディスプレイが変わっても OBS 側は変えなくてよい(以前は幅・高さを毎回入れ直した)
- アドレスはぷよ・テトリスで1つ(以前は 18765 と 18766 で、ブラウザソースが2つ要った)。ポートは画面に出さない
  (config/obs_overlay.json の port で変えられる)
- OBS のページが読みに来ているかで「つながっているか」を出す。位置合わせ用の枠も出せる

描くのは画面のスレッドで、OBS が読みに来ている間だけ(RENDER_MS ごと)。前回と同じ画像なら配り直さない。
PNG への変換(1080p で数十ミリ秒)は別のスレッドで最新の画像だけ行う(ドットの表示を遅らせない)。

半透明の濃さ(match_obs_blending): OBS は半透明を明るさの直線的な値(リニア)で混ぜるので、そのままでは情報パネルの
黒い下地が画面で見るより薄くなり、録画で文字が読みにくかった。配る前に、半透明の所の濃さを OBS の混ぜ方に合わせて直す。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

from .capture.game_window import content_rect
from . import i18n
from .paths import config_dir
from .ui.screen_map import native_rect, to_physical

if TYPE_CHECKING:
    from .capture.game_window import GameWindow

DEFAULT_PORT = 18765
FRAME_SIZE = (1920, 1080)  # 配る画像の大きさ(OBS のブラウザソースの幅・高さ)
ASPECT = (16, 9)  # ゲームの描画領域の縦横比
POLL_MS = 50  # OBS のページが版の番号を聞く間隔
RENDER_MS = 50  # 表示を画像に写す間隔
CONNECTED_SEC = 2.0  # この秒数の間にページから問い合わせがあれば「つながっている」
WINDOW_REFRESH_SEC = 1.0  # ゲームのウィンドウの位置を確かめ直す間隔
# この属性を True にしたウィンドウは OBS に出さない(上のバー: 終了ボタン・起動時間・CPU 使用率。配信には要らない)
SKIP_PROPERTY = "ued_obs_skip"

PAGE = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Universal Engine Dock</title>
<style>html,body{{margin:0;background:transparent;overflow:hidden}}img{{position:absolute;left:0;top:0;width:100%;height:100%}}</style>
</head><body><img id="f" alt="">
<script>
let v = -1;
async function tick() {{
  try {{
    const n = Number(await (await fetch("/version", {{cache: "no-store"}})).text());
    if (n !== v) {{
      const img = new Image();
      img.onload = () => {{ document.getElementById("f").src = img.src; }};
      img.src = "/frame.png?v=" + n;
      v = n;
    }}
  }} catch (e) {{}}
  setTimeout(tick, {POLL_MS});
}}
tick();
</script></body></html>
""".encode("utf-8")


# ---- 設定 ----


@dataclass
class ObsConfig:
    enabled: bool = False
    port: int = DEFAULT_PORT


def config_path() -> Path:
    return config_dir() / "obs_overlay.json"


def load_config(path: Path | None = None) -> ObsConfig:
    try:
        data = json.loads(Path(path or config_path()).read_text(encoding="utf-8"))
        port = int(data.get("port", DEFAULT_PORT))
        return ObsConfig(bool(data.get("enabled", False)), port if 1024 <= port <= 65535 else DEFAULT_PORT)
    except (OSError, ValueError, AttributeError, TypeError):
        return ObsConfig()


def save_config(config: ObsConfig, path: Path | None = None) -> None:
    path = Path(path or config_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabled": config.enabled, "port": config.port}, indent=2) + "\n", encoding="utf-8")


# ---- 画像を作る ----


def content_area(client: tuple[int, int, int, int]) -> tuple[float, float, float, float]:
    """ゲームのクライアント領域 (左, 上, 幅, 高さ。物理ピクセル) の中の、描画領域(16:9。黒帯を除く)の (左, 上, 幅, 高さ)。"""
    left, top, width, height = client
    ox, oy, scale = content_rect(width, height, ASPECT)
    return left + ox, top + oy, ASPECT[0] * scale, ASPECT[1] * scale


def letterbox(client_size: tuple[int, int]) -> tuple[int, int]:
    """クライアント領域の黒帯の幅 (左右それぞれ, 上下それぞれ。物理ピクセル)。16:9 なら (0, 0)。"""
    ox, oy, _scale = content_rect(client_size[0], client_size[1], ASPECT)
    return round(ox), round(oy)


def _render_widget(widget: QtWidgets.QWidget, scale: float) -> QtGui.QImage:
    """ウィンドウを scale 倍(論理ピクセルに対する倍率)の透明な画像に描く。

    ぷよのオーバーレイは自分の範囲を透明で塗りつぶしてから描く(CompositionMode_Source)ので、1つの画像に直接重ねて描くと
    先に描いたウィンドウを消してしまう。ウィンドウごとに別の画像に描いてから重ねる。"""
    image = QtGui.QImage(max(1, round(widget.width() * scale)), max(1, round(widget.height() * scale)),
                         QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
    painter.scale(scale, scale)
    # 背景は描かない(透明のまま)。ウィンドウ自身と子の描画だけを写す
    widget.render(painter, QtCore.QPoint(), QtGui.QRegion(), QtWidgets.QWidget.RenderFlag.DrawChildren)
    painter.end()
    return image


def compose_frame(
    area: tuple[float, float, float, float],
    widgets: Iterable[QtWidgets.QWidget],
    guide: bool = False,
    size: tuple[int, int] = FRAME_SIZE,
) -> QtGui.QImage:
    """画面に出しているウィンドウを、ゲームの描画領域 area(物理ピクセル)を size いっぱいに対応させた透明な画像に描く。
    ウィンドウはそれぞれ自分のあるディスプレイの倍率で物理ピクセルに戻してから置く(ディスプレイごとに倍率が違ってもずれない)。"""
    ax, ay, aw, ah = area
    image = QtGui.QImage(size[0], size[1], QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
    sx = size[0] / aw if aw > 0 else 1.0
    for widget in widgets:
        if widget is None or not widget.isVisible() or widget.property(SKIP_PROPERTY):
            continue
        screen = widget.screen()
        dpr = screen.devicePixelRatio() if screen is not None else 1.0
        px, py = to_physical(widget.x(), widget.y(), screen)
        # 描画領域にまったく重ならないウィンドウは描かない
        pw, ph = widget.width() * dpr, widget.height() * dpr
        if px >= ax + aw or py >= ay + ah or px + pw <= ax or py + ph <= ay:
            continue
        sprite = _render_widget(widget, sx * dpr)
        painter.drawImage(QtCore.QPointF((px - ax) * sx, (py - ay) * sx), sprite)
    if guide:
        draw_guide(painter, size)
    painter.end()
    return image


GUIDE_COLOR = QtGui.QColor(255, 220, 0)


def draw_guide(painter: QtGui.QPainter, size: tuple[int, int]) -> None:
    """位置合わせ用の枠: ゲームの描画領域の端と、プレイヤー名の帯の位置。OBS の画面でゲームに重なっていれば合っている。"""
    from .ui.engine_nameplate import NAMEPLATE_BOXES, REFERENCE_SIZE

    w, h = size
    painter.save()
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    pen = QtGui.QPen(GUIDE_COLOR, max(2, round(h / 180)))
    pen.setJoinStyle(QtCore.Qt.PenJoinStyle.MiterJoin)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    half = pen.widthF() / 2
    painter.drawRect(QtCore.QRectF(half, half, w - pen.widthF(), h - pen.widthF()))
    # 中央の十字
    arm = h / 30
    painter.drawLine(QtCore.QPointF(w / 2 - arm, h / 2), QtCore.QPointF(w / 2 + arm, h / 2))
    painter.drawLine(QtCore.QPointF(w / 2, h / 2 - arm), QtCore.QPointF(w / 2, h / 2 + arm))
    # プレイヤー名の帯(対戦の画面)
    k = w / REFERENCE_SIZE[0]
    for x0, y0, x1, y1 in NAMEPLATE_BOXES.values():
        painter.drawRect(QtCore.QRectF(x0 * k, y0 * k, (x1 - x0) * k, (y1 - y0) * k))
    font = painter.font()
    font.setPixelSize(round(h / 30))
    font.setBold(True)
    painter.setFont(font)
    text = "Universal Engine Dock 位置合わせ用の枠\n黄色の枠がゲームの画面の端に、下の2つの四角がプレイヤー名の帯に重なれば合っています"
    rect = QtCore.QRectF(0, h * 0.2, w, h * 0.2)
    # ゲームの画面の上でも読めるよう、文字の後ろに暗い下地を敷く
    bounds = painter.boundingRect(rect, QtCore.Qt.AlignmentFlag.AlignCenter, text)
    pad = h / 60
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtGui.QColor(0, 0, 0, 170))
    painter.drawRoundedRect(bounds.adjusted(-pad, -pad, pad, pad), pad, pad)
    painter.setPen(GUIDE_COLOR)
    painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, i18n.tr(text))
    painter.restore()


OBS_GAMMA = 2.2  # OBS が混ぜる時に色を直線的な値に戻す指数(sRGB のおおよそ)
_REFERENCE_BACKGROUND = 0.5  # 濃さを合わせる時に仮に置く背景の明るさ(0〜1)


def match_obs_blending(image: QtGui.QImage) -> QtGui.QImage:
    """半透明の所の濃さ(アルファ)を、OBS のリニアな混ぜ方で画面(sRGB で混ぜる)と同じ見え方になるよう直した画像。

    色の明るさ c・濃さ a の点を明るさ b の背景に重ねると、画面では c·a + b·(1−a)、OBS では明るさを γ 乗した値で混ぜる。
    b を中間の明るさに置いて、OBS で混ぜた結果が画面と同じになる濃さ a' を求める。黒(c=0)なら背景によらず
    a' = 1 − (1−a)^γ(170/255 の黒の下地は 0.91 になる)。白っぽい点は少し薄くなる。不透明・透明の点は変えない。"""
    rgba = image.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)  # 乗算済みでない色
    w, h = rgba.width(), rgba.height()
    ptr = rgba.bits()
    ptr.setsize(rgba.sizeInBytes())
    pixels = np.frombuffer(ptr, np.uint8).reshape(h, rgba.bytesPerLine() // 4, 4)[:, :w]
    alpha = pixels[:, :, 3]
    ys, xs = np.nonzero((alpha > 0) & (alpha < 255))
    if len(ys) == 0:
        return image
    a = alpha[ys, xs].astype(np.float32) / 255
    color = pixels[ys, xs, :3].astype(np.float32) / 255
    c = color @ np.array([0.2126, 0.7152, 0.0722], np.float32)  # 色の明るさ
    b = _REFERENCE_BACKGROUND
    target = (c * a + b * (1 - a)) ** OBS_GAMMA  # 画面で混ぜた結果(を直線的な値にしたもの)
    lc, lb = c ** OBS_GAMMA, b ** OBS_GAMMA
    diff = lc - lb
    safe = np.abs(diff) > 0.02  # 色が背景と同じくらいの明るさなら、濃さを変えても見え方は変わらないのでそのまま
    fixed = np.where(safe, (target - lb) / np.where(safe, diff, 1), a)
    pixels[ys, xs, 3] = np.clip(np.round(np.clip(fixed, 0, 1) * 255), 1, 255).astype(np.uint8)
    return rgba


def encode_png(image: QtGui.QImage) -> bytes:
    image = match_obs_blending(image)
    data = QtCore.QByteArray()
    buf = QtCore.QBuffer(data)
    buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    # Qt の PNG の「画質」は圧縮の弱さ。90 だと 1080p で 8MB になり転送が詰まったので 50
    image.save(buf, "PNG", 50)
    buf.close()
    return bytes(data)


# ---- 取り込みから外したウィンドウ ----

WDA_EXCLUDEFROMCAPTURE = 0x00000011


def _excluded_from_capture(widget: QtWidgets.QWidget) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    affinity = wintypes.DWORD()
    try:
        ok = ctypes.windll.user32.GetWindowDisplayAffinity(wintypes.HWND(int(widget.winId())), ctypes.byref(affinity))
    except (AttributeError, OSError):
        return False
    return bool(ok) and affinity.value == WDA_EXCLUDEFROMCAPTURE


def hidden_windows() -> list[QtWidgets.QWidget]:
    """画面に出している Universal Engine Dock のウィンドウのうち、画面の取り込みから外したもの(= OBS に写らないもの)。"""
    return [w for w in QtWidgets.QApplication.topLevelWidgets()
            if w.isVisible() and w.testAttribute(QtCore.Qt.WidgetAttribute.WA_WState_Created) and _excluded_from_capture(w)]


# ---- Web サーバー ----


class ObsOverlayServer:
    """この PC(127.0.0.1)だけに見える Web サーバー。描いた画像を配る。publish は Qt の画面のスレッドから呼ぶ。"""

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._png = encode_png(_blank())
        self._version = 0
        self._last_request: float | None = None  # ページが最後に版の番号を聞いた時刻(time.monotonic)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._encoder: threading.Thread | None = None
        self._pending: QtGui.QImage | None = None  # PNG への変換を待つ最新の画像
        self._running = False
        self._busy = False  # 変換中

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        """サーバーを立てる。ポートが使われていれば OSError。"""
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path == "/":
                    body, kind = PAGE, "text/html; charset=utf-8"
                elif path == "/version":
                    with server._lock:
                        body = str(server._version).encode()
                        server._last_request = time.monotonic()
                    kind = "text/plain"
                elif path == "/frame.png":
                    with server._lock:
                        body = server._png
                    kind = "image/png"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:  # noqa: ANN002
                pass  # 50ms ごとの問い合わせを標準エラーに出さない

        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.port = self._httpd.server_address[1]  # port=0 の時は空いているポート
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="obs-overlay", daemon=True)
        self._thread.start()
        self._running = True
        self._encoder = threading.Thread(target=self._encode_loop, name="obs-overlay-png", daemon=True)
        self._encoder.start()

    def connected(self, now: float | None = None) -> bool:
        """OBS のページが読みに来ているか(CONNECTED_SEC 以内に問い合わせがあったか)。"""
        with self._lock:
            last = self._last_request
        return last is not None and (time.monotonic() if now is None else now) - last <= CONNECTED_SEC

    def _encode_loop(self) -> None:
        while True:
            with self._wake:
                while self._running and self._pending is None:
                    self._wake.wait()
                if self._pending is None:
                    return
                image, self._pending = self._pending, None
                self._busy = True
            png = encode_png(image)
            with self._wake:
                self._png = png
                self._version += 1
                self._busy = False
                self._wake.notify_all()

    def publish(self, image: QtGui.QImage) -> None:
        if not self._running:  # サーバーを立てる前・止めた後はその場で変換する
            png = encode_png(image)
            with self._lock:
                self._png = png
                self._version += 1
            return
        with self._wake:
            self._pending = image  # 変換が追いつかなければ古い画像は捨てる
            self._wake.notify()

    def flush(self, timeout: float = 2.0) -> None:
        """変換待ち・変換中の画像を変換し終えるまで待つ(テスト用)。"""
        with self._wake:
            self._wake.wait_for(lambda: self._pending is None and not self._busy, timeout)

    def stop(self) -> None:
        with self._wake:
            self._running = False
            self._wake.notify_all()
        if self._encoder is not None:
            self._encoder.join(timeout=2.0)
            self._encoder = None
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def _blank(size: tuple[int, int] = FRAME_SIZE) -> QtGui.QImage:
    image = QtGui.QImage(size[0], size[1], QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    return image


# ---- メイン画面から使う部品 ----


class ObsOutput(QtCore.QObject):
    """OBS 出力のオン・オフ、サーバー、定期的な描き写しをまとめたもの。メイン画面が1つ持つ。

    window_provider はゲームのウィンドウを返す関数(find_game_window)。分からない時(手動キャリブレーション)は、
    取り込みから外したウィンドウのあるディスプレイ全体を描画領域とみなす。"""

    changed = QtCore.pyqtSignal()  # 状態(つながっているか・エラー)が変わった

    def __init__(
        self,
        window_provider: Callable[[], "GameWindow | None"],
        widgets_provider: Callable[[], list[QtWidgets.QWidget]] = hidden_windows,
        path: Path | None = None,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        self.config = load_config(path)
        self.window_provider = window_provider
        self.widgets_provider = widgets_provider
        self.server: ObsOverlayServer | None = None
        self.error = ""
        self.guide = False  # 位置合わせ用の枠を出す
        self.allowed = False  # OBS 出力プラグインが有効か
        self._window: GameWindow | None = None
        self._window_checked = -1e9
        self._last: QtGui.QImage | None = None
        self._last_status = ""
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(RENDER_MS)
        self._timer.timeout.connect(self.tick)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.port if self.server is not None else self.config.port}/"

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def set_allowed(self, allowed: bool) -> None:
        """OBS 出力プラグインの有効・無効。無効ならサーバーを止める。"""
        self.allowed = allowed
        self._apply()

    def set_enabled(self, enabled: bool) -> None:
        """「OBS に出す」の切り替え。保存して、サーバーを立てる・止める。"""
        if enabled != self.config.enabled:
            self.config.enabled = enabled
            save_config(self.config, self._path)
        self._apply()

    def set_guide(self, on: bool) -> None:
        self.guide = on
        self._last = None  # すぐ描き直す

    def _apply(self) -> None:
        wanted = self.allowed and self.config.enabled
        if wanted and self.server is None:
            server = ObsOverlayServer(self.config.port)
            try:
                server.start()
            except OSError:
                self.error = (f"ポート {self.config.port} がほかのアプリで使われているので、OBS 出力を始められません"
                              f"(config/obs_overlay.json の port で変えられます)")
            else:
                self.server, self.error = server, ""
                self._last = None
                self._timer.start()
        elif not wanted:
            self.stop()
            self.error = ""
        self._emit_if_changed()

    def stop(self) -> None:
        self._timer.stop()
        if self.server is not None:
            self.server.stop()
            self.server = None
        self._last = None

    def connected(self) -> bool:
        return self.server is not None and self.server.connected()

    def status_text(self) -> str:
        if self.error:
            return self.error
        if self.server is None:
            return ""
        if self.connected():
            return "OBS とつながっています"
        return "OBS からまだ読まれていません(OBS にブラウザソースを追加すると「つながっています」に変わります)"

    def _emit_if_changed(self) -> None:
        status = self.status_text()
        if status != self._last_status:
            self._last_status = status
            self.changed.emit()

    def game_window(self) -> "GameWindow | None":
        """ゲームのウィンドウ(WINDOW_REFRESH_SEC ごとに確かめ直す)。"""
        now = time.monotonic()
        if now - self._window_checked >= WINDOW_REFRESH_SEC:
            self._window_checked = now
            try:
                self._window = self.window_provider()
            except Exception:  # noqa: BLE001  ウィンドウ探しの失敗で OBS 出力を止めない
                self._window = None
        return self._window

    def frame_area(self, widgets: list[QtWidgets.QWidget]) -> tuple[float, float, float, float] | None:
        """画像に対応させる描画領域(物理ピクセル)。ゲームのウィンドウが分からなければ、最初のウィンドウのあるディスプレイ全体。"""
        window = self.game_window()
        if window is not None:
            return content_area((window.left, window.top, window.width, window.height))
        for widget in widgets:
            if widget.screen() is not None:
                return content_area(native_rect(widget.screen()))
        return None

    def render(self) -> QtGui.QImage | None:
        """今の表示を写した画像(描画領域が分からなければ None)。"""
        widgets = list(self.widgets_provider())
        area = self.frame_area(widgets)
        if area is None:
            return _blank() if self.guide or not widgets else None
        return compose_frame(area, widgets, self.guide)

    def tick(self) -> None:
        self._emit_if_changed()
        # OBS が読みに来ていない間は描かない(OBS を開いていない時に画面のスレッドを使わない)
        if self.server is None or not self.connected():
            return
        image = self.render()
        if image is None or (self._last is not None and image == self._last):
            return
        self._last = image
        self.server.publish(image)
