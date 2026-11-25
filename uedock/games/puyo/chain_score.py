"""1つの盤面の点数欄の読みから、連鎖の点数(始まる前と後の合計の差)を数える。

ゲームの点数表示(盤面の下の8桁。vision/score_reader.py)は、ふだんは合計を出し、連鎖の各段では「消した個数×10 × 倍率」
(例: 40×8)を出したあと合計が増える。連鎖が始まる前と終わった後の合計の差が、そのまま連鎖の点数になる。

自分の盤面と相手の盤面で同じ数え方をするので、盤面ごとに1つ作る(worker.py)。表示とログは呼び出し側が行う。
片側だけを支援している時は、相手の盤面の点数欄も読む。
"""

from __future__ import annotations

from dataclasses import dataclass

from .vision.score_reader import ScoreReading

# 合計は2回続けて同じ値を読んだら採用する(数字が切り替わる途中のフレームを読まない)
SCORE_STABLE_READS = 2


@dataclass
class ChainCount:
    """数えている連鎖。base は始まる前の合計、now は今の合計、steps は「a×b」の表示の回数、ended_at は連鎖が終わった時刻。"""

    base: int
    now: int
    why: str
    steps: int = 0
    ended_at: float | None = None
    live: bool = False  # 再現が無い連鎖で、点数表示で数えた「ここまで」の点数を出している
    # 連鎖の終わりを最初に見た時の再現 (連鎖数, 点数)。追跡が連鎖を途中で区切って続きを別の連鎖として数えても、最初の値を残す
    replay: tuple[int, int] | None = None

    @property
    def actual(self) -> int:
        return self.now - self.base


class ChainScoreCounter:
    """1つの盤面の点数欄の読みを受け取り、合計と、数えている連鎖の点数を持つ。"""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.total: int | None = None  # 採用した合計(SCORE_STABLE_READS 回続けて同じ値を読んだもの)
        self._candidate: tuple[int, int] | None = None  # (読んだ合計, 続けて読んだ回数)
        self._last_step: tuple[int, int] | None = None  # 直前に読んだ連鎖の段の「a×b」(同じ表示を何度も数えない)
        self.chain: ChainCount | None = None

    @property
    def counting(self) -> bool:
        """連鎖の途中(終わりをまだ見ていない)を数えているか。"""
        return self.chain is not None and self.chain.ended_at is None

    def open(self, why: str) -> ChainCount | None:
        """連鎖を数え始める(始まる前の合計が分かっている時だけ)。前に数えていた連鎖があれば、それを返す(呼び出し側が確定する)。

        連鎖の終わりを見た後、まだ確定していない(次の組ぷよが出ていない・時間もたっていない)連鎖があれば、新しくは数えず
        その続きとして数える。新しい連鎖は組ぷよを置かないと始まらない。"""
        if self.chain is not None and self.chain.ended_at is not None:
            self.chain.ended_at = None
            return None
        if self.total is None or self.counting:
            return None
        prev = self.take()
        self.chain = ChainCount(base=self.total, now=self.total, why=why)
        return prev

    def end(self, t: float, replay: tuple[int, int] | None = None) -> None:
        """連鎖の終わりを見た。この後も少しの間は合計の増加を数える(settled で確定する)。replay は追跡の再現 (連鎖数, 点数)。"""
        if self.chain is not None and self.chain.ended_at is None:
            self.chain.ended_at = t
            if self.chain.replay is None and replay is not None:
                self.chain.replay = replay

    def settled(self, t: float, settle_sec: float) -> bool:
        """連鎖の終わりから settle_sec たったか。"""
        return self.chain is not None and self.chain.ended_at is not None and t - self.chain.ended_at >= settle_sec

    def take(self) -> ChainCount | None:
        """数えていた連鎖を取り出す(以後は数えない)。"""
        chain, self.chain = self.chain, None
        return chain

    def feed(self, reading: ScoreReading) -> tuple[str, ChainCount | None, ChainCount | None]:
        """読みを1つ受け取る。返り値は (何が起きたか, 数えている連鎖, 新しく数え始めたので取り出した前の連鎖)。何が起きたか:
        - "step": 連鎖の段を1つ数えた(数えていなければ、ここで数え始める)
        - "total": 数えている連鎖の合計が増えた
        - "": 何も変わらない"""
        if reading.step is not None:
            if reading.step == self._last_step:
                return "", self.chain, None
            self._last_step = reading.step
            # 数えていなければ数え始める(終わりを見た後の段の表示は、同じ連鎖の続きとして数える)
            prev = self.open("点数表示に連鎖の段が出た") if not self.counting else None
            if self.chain is None:
                return "", None, prev
            self.chain.steps += 1
            return "step", self.chain, prev
        self._last_step = None
        cand = self._candidate
        self._candidate = (reading.total, cand[1] + 1) if cand is not None and cand[0] == reading.total else (reading.total, 1)
        if self._candidate[1] < SCORE_STABLE_READS or reading.total == self.total:
            return "", self.chain, None
        self.total = reading.total
        if self.chain is not None and reading.total >= self.chain.now:
            self.chain.now = reading.total
            return "total", self.chain, None
        return "", self.chain, None
