"""1P と 2P を両方支援している時に、情報パネルの「相手」の行を、相手側の「自分」の行にそろえる。

「相手」の点数は、自分の手番の始まりに、自分のエンジンが相手の盤面を解析した値。相手側の「自分」は、相手の手番の始まりに、
相手のエンジンが自分の盤面を解析した値。ログでは、
- 盤面とツモが同じ局面どうしでは、2P の相手と 1P の自分が 56/69件、1P の相手と 2P の自分が 51/55件で一致した。
  一致しない残りは、エンジン(ama と YamaNEKO)の探索の違い(同じ盤面・ツモで最大連鎖の見つけ方が違う)。
- 解析する時刻が手番ごとにずれるので、同じ時刻の表示では、片方だけ新しい局面のことが多い(同じ局面の思考が無い 32件・16件)。
そこで、両方を支援している時は、相手の行に相手側の最新の「自分」の解析を出す(各プレイヤーの点数は、そのプレイヤーのエンジンの値)。
連鎖中の「発火中」の行も同じ: 相手の行には、相手側が自分の連鎖として再現した段数・点数を出す。
表示だけを変える。思考(受け・潰しの判断)は今までどおり自分のエンジンが相手を解析した値を使う。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from .renderer import OverlayDrawData

Sink = Callable[[OverlayDrawData | None], None]


class PanelLink:
    """両方の支援の表示データを受け取り、相手の行を相手側の自分の行にそろえてから、それぞれの表示へ渡す。"""

    def __init__(self, sinks: dict[str, Sink]) -> None:
        self._sinks = dict(sinks)
        self._latest: dict[str, OverlayDrawData | None] = {side: None for side in sinks}

    def _other(self, side: str) -> str | None:
        return next((s for s in self._sinks if s != side), None)

    def linked(self, side: str) -> OverlayDrawData | None:
        """side の表示データの相手の行を、相手側の最新の自分の解析にしたもの(相手側にまだ解析が無ければそのまま)。"""
        data = self._latest.get(side)
        other = self._other(side)
        theirs = self._latest.get(other) if other is not None else None
        if data is None or theirs is None or theirs.self_analysis is None:
            return data
        return replace(data, enemy_analysis=theirs.self_analysis, enemy_firing=theirs.self_firing)

    def update(self, side: str, data: OverlayDrawData | None) -> None:
        """side の支援から表示データが届いた: その表示と、相手側の表示(相手の行が変わる)を描き直す。"""
        self._latest[side] = data
        self._sinks[side](self.linked(side))
        other = self._other(side)
        if other is not None and self._latest.get(other) is not None:
            self._sinks[other](self.linked(other))
