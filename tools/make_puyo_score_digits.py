"""ぷよぷよの点数表示(盤面の下の8桁)を読むための数字の見本を、録画から作る(uedock/games/puyo/vision/score_reader.py)。

見本はゲームの画面から作るので、リポジトリには入れない。config/puyo_score_digits.npz に保存する(config/ は公開しない)。

使い方(1920×1080 のぷよ対ぷよの録画。盤面の位置は Steam 版の標準の配置を使う):
    venv\\Scripts\\python tools\\make_puyo_score_digits.py 録画.mp4 3300:L:00000120 3300:R:"  50x  2" ...

ラベルは「フレーム番号:L か R(左右の盤面):8文字」。8文字は点数欄のマスと同じ並びで、数字・x(×)・空白(何も無いマス)。
0〜9 と x がそれぞれ1回以上出るように選ぶ(連鎖の段の「40×  8」は「  40x  8」)。
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uedock.games.puyo.capture.window_detect import REFERENCE_LAYOUT  # noqa: E402
from uedock.games.puyo.vision.score_reader import CELLS, build_exemplars, crop_strip, save_exemplars  # noqa: E402
from uedock.paths import config_dir  # noqa: E402

OUTPUT_NAME = "puyo_score_digits.npz"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    video, labels = argv[0], argv[1:]
    cap = cv2.VideoCapture(video)
    players = {"L": REFERENCE_LAYOUT.self_player.geometry(), "R": REFERENCE_LAYOUT.enemy_player.geometry()}
    samples = []
    for item in labels:
        frame_no, side, label = item.split(":", 2)
        if len(label) != CELLS or side not in players:
            print(f"ラベルの形が違います: {item!r}")
            return 1
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_no))
        ok, frame = cap.read()
        if not ok:
            print(f"フレーム {frame_no} を読めません")
            return 1
        strip = crop_strip(frame, players[side], (0, 0))
        if strip is None:
            print(f"フレーム {frame_no} の点数欄が画面の外です")
            return 1
        samples.append((strip, label))
    cells, names = build_exemplars(samples)
    missing = [ch for ch in "0123456789x" if ch not in names]
    if missing:
        print(f"見本の無い文字があります: {' '.join(missing)}(その文字が出るフレームを足してください)")
        return 1
    path = config_dir() / OUTPUT_NAME
    save_exemplars(cells, names, path)
    print(f"保存しました: {path}({len(samples)}枚から {len(set(names))}文字・見本{len(names)}個)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
