"""「全消し」バナーの検出と、その画素を盤面の読み取りから除外するためのマスク

全消しボーナスを持っている間、盤面の 9〜11段目・1〜5列目あたりに「全消し」の文字が表示され
続ける。文字は白い本体と
オレンジの縁取りで、空マスの上では黄ぷよ・おじゃまとして読まれ、偽の4連結(YYYY)が取り込み待ちを
起こし、確定盤面にも混入した。ぷよは文字より手前に描かれる(文字はぷよの無い所だけに見える)ので、
文字の画素だけを分類から除けば、その下の空マスは空、手前のぷよは通常どおり読める。

検出はテンプレート一致(カラー、TM_CCOEFF_NORMED)。テンプレートは実機の 000_turn1.png(空盤面に
文字だけ)を盤面座標(1マス 32px)に変換して切り出したもの。文字は出現直後に少し動く(y が 12px 違う
フレームがある)ので、9〜11段目付近を探索する。
得点の実測(debug_frames_history 2,819枚): 文字あり ≥0.66(ぷよに大きく隠れた場合)、文字なし ≤0.49。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

TEMPLATE_PATH = Path(__file__).parent / "templates" / "all_clear_banner.png"

# 探索範囲(盤面座標の y、1マス 32px。row 1〜5 = 12〜8段目)
SEARCH_Y0 = 32
SEARCH_Y1 = 224
# テンプレート全体の一致がこれ以上なら文字あり
BANNER_SCORE_MIN = 0.58
# 一致度がこれ以上で、文字の画素の色まで合う割合(インクの一致率)が BANNER_INK_CONFIRM_MIN 以上でも文字あり。
# テンプレートは背景(別のキャラクターの絵)ごと切り出してあるので、
# 背景の違う盤面では一致度が 0.48〜0.56 に下がって文字を検出できず、文字を黄ぷよ・おじゃまと読み、先読みの盤面と違うとして
# 次の手を考え直し、組ぷよが約0.6秒止まった。09:06〜09:20 の保存画面 1,368 盤面では、文字ありは
# 一致度 0.478 以上・インクの一致率 0.751 以上、文字なしは一致度 0.338 以下
BANNER_SCORE_WITH_INK_MIN = 0.45
BANNER_INK_CONFIRM_MIN = 0.7
# テンプレートを縮める・伸ばす倍率(文字の大きさはウィンドウ・場面で少し違う。09:20 の実機では約0.92倍で、大きさが合わないと
# 文字の画素の除き方がずれ、端が黄ぷよと読まれ続けた)
BANNER_SCALES = (0.88, 0.92, 0.96, 1.0, 1.04)
# 倍率1の一致度がこれ未満なら文字は無いとみなし、ほかの倍率は試さない(文字なしの盤面は 0.338 以下、文字ありは倍率1でも 0.478 以上)
BANNER_SCALE_SEARCH_MIN = 0.40
# 文字の画素を広げる時に、テンプレートの枠の外へ見る幅(盤面座標の px)
BANNER_GROW_MARGIN = 6
# テンプレートと画素が一致した(BGR の差の合計がこれ未満)所を文字の画素とみなす
BANNER_PIXEL_DIFF_MAX = 90
# 一度検出した文字は、ぷよに大きく隠れてテンプレート全体の一致度が下がっても、インクの画素がこの割合以上
# 一致している間は見えているとみなす(実測: 隠れた文字 0.65〜0.78、文字なしの盤面 ≤0.55 は
# カットインの白と橙で、文字を検出していない状態では使わない)
BANNER_KEEP_INK_FRAC_MIN = 0.3
# インクの一致がこの回数続けて下回ったら文字は消えたとみなす
BANNER_LOST_FRAMES = 10


@dataclass(frozen=True)
class BannerMatch:
    score: float
    x: int  # 盤面座標での左上
    y: int
    scale: float = 1.0  # テンプレートの倍率(BANNER_SCALES)


class AllClearBannerDetector:
    """テンプレート一致で文字を探し、見つけた後は隠れても位置を覚えて追い続ける(盤面ごとに1個持つ)。"""

    def __init__(self, template_path: Path = TEMPLATE_PATH) -> None:
        self.active: BannerMatch | None = None  # 追跡中の文字の位置
        self._lost = 0
        tpl = cv2.imread(str(template_path))
        if tpl is None:
            raise FileNotFoundError(f"全消しバナーのテンプレートが読めません: {template_path}")
        self.template = tpl
        hsv = cv2.cvtColor(tpl, cv2.COLOR_BGR2HSV)
        h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        # 文字のインク(白い本体+オレンジの縁取り)。透けている背景は除く
        self.ink = ((s < 70) & (v > 170)) | ((h >= 5) & (h <= 25) & (s > 120) & (v > 150))
        # 倍率ごとのテンプレートとインク
        self._scaled: dict[float, tuple[np.ndarray, np.ndarray]] = {}
        for k in BANNER_SCALES:
            if k == 1.0:
                self._scaled[k] = (tpl, self.ink)
                continue
            t = cv2.resize(tpl, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
            ink = cv2.resize(self.ink.astype(np.uint8), (t.shape[1], t.shape[0]), interpolation=cv2.INTER_NEAREST).astype(bool)
            self._scaled[k] = (t, ink)

    def _best(self, warped_bgr: np.ndarray) -> BannerMatch | None:
        roi = warped_bgr[SEARCH_Y0:SEARCH_Y1, :]
        best: BannerMatch | None = None
        # 速さのため: 追跡中は見つけた倍率だけ、そうでなければまず倍率1で探し、文字の無い盤面(一致度が明らかに低い)なら
        # ほかの倍率は試さない(観測は毎秒約30回・2盤面。全部の倍率を毎回試すと1盤面 4ms → 9ms になった)
        if self.active is not None:
            scales = [self.active.scale]
        else:
            scales = [1.0] + [k for k in self._scaled if k != 1.0]
        for k in scales:
            tpl, _ink = self._scaled[k]
            if best is not None and k != 1.0 and best.scale == 1.0 and best.score < BANNER_SCALE_SEARCH_MIN:
                break
            th, tw = tpl.shape[:2]
            if roi.shape[0] < th or roi.shape[1] < tw:
                continue
            result = cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(result)
            if np.isfinite(score) and (best is None or score > best.score):
                best = BannerMatch(float(score), int(loc[0]), int(loc[1]) + SEARCH_Y0, k)
        return best

    def _confirmed(self, warped_bgr: np.ndarray, best: BannerMatch | None) -> bool:
        """最良の位置に文字があるか: 一致度が高いか、中くらいでもインクの画素の色まで合っているか。"""
        if best is None:
            return False
        if best.score >= BANNER_SCORE_MIN:
            return True
        return best.score >= BANNER_SCORE_WITH_INK_MIN and self._ink_frac(warped_bgr, best) >= BANNER_INK_CONFIRM_MIN

    def find(self, warped_bgr: np.ndarray) -> BannerMatch | None:
        """状態を持たない検出(テンプレート全体の一致度と、インクの一致率で判定)。"""
        best = self._best(warped_bgr)
        return best if self._confirmed(warped_bgr, best) else None

    def _ink_frac(self, warped_bgr: np.ndarray, match: BannerMatch) -> float:
        tpl, ink = self._scaled.get(match.scale, (self.template, self.ink))
        th, tw = tpl.shape[:2]
        return float(self.pixel_mask(warped_bgr, match)[match.y : match.y + th, match.x : match.x + tw].sum() / max(1, ink.sum()))

    def observe(self, warped_bgr: np.ndarray) -> tuple[np.ndarray | None, float]:
        """1フレーム観測し、(文字の画素マスク or None, 一致度) を返す。

        一致度が閾値以上なら文字を検出して位置を覚える。以後は一致度が下がっても、覚えた位置(または
        今回の最良位置)でインクの画素が BANNER_KEEP_INK_FRAC_MIN 以上一致していれば見えているとみなす。
        """
        best = self._best(warped_bgr)
        if self._confirmed(warped_bgr, best):
            self.active, self._lost = best, 0
            return self.full_mask(warped_bgr, best), best.score
        if self.active is None:
            return None, 0.0
        candidates = [self.active] + ([best] if best is not None else [])
        match = max(candidates, key=lambda m: self._ink_frac(warped_bgr, m))
        frac = self._ink_frac(warped_bgr, match)
        if frac >= BANNER_KEEP_INK_FRAC_MIN:
            self._lost = 0
            self.active = BannerMatch(frac, match.x, match.y, match.scale)
            return self.full_mask(warped_bgr, match), frac
        self._lost += 1
        if self._lost >= BANNER_LOST_FRAMES:
            self.active = None
            return None, 0.0
        return self.full_mask(warped_bgr, match), frac  # 消えたと確定するまでは覚えた位置で除く(害はない)

    def pixel_mask(self, warped_bgr: np.ndarray, match: BannerMatch) -> np.ndarray:
        """文字が実際に見えている画素(テンプレートのインクと一致する所)の bool マスク(盤面全体の大きさ)。
        手前にぷよが描かれている所はテンプレートと違う色なので含まれない。"""
        tpl, ink = self._scaled.get(match.scale, (self.template, self.ink))
        th, tw = tpl.shape[:2]
        mask = np.zeros(warped_bgr.shape[:2], dtype=bool)
        region = warped_bgr[match.y : match.y + th, match.x : match.x + tw]
        if region.shape[:2] != (th, tw):
            return mask
        diff = np.abs(region.astype(np.int32) - tpl.astype(np.int32)).sum(axis=2)
        hit = (ink & (diff < BANNER_PIXEL_DIFF_MAX)).astype(np.uint8)
        # 縁取りの点模様やアンチエイリアスの隙間を埋める
        hit = cv2.dilate(hit, np.ones((3, 3), np.uint8))
        mask[match.y : match.y + th, match.x : match.x + tw] = hit.astype(bool)
        return mask

    def full_mask(self, warped_bgr: np.ndarray, match: BannerMatch) -> np.ndarray:
        """盤面の読み取りから除く画素: テンプレートと色まで合う文字の画素(pixel_mask)に、それとつながっている白・橙の画素を足す。

        画面の文字はテンプレートと形が少し違い(縁取りの太さ・傾き)、外側の橙の縁取りや白い本体の端が残って、
        そのマスが黄ぷよ・おじゃまと読まれ続けた。文字の枠(少し広げた範囲)の中で、
        合った画素から白・橙の画素をたどって広げる。手前のぷよは体の色で途切れるので広がらない。黄ぷよの色は橙に含めない。"""
        mask = self.pixel_mask(warped_bgr, match)
        tpl, _ink = self._scaled.get(match.scale, (self.template, self.ink))
        th, tw = tpl.shape[:2]
        y0, x0 = max(0, match.y - BANNER_GROW_MARGIN), max(0, match.x - BANNER_GROW_MARGIN)
        y1 = min(warped_bgr.shape[0], match.y + th + BANNER_GROW_MARGIN)
        x1 = min(warped_bgr.shape[1], match.x + tw + BANNER_GROW_MARGIN)
        hsv = cv2.cvtColor(warped_bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
        h, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        inkish = (((sat < 70) & (val > 170)) | ((h >= 8) & (h <= 20) & (sat > 120) & (val > 120))).astype(np.uint8)
        count, labels = cv2.connectedComponents(inkish, connectivity=8)
        seeds = np.unique(labels[mask[y0:y1, x0:x1] & (inkish > 0)])
        grown = np.isin(labels, seeds[seeds > 0])
        grown = cv2.dilate(grown.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        mask[y0:y1, x0:x1] |= grown
        return mask


_default: AllClearBannerDetector | None = None


def default_detector() -> AllClearBannerDetector:
    global _default
    if _default is None:
        _default = AllClearBannerDetector()
    return _default
