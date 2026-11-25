"""テトリスの思考エンジンのサンプル(TBP)。規格は docs/ENGINE_SPEC.md。

1手だけ読み、穴と高さが少なくなる置き場所を返す。真上から落とす置き方だけを考える。
Python の標準ライブラリだけで動く。登録する時は、実行ファイルに python.exe、起動引数に -u とこのファイルを指定する。
"""

import json
import sys

# Windows の標準入出力の文字コードを、規格どおり UTF-8 にする
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

WIDTH = 10
CELLS = {
    "I": [(-1, 0), (0, 0), (1, 0), (2, 0)],
    "O": [(0, 0), (1, 0), (0, 1), (1, 1)],
    "T": [(-1, 0), (0, 0), (1, 0), (0, 1)],
    "L": [(-1, 0), (0, 0), (1, 0), (1, 1)],
    "J": [(-1, 0), (0, 0), (1, 0), (-1, 1)],
    "S": [(-1, 0), (0, 0), (0, 1), (1, 1)],
    "Z": [(-1, 1), (0, 1), (0, 0), (1, 0)],
}
ROTATE = {
    "north": lambda x, y: (x, y),
    "east": lambda x, y: (y, -x),
    "south": lambda x, y: (-x, -y),
    "west": lambda x, y: (-y, x),
}


def send(message: dict) -> None:
    print(json.dumps(message, ensure_ascii=False), flush=True)


class Game:
    """今の局面。board は埋まったマス (x, y) の集合(y は下から)。"""

    def __init__(self, start: dict) -> None:
        self.board = {(x, y) for y, row in enumerate(start["board"]) for x, cell in enumerate(row) if cell is not None}
        self.hold = start["hold"]
        self.queue = list(start["queue"])

    def free(self, cells: list) -> bool:
        return all(0 <= x < WIDTH and y >= 0 and (x, y) not in self.board for x, y in cells)

    def placements(self, piece: str):
        """真上から落として置ける (向き, 中心x, 中心y, 4マス)。"""
        for orientation, rotate in ROTATE.items():
            shape = [rotate(dx, dy) for dx, dy in CELLS[piece]]
            for x in range(WIDTH):
                y = 22
                if not self.free([(x + dx, y + dy) for dx, dy in shape]):
                    continue
                while self.free([(x + dx, y - 1 + dy) for dx, dy in shape]):
                    y -= 1
                yield orientation, x, y, [(x + dx, y + dy) for dx, dy in shape]

    @staticmethod
    def put(board: set, cells: list) -> tuple[set, int]:
        """置いてそろった行を消した盤面と、消えた行数。"""
        board = board | set(cells)
        rows = {y for _x, y in board}
        full = sorted(y for y in rows if all((x, y) in board for x in range(WIDTH)))
        out = {(x, y - sum(f < y for f in full)) for x, y in board if y not in full}
        return out, len(full)

    @staticmethod
    def evaluate(board: set, cleared: int) -> float:
        heights = [max([y + 1 for x, y in board if x == c], default=0) for c in range(WIDTH)]
        holes = sum(heights[c] - sum(x == c for x, _y in board) for c in range(WIDTH))
        bumps = sum(abs(a - b) for a, b in zip(heights, heights[1:]))
        return cleared * 3 - holes * 8 - bumps - sum(heights) * 0.5 - max(heights) * 0.5

    def best(self) -> dict | None:
        """操作ミノか HOLD のミノ(HOLD が空なら NEXT の先頭)の、評価の一番良い手。"""
        pieces = set(self.queue[:1]) | ({self.hold} if self.hold else set(self.queue[1:2]))
        options = [
            (self.evaluate(*self.put(self.board, cells)), piece, orientation, x, y)
            for piece in sorted(pieces)
            for orientation, x, y, cells in self.placements(piece)
        ]
        if not options:
            return None
        _score, piece, orientation, x, y = max(options)
        return {"location": {"type": piece, "orientation": orientation, "x": x, "y": y}, "spin": "none"}

    def play(self, move: dict) -> None:
        """提示した手のとおりに置かれた。"""
        loc = move["location"]
        cells = [ROTATE[loc["orientation"]](dx, dy) for dx, dy in CELLS[loc["type"]]]
        self.board, _cleared = self.put(self.board, [(loc["x"] + dx, loc["y"] + dy) for dx, dy in cells])
        current = self.queue.pop(0)
        if loc["type"] != current:  # HOLD して置いた
            if self.hold is None:
                self.queue.pop(0)
            self.hold = current


def main() -> None:
    send({"type": "info", "name": "Sample", "version": "1.0", "author": "Universal Engine Dock", "features": []})
    game = None
    for line in sys.stdin:
        line = line.strip().lstrip("﻿")  # PowerShell から手で渡すと先頭に BOM が付く
        if not line:
            continue
        message = json.loads(line)
        kind = message.get("type")
        if kind == "rules":
            send({"type": "ready"})
        elif kind == "start":
            game = Game(message)
        elif kind == "suggest":
            move = game.best() if game is not None else None
            send({"type": "suggestion", "moves": [move] if move else [], "move_info": {"nodes": 1, "nps": 1.0}})
        elif kind == "play" and game is not None:
            game.play(message["move"])
        elif kind == "new_piece" and game is not None:
            game.queue.append(message["piece"])
        elif kind == "stop":
            game = None
        elif kind == "quit":
            break
        # ほかの種類は無視する


if __name__ == "__main__":
    main()
