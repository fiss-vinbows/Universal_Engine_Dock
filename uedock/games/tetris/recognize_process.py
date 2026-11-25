"""画面の撮影と画像認識(recognize)を別のプロセスで行う。

1P と 2P の支援を同じプロセスで動かすと、
Python は同時に1つのスレッドしか計算を進められない(GIL)ため、2人分の画像認識が取り合いになり、1回の読み取りが
75ms から約125ms に遅くなった。読み取りを待つ分が
そのまま1手の遅さになった。

側ごとに、撮影と認識だけをする子プロセスを1つ持つ。ワーカー(QThread)は依頼を送って結果を待つだけなので、
待つ間は GIL を手放し、2人分の読み取りが同時に進む。recognize は前回の結果をヒントとして受け取るだけの関数
なので、ヒントも一緒に送る。子プロセスが動かない時は、呼び出し側が今までどおり自分のスレッドで認識する。
"""

from __future__ import annotations

import multiprocessing
import time
from typing import Any

# 子プロセスの起動(uedock.games.tetris.worker の読み込みに1〜2秒)を待つ上限と、1回の認識を待つ上限(秒)
START_TIMEOUT_SEC = 15.0
RECOGNIZE_TIMEOUT_SEC = 5.0


def _serve(conn) -> None:  # noqa: ANN001
    """子プロセス: 依頼(キャリブレーション, 引数)を受けて、撮影と認識をして結果を返す。None で終わる。"""
    from uedock.games.tetris.worker import recognize
    from uedock.capture.screen_capture import ScreenCapture

    conn.send(("ready", None))
    with ScreenCapture() as capture:
        while True:
            message = conn.recv()
            if message is None:
                break
            calibration, kwargs = message
            started = time.perf_counter()
            capture.last_grab_ms = 0.0
            try:
                result = recognize(calibration, capture, **kwargs)
                timing = (capture.last_grab_ms, (time.perf_counter() - started) * 1000)
                conn.send(("ok", result, timing))
            except Exception as exc:  # noqa: BLE001 - 認識失敗の1回で子プロセスを止めない
                conn.send(("error", repr(exc), None))


def summarize_timings(timings: list[tuple[float, float, float]]) -> str:
    """取り込みの計測(1行)。timings は (撮影ms, 撮影と認識ms, 依頼から受け取りまでms) の並び。"""
    if not timings:
        return "記録なし"
    parts = []
    for i, name in enumerate(("撮影", "撮影と認識", "依頼から受け取り")):
        values = sorted(t[i] for t in timings)
        parts.append(f"{name} 中央{values[len(values) // 2]:.0f}ms 最大{values[-1]:.0f}ms")
    return f"{len(timings)}回 / " + " / ".join(parts)


class RecognizerProcess:
    """撮影と認識をする子プロセス1つ。recognize() は uedock.games.tetris.worker.recognize と同じ引数(capture を除く)。"""

    def __init__(self) -> None:
        context = multiprocessing.get_context("spawn")
        self._conn, child = context.Pipe()
        self._process = context.Process(target=_serve, args=(child,), daemon=True)
        self._process.start()
        child.close()
        if not self._conn.poll(START_TIMEOUT_SEC):
            self.close()
            raise TimeoutError("画像認識の子プロセスが起動しません")
        self._conn.recv()  # ("ready", None)
        # (撮影ms, 撮影と認識ms, 依頼から受け取りまでms)。summarize_timings で1行にする
        self.timings: list[tuple[float, float, float]] = []

    def _receive(self, requested_at: float) -> tuple[str, Any]:
        """子プロセスの返事を受け取り、かかった時間を timings に足す。"""
        status, result, timing = self._conn.recv()
        if timing is not None:
            self.timings.append((timing[0], timing[1], (time.perf_counter() - requested_at) * 1000))
        return status, result

    def recognize(self, calibration, **kwargs: Any):  # noqa: ANN001, ANN201
        requested_at = time.perf_counter()
        self._conn.send((calibration, kwargs))
        if not self._conn.poll(RECOGNIZE_TIMEOUT_SEC):
            raise TimeoutError("画像認識の子プロセスから応答がありません")
        status, result = self._receive(requested_at)
        if status != "ok":
            raise RuntimeError(f"子プロセスでの認識エラー: {result}")
        return result

    def close(self) -> None:
        try:
            self._conn.send(None)
        except (OSError, ValueError):
            pass
        self._process.join(2.0)
        if self._process.is_alive():
            self._process.terminate()
        self._conn.close()
