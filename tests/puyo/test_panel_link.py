"""情報パネルの相手の行を、相手側の自分の行にそろえる(overlay/panel_link.py)。

"""

from __future__ import annotations

import unittest

from uedock.games.puyo.engine.ama_client import AttackInfo, FieldAnalysis
from uedock.games.puyo.overlay.panel_link import PanelLink
from uedock.games.puyo.overlay.renderer import OverlayDrawData


def _analysis(count: int, score: int) -> FieldAnalysis:
    a = AttackInfo(count=count, score=score, garbage=score // 70, x=0, rotation="UP", frame=0, all_clear=False)
    return FieldAnalysis(immediate=a, max=a, harass=a, potential=0, garbage_max=score // 70)


def _data(self_score: int, enemy_score: int) -> OverlayDrawData:
    return OverlayDrawData(axis_cell=(12, 0), child_cell=(11, 0), axis_color="red", child_color="blue", label="build",
                           self_analysis=_analysis(5, self_score), enemy_analysis=_analysis(5, enemy_score))


class PanelLinkTest(unittest.TestCase):
    def setUp(self) -> None:
        self.shown: dict[str, list] = {"1p": [], "2p": []}
        self.link = PanelLink({side: self.shown[side].append for side in ("1p", "2p")})

    def test_enemy_row_follows_the_other_side_self_row(self) -> None:
        # 1P の思考: 相手(2P)は 3,000点と見た。2P はまだ思考が無いので、1P の見立てのまま出す
        self.link.update("1p", _data(1_800, 3_000))
        self.assertEqual(self.shown["1p"][-1].enemy_analysis.max.score, 3_000)
        # 2P の思考: 自分は 2,840点。2P の相手の行は 1P の自分(1,800点)、1P の相手の行は 2P の自分(2,840点)になる
        self.link.update("2p", _data(2_840, 3_040))
        self.assertEqual(self.shown["2p"][-1].enemy_analysis.max.score, 1_800)
        self.assertEqual(self.shown["1p"][-1].enemy_analysis.max.score, 2_840)
        # 自分の行は変えない
        self.assertEqual(self.shown["1p"][-1].self_analysis.max.score, 1_800)
        self.assertEqual(self.shown["2p"][-1].self_analysis.max.score, 2_840)

    def test_firing_row_follows_the_other_side(self) -> None:
        # 連鎖中の「発火中」の行も、相手側が自分の連鎖として再現した値にそろえる
        mine = _data(1_800, 3_000)
        mine.enemy_firing = (13, 91_140, True)  # 1P が見た 2P の連鎖
        theirs = _data(2_840, 3_040)
        theirs.self_firing = (13, 91_320, True)  # 2P が再現した自分の連鎖
        self.link.update("1p", mine)
        self.link.update("2p", theirs)
        self.assertEqual(self.shown["1p"][-1].enemy_firing, (13, 91_320, True))
        # 相手側の連鎖が終わって発火中を外したら、こちらも外す(相手の行は解析に戻る)
        self.link.update("2p", _data(2_840, 3_040))
        self.assertIsNone(self.shown["1p"][-1].enemy_firing)

    def test_cleared_display_is_passed_through(self) -> None:
        self.link.update("2p", _data(2_840, 3_040))
        self.link.update("1p", None)
        self.assertIsNone(self.shown["1p"][-1])
        self.assertEqual(self.shown["2p"][-1].enemy_analysis.max.score, 3_040)  # 1P に解析が無ければ 2P の見立てのまま

    def test_original_data_is_not_changed(self) -> None:
        # 支援側が持っている表示データ(次の再計算で解析だけ書き換えて出し直す)は書き換えない
        data = _data(1_800, 3_000)
        self.link.update("2p", _data(2_840, 3_040))
        self.link.update("1p", data)
        self.assertEqual(data.enemy_analysis.max.score, 3_000)
        self.assertEqual(self.shown["1p"][-1].enemy_analysis.max.score, 2_840)


if __name__ == "__main__":
    unittest.main()
