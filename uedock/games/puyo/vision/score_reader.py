"""ゲームの点数表示(盤面の下の8桁)を読む。

連鎖の点数は、点滅から作り直した盤面で連鎖を再現して見積もってきたが、見えない13段目や読み違いで外れることがある
。ゲームは盤面の下に合計点を
8桁で出し、連鎖の各段では「40×  8」(消した個数×10 × 倍率)を出してから合計を足す。連鎖が始まる前と後の合計の差が、ゲーム自身が数えた連鎖の点数になる。

読み方: 点数欄を8つのマス(1文字ずつ、等間隔)に分け、各マスの白い塗り(数字の中身)を、数字・「×」の見本と比べる。
見本はゲームの画面から作るので、リポジトリには入れない(config/ に置く。tools/make_puyo_score_digits.py で作る)。
見本が無ければ読まない(今までどおり再現だけを使う)。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

# 1920×1080 の画面での1マスの大きさ(px)と、マスの数
CELL_W, CELL_H = 42, 58
CELLS = 8
# 盤面の格子(列・段)で表した点数欄の位置: 左端・1マスの幅(列)、上端・下端(段。可視12段の下端が 12)。
# 1920×1080 の録画で、盤面(400×723px)の下の8桁を測った(左右の盤面で同じ位置)
STRIP_U0, STRIP_PITCH = 0.4725, 0.63
STRIP_V0, STRIP_V1 = 12.10, 13.06
# 読む時の左右の余白(基準の px)。位置合わせのずれをこの範囲で探す
MARGIN = 6
# 3段階の明るさ: 数字の白い塗り(+1)・数字の暗い縁(-1)・それ以外(0)。白い塗りだけで比べると、塗りの多い 8 に 3・6・9 が
# 似て読み違えた。縁も比べると、1つ抜きの確かめで 216文字すべて正しく読めた
WHITE_MIN = 200
DARK_MAX = 100
# 何も書かれていないマス(白い塗りの割合がこれ未満)
BLANK_MAX = 0.08
# 比べる時の縮小(速さのため。半分でも1つ抜きの確かめは変わらない)と、各マスで探す左右・上下のずれ(縮小後の px)
DOWN = 2
SHIFT = 2
# 見本との違い(3段階の差の平均、0〜2)がこれより大きいマスは読めない
MATCH_MAX = 0.45
TIMES = "x"  # 「×」の見本の名前
# 見本の数の上限(名前ごと)
EXEMPLARS_PER_NAME = 8


def strip_grid_box() -> tuple[float, float, float, float]:
    """点数欄の範囲を盤面の格子で (u0, v0, u1, v1)。"""
    return STRIP_U0, STRIP_V0, STRIP_U0 + STRIP_PITCH * CELLS, STRIP_V1


@dataclass(frozen=True)
class ScoreReading:
    """点数欄の読み。合計(8桁)か、連鎖の段の「a×b」のどちらか。"""

    total: int | None = None
    step: tuple[int, int] | None = None  # (消した個数×10, 倍率)
    text: str = ""


def ternary(gray: np.ndarray) -> np.ndarray:
    """白い塗り +1・暗い縁 -1・それ以外 0。"""
    return np.where(gray >= WHITE_MIN, 1, np.where(gray <= DARK_MAX, -1, 0)).astype(np.float32)


def _half(t: np.ndarray) -> np.ndarray:
    return cv2.resize(t, (t.shape[1] // DOWN, t.shape[0] // DOWN), interpolation=cv2.INTER_AREA)


def crop_strip(frame_bgr: np.ndarray, geometry, origin: tuple[int, int]) -> np.ndarray | None:  # noqa: ANN001
    """画面から点数欄を切り出し、基準の大きさ(余白つき)の白黒画像にする。範囲が画面の外なら None。"""
    u0, v0, u1, v1 = strip_grid_box()
    (x0, y0) = geometry.grid_to_screen(u0, v0)
    (x1, y1) = geometry.grid_to_screen(u1, v1)
    scale = (x1 - x0) / (CELL_W * CELLS)
    pad = MARGIN * scale
    left = int(round(x0 - pad - origin[0]))
    right = int(round(x1 + pad - origin[0]))
    top = int(round(y0 - origin[1]))
    bottom = int(round(y1 - origin[1]))
    h, w = frame_bgr.shape[:2]
    if left < 0 or top < 0 or right > w or bottom > h or right <= left or bottom <= top:
        return None
    gray = cv2.cvtColor(frame_bgr[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (CELL_W * CELLS + 2 * MARGIN, CELL_H), interpolation=cv2.INTER_AREA)


def _cell(strip_t: np.ndarray, k: int) -> np.ndarray:
    """余白つきの帯(3段階)から k 番目のマス(ずれ無しの位置)。"""
    x = MARGIN + k * CELL_W
    return strip_t[:, x:x + CELL_W]


def _parse(chars: list[str]) -> ScoreReading | None:
    text = "".join(chars)
    if all(c.isdigit() for c in chars):
        return ScoreReading(total=int(text), text=text)
    if TIMES in chars:
        i = chars.index(TIMES)
        left = text[:i].strip()
        right = text[i + 1:].strip()
        if left.isdigit() and right.isdigit():
            return ScoreReading(step=(int(left), int(right)), text=text)
    return None


class ScoreReader:
    """点数欄を読む。exemplars は見本のマス(3段階、CELL_H×CELL_W)、names はその名前("0"〜"9"・"x")。
    各マスを、上下左右に少しずらしながら一番近い見本の名前で読む。"""

    def __init__(self, exemplars: np.ndarray, names: list[str]) -> None:
        self.names = list(names)
        ex = np.stack([_half(e.astype(np.float32)) for e in exemplars]) if len(exemplars) else np.zeros((0, 1, 1), np.float32)
        self._h, self._w = ex.shape[1], ex.shape[2]
        self._ex = ex.reshape(len(ex), -1)

    @classmethod
    def load(cls, path: Path) -> "ScoreReader | None":
        """見本のファイル(npz: cells・names)を読む。無ければ None。"""
        if not Path(path).exists():
            return None
        data = np.load(path)
        if "cells" not in data.files or len(data["cells"]) == 0:
            return None
        return cls(data["cells"], [str(n) for n in data["names"]])

    def _classify(self, strip_half: np.ndarray, k: int) -> str:
        x0 = (MARGIN + k * CELL_W) // DOWN
        best, name = None, "?"
        H, W = strip_half.shape
        for dy in range(-SHIFT, SHIFT + 1):
            for dx in range(-SHIFT, SHIFT + 1):
                x, y = x0 + dx, dy
                if x < 0 or x + self._w > W or y < 0 or y + self._h > H:
                    # 上下は帯の外に出ないように、はみ出す分は端で切る
                    if not (0 <= x and x + self._w <= W):
                        continue
                    y = min(max(y, 0), H - self._h)
                c = strip_half[y:y + self._h, x:x + self._w].reshape(1, -1)
                d = np.abs(self._ex - c).mean(axis=1)
                i = int(d.argmin())
                if best is None or d[i] < best:
                    best, name = float(d[i]), self.names[i]
        return name if best is not None and best <= MATCH_MAX else "?"

    def read_strip(self, strip_gray: np.ndarray) -> ScoreReading | None:
        """crop_strip の画像を読む。どれかのマスが読めなければ None(連鎖の演出・画面の切り替わりなど)。"""
        t = ternary(strip_gray)
        half = _half(t)
        chars = []
        for k in range(CELLS):
            if (_cell(t, k) > 0.5).mean() < BLANK_MAX:
                chars.append(" ")
                continue
            ch = self._classify(half, k)
            if ch == "?":
                return None
            chars.append(ch)
        return _parse(chars)

    def read(self, frame_bgr: np.ndarray, geometry, origin: tuple[int, int]) -> ScoreReading | None:  # noqa: ANN001
        strip = crop_strip(frame_bgr, geometry, origin)
        return self.read_strip(strip) if strip is not None else None


def build_exemplars(samples: list[tuple[np.ndarray, str]]) -> tuple[np.ndarray, list[str]]:
    """見本を作る。samples は (crop_strip の画像, 8文字のラベル)。ラベルは数字・「x」(×)・空白(何も無いマス)。
    名前ごとに EXEMPLARS_PER_NAME 個まで、ラベルのマスをそのまま見本にする。"""
    cells, names = [], []
    count: dict[str, int] = {}
    for strip, label in samples:
        if len(label) != CELLS:
            raise ValueError(f"ラベルは{CELLS}文字にしてください: {label!r}")
        t = ternary(strip)
        for k, ch in enumerate(label):
            if ch == " " or count.get(ch, 0) >= EXEMPLARS_PER_NAME:
                continue
            cells.append(_cell(t, k).astype(np.int8))
            names.append(ch)
            count[ch] = count.get(ch, 0) + 1
    return (np.stack(cells) if cells else np.zeros((0, CELL_H, CELL_W), np.int8)), names


def save_exemplars(cells: np.ndarray, names: list[str], path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, cells=cells, names=np.array(names))
