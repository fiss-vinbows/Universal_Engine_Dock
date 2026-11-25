"""シミュレーションモードの複数候補の提示: ブリッジが返す候補ごとの数字から、見出しと根拠の文を作る。

数字はブリッジ(YamaNEKO の mode=candidates)が求める。ここは文にするだけの純粋な部分。
読みの値は乱数のツモの本数が少なく誤差があるので、最も伸びる手との差が誤差の2倍以内なら「誤差の範囲」と書く。
"""

from __future__ import annotations

from ..engine.ama_client import Candidate, CandidateList
from .rules import CHILD_OFFSET, EMPTY, ROWS, resolve_chain

COLOR_JA = {"R": "赤", "G": "緑", "B": "青", "Y": "黄"}
# 読みで次に多い置き方の割合がこれより低ければ、構想として出さない(ツモ次第でばらつくので当てにならない)
NEXT_SHARE_MIN = 30


def attack_word(early: bool) -> str:
    """序盤は速攻、ある程度積み込んだ中盤からは催促と呼ぶ。"""
    return "速攻" if early else "催促"


def placement_text(x: int, rotation: str, pair: str) -> str:
    """置き方を色つきで書く(例: 3列目 縦置き(緑下・黄上) / 3列目緑・4列目黄)。pair は「軸、子」の2文字。"""
    axis, child = COLOR_JA.get(pair[0], pair[0]), COLOR_JA.get(pair[1], pair[1])
    col = x + 1
    if rotation == "UP":
        return f"{col}列目 縦置き({axis}下・{child}上)" if axis != child else f"{col}列目 縦置き({axis}{axis})"
    if rotation == "DOWN":
        return f"{col}列目 縦置き({child}下・{axis}上)" if axis != child else f"{col}列目 縦置き({axis}{axis})"
    if rotation == "RIGHT":
        return f"{col}列目{axis}・{col + 1}列目{child}"
    return f"{col - 1}列目{child}・{col}列目{axis}"


def aim_text(c: Candidate, early: bool = False) -> str:
    """候補の狙い(見出しの後ろに付ける)。"""
    aims = []
    if c.primary:
        aims.append("第一候補")
    if c.gtr:
        aims.append("先折りGTR")
    if c.fire_best:
        aims.append("発火")
    if c.threat_best:
        aims.append(attack_word(early))
    if c.grow:
        aims.append("本線が最大")
    return "・".join(aims) if aims else "本線を伸ばす"


def follow_up_move(c: Candidate) -> tuple[int, str] | None:
    """この候補の次の組の想定の置き方。先折りGTRの手は形に合う置き方、ほかは根拠の文と同じく催促を作る手を優先する。"""
    if c.gtr and c.gtr_next is not None:
        return c.gtr_next
    if c.threat_hits > 0 and c.threat_next is not None:
        return c.threat_next
    return c.next if c.next is not None and c.next_share >= NEXT_SHARE_MIN else None


def _landed(board: list[list[str]], x: int, rotation: str, pair: str) -> list[tuple[tuple[int, int], str]] | None:
    """組ぷよを (x, rotation) で落とした時に2個が止まるマス(置いた後の盤面に書き込む)。置けなければ None。"""
    child_col = x + CHILD_OFFSET[rotation][1]
    # 下になる方から落とす
    order = [(child_col, pair[1]), (x, pair[0])] if rotation == "DOWN" else [(x, pair[0]), (child_col, pair[1])]
    cells = []
    for col, color in order:
        row = ROWS - 1
        while row >= 0 and board[row][col] != EMPTY:
            row -= 1
        if row < 0:
            return None
        board[row][col] = color
        cells.append(((row, col), color))
    return cells


def follow_up_cells(board: list[list[str]], c: Candidate, pair: str, next_pair: str) -> list[tuple[tuple[int, int], str]]:
    """候補を置いた(連鎖も済ませた)後の盤面に、次の組を想定どおりに置いた時の2マスと色。分からなければ空。"""
    move = follow_up_move(c)
    if move is None:
        return []
    after = [list(row) for row in board]
    placed = _landed(after, c.x, c.rotation, pair)
    if placed is None:
        return []
    after = resolve_chain(after).final if c.fire_chain > 0 else after
    after = [list(row) for row in after]
    return _landed(after, move[0], move[1], next_pair) or []


def title_text(index: int, c: Candidate, pair: str, early: bool = False) -> str:
    return f"候補{index + 1}({aim_text(c, early)}) {placement_text(c.x, c.rotation, pair)}"


def reason_lines(c: Candidate, result: CandidateList, next_pair: str | None) -> list[str]:
    """候補の根拠の文(1項目1行)。"""
    lines: list[str] = []
    if c.gtr:
        roles = "・".join(c.gtr_roles)
        follow = ""
        if c.gtr_next is not None and next_pair is not None:
            follow = f"次は {placement_text(c.gtr_next[0], c.gtr_next[1], next_pair)}。"
        lines.append(f"先折りGTRの形「{c.gtr_form}」に合う。置くのは{roles}。{follow}")
    if c.fire_chain > 0:
        lines.append(f"この手で {c.fire_chain}連鎖 {c.fire_score:,}点を発火。")
    source = "本当の先のツモで読む" if result.real_future else f"{result.reads}通りのツモで読む"
    if c.grow:
        lines.append(f"{source}と本線は平均 {c.value:,}点まで伸び、候補の中で最大。")
    elif result.real_future or c.gap > 2 * c.gap_se:
        lines.append(f"{source}と本線は平均 {c.value:,}点まで伸びる(最も伸びる手より {c.gap:,}点低い)。")
    else:
        lines.append(f"{source}と本線は平均 {c.value:,}点まで伸びる(最も伸びる手との差は誤差の範囲)。")
    word = attack_word(result.early)
    if c.threat_hits > 0:
        best = "".join(COLOR_JA.get(ch, ch) for ch in c.threat_best_pair)
        how = "次の組を置くと、" if c.threat_next is not None else ""
        if next_pair is not None and c.threat_next is not None:
            how = f"次に {placement_text(c.threat_next[0], c.threat_next[1], next_pair)} と置くと、"
        lines.append(f"{how}その次の組の {c.threat_hits}/16 で{word}を打てる(2連鎖ダブルか3連鎖。最大は{best}で {c.threat_best_chain}連鎖 {c.threat_best_score:,}点)。")
    else:
        lines.append(f"すぐに打てる{word}はまだ無い。")
    if c.next is not None and next_pair is not None and c.next != c.threat_next and c.next_share >= NEXT_SHARE_MIN:
        lines.append(f"読みで次に多い置き方: {placement_text(c.next[0], c.next[1], next_pair)}({c.next_share}%)。")
    return lines
