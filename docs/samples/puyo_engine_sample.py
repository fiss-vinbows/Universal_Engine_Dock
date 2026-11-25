"""ぷよの思考エンジンのサンプル。規格は docs/ENGINE_SPEC.md。

今の組ぷよだけを見て、同じ色が多くつながる低い置き場所を返す。連鎖を狙うことはしない。
Python の標準ライブラリだけで動く。登録する時は、実行ファイルに python.exe、起動引数に -u とこのファイルを指定する。
"""

import json
import sys
import time

# Windows の標準入出力の文字コードを、規格どおり UTF-8 にする
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

WIDTH, HEIGHT = 6, 13
CHILD = {"UP": (0, 1), "RIGHT": (1, 0), "DOWN": (0, -1), "LEFT": (-1, 0)}
POWER = [0, 8, 16, 32] + [64 + 32 * i for i in range(16)]  # 連鎖ボーナス
COLOR_BONUS = [0, 0, 3, 6, 12]


def send(message: dict) -> None:
    print(json.dumps(message, ensure_ascii=False), flush=True)


def read_field(rows: list) -> list:
    """列ごとに下から並べた盤面。"""
    return [[rows[HEIGHT - 1 - y][x] for y in range(HEIGHT)] for x in range(WIDTH)]


def height(field: list, x: int) -> int:
    return max([y + 1 for y in range(HEIGHT) if field[x][y] != "."], default=0)


def place(field: list, pair: str, x: int, r: str):
    """置いた後の (軸のマス, 子のマス, 盤面)。置けなければ None。"""
    cx = x + CHILD[r][0]
    if not 0 <= cx < WIDTH:
        return None
    if r == "UP":
        axis, child = (x, height(field, x)), (x, height(field, x) + 1)
    elif r == "DOWN":
        axis, child = (x, height(field, x) + 1), (x, height(field, x))
    else:
        axis, child = (x, height(field, x)), (cx, height(field, cx))
    # 出現する3列目から横に動く途中の列がふさがっていたら通れない
    for target in {x, cx}:
        step = 1 if target > 2 else -1
        if any(height(field, c) >= 12 for c in range(2, target + step, step)):
            return None
    if axis[1] >= HEIGHT or child[1] >= HEIGHT:
        return None
    out = [column[:] for column in field]
    out[axis[0]][axis[1]], out[child[0]][child[1]] = pair[0], pair[1]
    return axis, child, out


def group(field: list, x: int, y: int) -> set:
    """(x, y) と同じ色でつながるマス(13段目は数えない)。"""
    color, seen, stack = field[x][y], set(), [(x, y)]
    while stack:
        a, b = stack.pop()
        if (a, b) in seen or not (0 <= a < WIDTH and 0 <= b < HEIGHT - 1) or field[a][b] != color:
            continue
        seen.add((a, b))
        stack += [(a + 1, b), (a - 1, b), (a, b + 1), (a, b - 1)]
    return seen


def simulate(field: list) -> tuple[int, int, list]:
    """連鎖を最後まで進めた (連鎖数, 点, 盤面)。"""
    field = [column[:] for column in field]
    count = score = 0
    while True:
        # 浮いたぷよを落とす
        for x in range(WIDTH):
            puyos = [c for c in field[x] if c != "."]
            field[x] = puyos + ["."] * (HEIGHT - len(puyos))
        groups, done = [], set()
        for x in range(WIDTH):
            for y in range(HEIGHT - 1):
                if field[x][y] in "RYGB" and (x, y) not in done:
                    found = group(field, x, y)
                    done |= found
                    if len(found) >= 4:
                        groups.append(found)
        if not groups:
            return count, score, field
        cells = set().union(*groups)
        colors = {field[x][y] for x, y in cells}
        bonus = POWER[min(count, len(POWER) - 1)] + COLOR_BONUS[len(colors)]
        bonus += sum(10 if len(g) >= 11 else max(0, len(g) - 3) if len(g) > 4 else 0 for g in groups)
        score += 10 * len(cells) * max(1, min(999, bonus))
        count += 1
        for x, y in cells:
            for a, b in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= a < WIDTH and 0 <= b < HEIGHT and field[a][b] == "#":
                    field[a][b] = "."
            field[x][y] = "."


def options(field: list, pair: str):
    for x in range(WIDTH):
        for r in CHILD:
            placed = place(field, pair, x, r)
            if placed is not None:
                yield x, r, placed


def best_move(field: list, queue: list) -> dict | None:
    """つながりが多く、低く置ける手。3列目の12段目をふさぐ手は避ける。"""
    best = None
    for x, r, (axis, child, after) in options(field, queue[0]):
        count, score, final = simulate(after)
        links = sum(len(group(after, *cell)) for cell in (axis, child) if cell[1] < HEIGHT - 1)
        value = links * 40 + min(score, 3000) // 10 - (axis[1] + child[1]) * 25 - 100000 * (height(final, 2) >= 12)
        if best is None or value > best["score"]:
            best = {"x": x, "r": r, "score": value, "axis": list(axis), "child": list(child), "chain": (count, score)}
    return best


def analyze(field: list, queue: list, target_point: int = 70) -> dict:
    """今の組ぷよと、次の組ぷよまでで撃てる一番大きい連鎖。"""
    def attack(count: int, score: int, x: int, r: str, frame: int) -> dict:
        return {"count": count, "score": score, "garbage": score // max(1, target_point), "x": x, "r": r, "frame": frame}

    none = attack(0, 0, 0, "UP", 0)
    found = []
    for x, r, (_axis, _child, after) in options(field, queue[0]):
        count, score, final = simulate(after)
        found.append(attack(count, score, x, r, 0))
        if len(queue) > 1:
            for _x, _r, (_a, _c, second) in options(final, queue[1]):
                count, score, _final = simulate(second)
                found.append(attack(count, score, x, r, 1))
    found = [f for f in found if f["count"] > 0]
    top = lambda items: max(items, key=lambda f: f["score"], default=none)  # noqa: E731
    largest = top(found)
    return {
        "immediate": top([f for f in found if f["frame"] == 0]),
        "max": largest,
        "harass": top([f for f in found if f["count"] <= 3]),
        "potential": 0,
        "garbage_max": largest["garbage"],
    }


def answer(request: dict) -> dict:
    mode = request.get("mode", "build")
    if mode == "analyze":
        return analyze(read_field(request["field"]), request["queue"])
    if mode not in ("build", "battle"):
        return {"error": f"unknown mode: {mode}"}
    me = request["self"] if mode == "battle" else request
    started = time.perf_counter()
    field = read_field(me["field"])
    move = best_move(field, me["queue"])
    if move is None:
        return {"error": "no valid placement"}
    count, score = move.pop("chain")
    if mode == "battle":
        point = request.get("target_point", 70)
        enemy = request["enemy"]
        move["eval"] = move.pop("score")
        move["label"] = "main" if count >= 5 else "harass" if count >= 2 else "build"
        move["self"] = analyze(field, me["queue"], point)
        move["enemy"] = analyze(read_field(enemy["field"]), enemy["queue"], point)
        move["chain_after"] = {"count": count, "score": score}
    move["ms"] = round((time.perf_counter() - started) * 1000)
    return move


def main() -> None:
    send({"ready": True})
    for line in sys.stdin:
        line = line.strip().lstrip("﻿")  # PowerShell から手で渡すと先頭に BOM が付く
        if line == "quit":
            break
        if not line:
            continue
        try:
            send(answer(json.loads(line)))
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            send({"error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
