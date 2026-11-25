"""プレイヤー名の帯の上に、そのサイドで使っている思考エンジンの名前を重ねて出す。ぷよ・テトリスで共通。

ぷよぷよテトリス2 の対戦画面では、盤面の下にプレイヤー名の色付きの帯がある(位置はぷよ・テトリスで同じ)。
帯の内側(縁は残す)を同じ色で塗り、エンジン名を本家の名前と同じ形・大きさ・位置の白い文字で書く。

決まりごと:
- ほかのオーバーレイと同じく、画面の取り込み(mss など)から外したウィンドウにする。外せない環境では出さない
  (取り込んだ画像に Universal Engine Dock の表示が混ざらないように)。外すのは表示する前に行い、外れたことを確かめてから表示する
- 対戦の配置の時だけ出す(一人用の画面には帯が無い)。ゲームのウィンドウが分からない時(手動キャリブレーション)も出さない
- 位置はゲームのウィンドウの描画領域(16:9。黒帯を除く)から決める。支援モードの間にウィンドウが動けば支援モード自体が止まる
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping

from PyQt6 import QtCore, QtGui, QtWidgets

from .screen_map import screen_of_rect, to_logical_rect, to_physical

# 基準の大きさ(描画領域をこの大きさとみなした座標)
REFERENCE_SIZE = (1280, 720)
# 帯の色の付いた内側(白い縁の内側。x0, y0, x1, y1)。2P は 1P の左右反転。
# 利用者の実機のスクリーンショット(1600×900)で、白い縁と色の境目
# (半分ずつ混ざった画素)を測り直した。1P の左端は顔の絵の枠の右端(帯は絵の下に続くので、絵には重ねない)
NAMEPLATE_BOXES: dict[str, tuple[float, float, float, float]] = {
    "1p": (168.0, 658.0, 490.0, 682.0),
    "2p": (790.0, 658.0, 1112.0, 682.0),
}
PLATE_RADIUS = 2.5  # 内側の角の丸み(基準の px)
# 帯の色(1P は青、2P は赤。暗くなっていない場面で測った)と文字の色
PLATE_COLORS = {"1p": QtGui.QColor(0, 113, 188), "2p": QtGui.QColor(194, 56, 53)}
TEXT_COLOR = QtGui.QColor(255, 255, 255)
# 文字(本家の名前の表示に合わせる)。英数字は Arial の太字(本家の文字と形を比べて一番近かった)、日本語は游ゴシックの太字
FONT_FAMILIES = ["Arial", "Yu Gothic", "Meiryo"]
# 文字の送り幅の中心の x、ベースライン(文字の下端)の y、大文字の高さ(基準の px)。本家の「YamaNEKO_KasugAI」「COM1」の
# 文字の範囲を測り、Arial の字形の余白を除いて求めた値。中心は帯の全体(顔の絵の下も含む)の中央なので、
# 色の付いた内側の中央より絵の側に寄る
TEXT_CENTER_X = {"1p": 324.6, "2p": 954.0}
TEXT_BASELINE_Y = 676.5
TEXT_CAP_HEIGHT = 13.9
TEXT_MARGIN = 4.0  # 文字と帯の端の間の最小の余白(基準の px)。長い名前はこれより内側で「…」にする


def content_rect(width: int, height: int) -> tuple[float, float, float]:
    """クライアント領域 width×height の中の描画領域(16:9、中央寄せ)の (左の余白, 上の余白, 基準からの倍率)。"""
    rw, rh = REFERENCE_SIZE
    scale = min(width / rw, height / rh)
    return (width - rw * scale) / 2, (height - rh * scale) / 2, scale


def to_screen(client: tuple[int, int, int, int], x: float, y: float) -> tuple[float, float]:
    """基準の座標 (x, y) を、画面の物理ピクセルにする。"""
    left, top, width, height = client
    ox, oy, scale = content_rect(width, height)
    return left + ox + x * scale, top + oy + y * scale


def nameplate_area(client: tuple[int, int, int, int], side: str) -> tuple[float, float, float, float]:
    """side の帯の色の付いた内側 (左, 上, 右, 下。物理ピクセル。小数のまま)。"""
    x0, y0, x1, y1 = NAMEPLATE_BOXES[side]
    return (*to_screen(client, x0, y0), *to_screen(client, x1, y1))


def nameplate_rect(client: tuple[int, int, int, int], side: str) -> tuple[int, int, int, int]:
    """ゲームのクライアント領域 (左, 上, 幅, 高さ。物理ピクセル) から、side の札のウィンドウ (左, 上, 幅, 高さ。物理ピクセル)。
    色の付いた内側を少し広く囲む(内側の端は小数なので、ウィンドウの中で小数の位置に描く)。"""
    x0, y0, x1, y1 = nameplate_area(client, side)
    left, top = math.floor(x0) - 1, math.floor(y0) - 1
    return left, top, math.ceil(x1) + 1 - left, math.ceil(y1) + 1 - top


class EngineNameplate(QtWidgets.QWidget):
    """帯の内側に重ねる、エンジン名の札。マウスは通し、フォーカスも取らない。

    client を渡すと、帯の色の付いた内側を小数の位置まで合わせて塗り、文字は本家の名前と同じ位置・大きさに書く。"""

    def __init__(self, side: str, text: str, rect: tuple[int, int, int, int],
                 client: tuple[int, int, int, int] | None = None) -> None:
        super().__init__()
        self.side = side
        self.text = text
        self.client = client
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(to_logical_rect(*rect, screen_of_rect(*rect)))

    def _local(self, x: float, y: float) -> QtCore.QPointF:
        """物理ピクセルの点を、このウィンドウの中の論理ピクセルにする。"""
        screen = self.screen()
        ox, oy = to_physical(self.x(), self.y(), screen)
        dpr = screen.devicePixelRatio() if screen is not None else 1.0
        return QtCore.QPointF((x - ox) / dpr, (y - oy) / dpr)

    def parts(self) -> tuple[QtCore.QRectF, float, QtCore.QPointF, float, float]:
        """(色の付いた内側, 角の丸み, 文字の中心とベースライン, 大文字の高さ, 文字に使える幅)。ウィンドウの中の論理ピクセル。"""
        if self.client is None:  # 位置の基準が無い時(テストなど)はウィンドウいっぱい
            rect = QtCore.QRectF(self.rect())
            unit = rect.height() / (NAMEPLATE_BOXES["1p"][3] - NAMEPLATE_BOXES["1p"][1])
            center = QtCore.QPointF(rect.center().x(), rect.top() + (TEXT_BASELINE_Y - NAMEPLATE_BOXES["1p"][1]) * unit)
            return rect, PLATE_RADIUS * unit, center, TEXT_CAP_HEIGHT * unit, rect.width() - TEXT_MARGIN * 2 * unit
        x0, y0, x1, y1 = NAMEPLATE_BOXES[self.side]
        top_left = self._local(*to_screen(self.client, x0, y0))
        bottom_right = self._local(*to_screen(self.client, x1, y1))
        rect = QtCore.QRectF(top_left, bottom_right)
        unit = rect.width() / (x1 - x0)  # 基準の 1px が何論理ピクセルか
        # 境目の画素はゲーム自身が縁の白と半分ずつ混ぜて描いている。その画素に重ねて塗ると帯の色が濃くなるので、
        # 物理ピクセルの半分だけ内側を塗り、境目はゲームの画素をそのまま見せる
        screen = self.screen()
        inset = 0.5 / (screen.devicePixelRatio() if screen is not None else 1.0)
        rect.adjust(inset, inset, -inset, -inset)
        cx = TEXT_CENTER_X[self.side]
        center = self._local(*to_screen(self.client, cx, TEXT_BASELINE_Y))
        room = 2 * (min(cx - x0, x1 - cx) - TEXT_MARGIN) * unit
        return rect, PLATE_RADIUS * unit, center, TEXT_CAP_HEIGHT * unit, room

    def paintEvent(self, event: QtCore.QEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect, radius, center, cap, room = self.parts()
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(PLATE_COLORS.get(self.side, PLATE_COLORS["1p"]))
        painter.drawRoundedRect(rect, radius, radius)
        # 文字は大きな文字の形(パス)を縮めて描く(文字の大きさを整数の px に丸めず、本家の大きさにそろえる)
        font = name_font()
        metrics = QtGui.QFontMetricsF(font)
        k = cap / metrics.capHeight()
        text = metrics.elidedText(self.text, QtCore.Qt.TextElideMode.ElideRight, room / k)
        path = QtGui.QPainterPath()
        path.addText(0, 0, font, text)
        painter.translate(center.x() - metrics.horizontalAdvance(text) * k / 2, center.y())
        painter.scale(k, k)
        painter.setBrush(TEXT_COLOR)
        painter.drawPath(path)
        painter.end()


def name_font() -> QtGui.QFont:
    """名前の文字(大きさ 100px。描く時に縮める)。"""
    font = QtGui.QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setBold(True)
    font.setPixelSize(100)
    return font


def show_engine_nameplates(
    client: tuple[int, int, int, int] | None,
    names: Mapping[str, str],
    exclude: Callable[[QtWidgets.QWidget], bool],
) -> list[EngineNameplate]:
    """names(サイド "1p"/"2p" → エンジン名)の札を出して返す。client はゲームのクライアント領域(物理ピクセル)。

    exclude は画面の取り込みから外す関数(成功したら True)。外せなかった札は出さずに捨てる。"""
    if client is None:
        return []
    plates = []
    for side, name in names.items():
        if side not in NAMEPLATE_BOXES or not name:
            continue
        plate = EngineNameplate(side, name, nameplate_rect(client, side), client)
        plate.winId()  # 表示する前にウィンドウを作り、取り込みから外す
        if not exclude(plate):
            plate.deleteLater()
            continue
        plate.show()
        plates.append(plate)
    return plates


def close_nameplates(plates: list[EngineNameplate]) -> None:
    for plate in plates:
        plate.close()
        plate.deleteLater()
    plates.clear()
