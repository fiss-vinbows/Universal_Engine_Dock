"""ぷよの支援モードの開始と終了。

共通のメイン画面は uedock/game_mode.py の登録口を通じてここを呼ぶ:
1. start_assist(request) — 思考エンジンを起動する(メイン画面は見えている)
2. session.begin() — メイン画面を隠した後。盤面の位置を決め、オーバーレイと終了バッジを出し、観測を始める
3. session.stop() — 止める。終了バッジ・ゲームのウィンドウの移動・エラーで自分から止まることもある
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import cv2
from PyQt6 import QtCore, QtWidgets

from ...capture.screen_capture import ScreenCapture
from ...game_mode import LAYOUT_SOLO, NO_GAME, AssistRequest, SideSetup
from ...obs_output import content_area
from ...paths import APP_TITLE, app_root
from ...plugin_api import FEATURE_DEBUG_LOG
from ...ui.engine_nameplate import close_nameplates, show_engine_nameplates
from ...ui.screen_map import screen_of_rect, to_logical_rect
from ...process_cpu import CpuBreakdownStats, ProcessCpuMeter, ProcessGroup, window_pid
from . import side_options
from .capture.calibrate import CONFIG_PATH, SOLO_CONFIG_PATH, CalibrationResult, load_calibration
from .capture.window_detect import client_rect, detect_calibration, title_bar_rect
from .engine.ama_client import HAIPUYO_BRIDGE_ARGS, PEEK_BRIDGE_ARGS, AmaClient
from .engine.book_client import BookClient
from .engine.perf_meter import CpuMeter
from .engine.registry import Registry
from .overlay.panel_link import PanelLink
from .overlay.renderer import OverlayDrawData, register_language
from .side_options import SideOptions
from .simulator.opening_book import OpeningBook
from .worker import AssistWorker, ControlBadge, OverlayWindow, _exclude_window_from_screen_capture

GAME_ID = "puyo"
# ゲームのウィンドウが動いていないかを確かめる間隔(ms)
WINDOW_WATCH_MS = 1000
NO_POSITION_MESSAGE = (
    "ぷよの盤面の位置が分かりません。PC 版はウィンドウ表示で起動して「ゲームのウィンドウを検出」を、"
    "キャプチャボードの映像は「ぷよのキャリブレーション」を実行してください"
)


def worker_side(side: str) -> str:
    """AssistWorker に渡す側。一人用は 1P として扱う(キー割り当て・画像の保存先が 1P・2P で分かれている)。"""
    return "2p" if side == "2p" else "1p"


def watches_enemy(request: AssistRequest, side: str, options: SideOptions) -> bool:
    """そのサイドで相手の盤面も読むか。一人用は読まない。対戦では、相手のサイドがぷよ以外のゲームを選んでいれば読まず
    (v0.1 では別種の相手を凝視しない)、相手が支援なし・ぷよなら設定の「相手の盤面を見る」に従う。"""
    if request.layout == LAYOUT_SOLO:
        return False
    if request.opponents.get(side, NO_GAME) not in (NO_GAME, GAME_ID):
        return False
    return options.gaze


# CPU 使用率の内訳: 測る間隔と、ログに出す間隔
CPU_BREAKDOWN_MS = 1000
# 支援モードの間の OpenCV のスレッド数。
# 既定では全コア(24)を使うが、ぷよの画像は小さく、スレッドを起こして待ち合わせる手間の方が大きい。実機の画面(1314×805)で、
# 1P・2P が同時に撮って読む1回の CPU は 35.7ms → 14.6ms に減り、かかる時間(約31ms)は変わらなかった。
# OpenCV のスレッド数はプロセス全体の設定なので、支援モードを終えたら元の値に戻す
OPENCV_THREADS = 1
CPU_BREAKDOWN_LOG_SAMPLES = 60
APP_CPU_LABEL = "Universal Engine Dock"
GAME_CPU_LABEL = "ゲーム"
PC_CPU_LABEL = "PC 全体"


def load_book(name: str) -> OpeningBook:
    """その名前の定跡。読めなければ ValueError。"""
    path = Registry.load().book_path(name)
    try:
        book = OpeningBook.load() if path is None else OpeningBook.load(path)
    except (ValueError, KeyError, TypeError):
        book = None
    if book is None:
        raise ValueError(f"定跡ファイルを読めません: {name}")
    return book


def make_client(setup: SideSetup, options: SideOptions, threads: int | None):  # noqa: ANN201
    """そのサイドの思考エンジンを起動する。配ぷよ読みなど YamaNEKO だけの起動引数は、AmaClient が実行ファイル名で見分けて
    YamaNEKO の時だけ渡す。登録したエンジンの起動引数(EngineSpec.args)はそのまま渡す。"""
    extra = (HAIPUYO_BRIDGE_ARGS if options.haipuyo else []) + (PEEK_BRIDGE_ARGS if options.peek else [])
    client = AmaClient(
        exe_path=Path(setup.engine.exe),
        search_threads=options.search_threads(threads),
        extra_args=extra or None,
        custom_args=list(setup.engine.args) or None,
        low_priority=options.low_priority,
    )
    if options.book:
        try:
            return BookClient(client, load_book(options.book))
        except ValueError:
            client.close()
            raise
    return client


class PuyoAssistSession(QtCore.QObject):
    """ぷよの支援モード1回分。受け持つサイドごとに、思考エンジン・観測のスレッド・オーバーレイを持つ。"""

    def __init__(self, request: AssistRequest) -> None:
        super().__init__()
        self.request = request
        self.solo = request.layout == LAYOUT_SOLO
        self.game_window = request.game_window
        self.options = {s.side: side_options.load(s.side) for s in request.sides}
        self.clients: dict[str, object] = {}
        self.workers: dict[str, AssistWorker] = {}
        self.overlays: dict[str, OverlayWindow] = {}
        self.panel_link: PanelLink | None = None  # 両方を支援している時だけ(_on_draw_data)
        self.nameplates: list = []  # プレイヤー名の帯に重ねるエンジン名(uedock/ui/engine_nameplate.py)
        self.badge: ControlBadge | None = None
        self._badge_lines: dict[str, str] = {}
        self._running = False
        self._window_watch = QtCore.QTimer(self)
        self._window_watch.timeout.connect(self._check_game_window)
        self._cpu_timer = QtCore.QTimer(self)
        self._cpu_timer.timeout.connect(self._sample_cpu)
        self._cpu_meter: ProcessCpuMeter | None = None
        self._cv_threads_before: int | None = None  # 支援モードを始める前の OpenCV のスレッド数(終えたら戻す)
        self._pc_meter: CpuMeter | None = None
        self._cpu_minute: CpuBreakdownStats | None = None
        self._cpu_total: CpuBreakdownStats | None = None
        for key, lang in request.plugins.languages().items():
            register_language(key, lang.transform, lang.word_break)
        if self.game_window is None:
            self._load_calibration_file()  # 位置が分からなければ、エンジンを起動する前に断る
        # 思考エンジンはサイドごとに1つ(同時に考えるため)。1つでも起動できなければ全て閉じる。
        # 2つの時は探索スレッドをコア数の半分ずつにして、2つの YamaNEKO が CPU を取り合わないようにする。
        # 起動は1つずつ(AmaClient は ready まで待つ)なので、YamaNEKO の起動時の思考時間の計測は重ならない
        threads = max(1, (os.cpu_count() or 2) // 2) if len(request.sides) > 1 else None
        try:
            for setup in request.sides:
                self.clients[setup.side] = make_client(setup, self.options[setup.side], threads)
        except (FileNotFoundError, RuntimeError, TimeoutError, OSError, ValueError) as exc:
            self._close_clients()
            raise RuntimeError(f"ぷよの思考エンジンの起動に失敗しました:\n{exc}") from exc

    # ---- 盤面の位置 ----

    def _load_calibration_file(self) -> CalibrationResult:
        """手動キャリブレーション(Switch 用)のファイル。無い・壊れていれば理由を付けて断る。"""
        path = SOLO_CONFIG_PATH if self.solo else CONFIG_PATH
        try:
            return load_calibration(path)
        except FileNotFoundError:
            raise RuntimeError(NO_POSITION_MESSAGE) from None
        except (KeyError, ValueError) as exc:
            # 壊れた設定(幅0のネクスト枠など)で始めると認識が落ちるため、理由を出してやり直しを促す
            raise RuntimeError(f"ぷよのキャリブレーションが不正です。やり直してください:\n{exc}") from exc

    def _resolve_calibration(self) -> CalibrationResult:
        """盤面・ネクスト枠の位置(対戦用は 1P = 左を自分とした形)。ゲームのウィンドウが見つかっていれば、その画像の
        盤面の暗い矩形に合わせ直す(メイン画面を隠した後に呼ぶ)。無ければ手動キャリブレーションのファイル。"""
        if self.game_window is None:
            return self._load_calibration_file()
        with ScreenCapture() as cap:
            return detect_calibration(self.game_window, solo=self.solo, grab=cap.grab_bgr)

    def _calibration_for(self, base: CalibrationResult, side: str) -> CalibrationResult:
        """そのサイドを自分としたキャリブレーション。対戦用は 1P(左)を自分としているので 2P は入れ替える。"""
        return base.swapped() if side == "2p" and not self.solo else base

    # ---- 開始 ----

    def begin(self) -> None:
        base = self._resolve_calibration()
        # 位置が分からず始められない時(上で例外)は変えない
        if self._cv_threads_before is None:
            self._cv_threads_before = cv2.getNumThreads()
            cv2.setNumThreads(OPENCV_THREADS)
        if self.game_window is not None:
            self._window_watch.start(WINDOW_WATCH_MS)
        self.badge = ControlBadge(on_stop=self.stop, anchor=self._badge_anchor())
        # 起動時間・CPU 使用率はテトリスと同じく、終了ボタンを出す側(primary)のバーだけに置く(ぷよ対テトリスで二重にしない)
        if self.request.primary:
            self.badge.add_status_labels([s.side for s in self.request.sides])
        self.badge.show()
        excluded = _exclude_window_from_screen_capture(self.badge)
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
        log_wanted = self.request.debug_log and self.request.plugins.has_feature(FEATURE_DEBUG_LOG)
        # 1P と 2P を両方支援し、どちらも相手を読む時は、情報パネルの相手の行を相手側の自分の行にそろえる(overlay/panel_link.py)
        linked = [s.side for s in self.request.sides if watches_enemy(self.request, s.side, self.options[s.side])]
        self.panel_link = None
        for setup in self.request.sides:
            side, options = setup.side, self.options[setup.side]
            wside = worker_side(side)
            calibration = self._calibration_for(base, side)
            overlay = OverlayWindow(calibration, bounds=self._content_bounds())
            overlay.show()
            excluded = _exclude_window_from_screen_capture(overlay) and excluded

            debug_log_path = None
            if log_wanted:
                log_dir = app_root() / "debug_logs"
                log_dir.mkdir(exist_ok=True)
                suffix = "" if wside == "1p" else f"_{wside}"
                debug_log_path = log_dir / f"debug_log_{stamp}{suffix}.txt"

            worker_args = dict(
                calibration=calibration,
                client=self.clients[side],
                debug_log_path=debug_log_path,
                # 相手を読まない時は一人用と同じ動き(相手を見ず、連鎖構築だけを提案する)
                solo=not watches_enemy(self.request, side, options),
                side=wside,
                haipuyo=options.haipuyo and self.clients[side].engine == "yamaneko",
                peek=options.peek and self.clients[side].engine == "yamaneko",
                # 相手の盤面の点数欄も読む。両方を支援してパネルをそろえる時は、相手側の支援が読んだ値を出すので読まない
                enemy_score=not (len(linked) == 2 and len(self.request.sides) == 2),
            )
            # プラグインの拡張が観測ループを作ることがある(uedock/extension.py の create_worker)。作らなければ標準のもの
            worker = None
            for ext in self.request.extensions:
                worker = ext.create_worker(GAME_ID, side, {"worker_args": worker_args, "session": self})
                if worker is not None:
                    break
            if worker is None:
                worker = AssistWorker(**worker_args)
            queued = QtCore.Qt.ConnectionType.QueuedConnection
            worker.draw_data_ready.connect(lambda data, side=side: self._on_draw_data(side, data), queued)
            if self.badge.fps_label is not None:
                worker.fps_ready.connect(lambda fps, side=side: self._on_fps(side, fps), queued)
            worker.status_changed.connect(lambda text, side=side: self._on_status_changed(side, text), queued)
            worker.fatal_error.connect(self._on_fatal_error, queued)
            self.workers[side] = worker
            self.overlays[side] = overlay
        if len(linked) == 2 and len(self.overlays) == 2:
            self.panel_link = PanelLink({side: self.overlays[side].update_draw_data for side in linked})
        self._running = True
        self._show_nameplates()
        if not excluded:
            QtWidgets.QMessageBox.warning(
                None, "警告",
                "オーバーレイを画面キャプチャから除外できませんでした。\n表示中のドットをぷよとして誤認識する可能性があります。",
            )
        for ext in self.request.extensions:
            ext.session_ready(GAME_ID, self)
        for worker in self.workers.values():
            worker.start()
        self._start_cpu_breakdown()

    def _on_draw_data(self, side: str, data: OverlayDrawData | None) -> None:
        """支援の表示データを表示へ渡す(両方を支援している時は、相手の行を相手側の自分の行にそろえてから)。"""
        if self.panel_link is not None:
            self.panel_link.update(side, data)
        elif side in self.overlays:
            self.overlays[side].update_draw_data(data)

    def _show_nameplates(self) -> None:
        """プレイヤー名の帯に、使っているエンジンの名前を重ねる。"""
        if self.solo or self.game_window is None:
            return
        w = self.game_window
        names = {worker_side(s.side): s.engine.name for s in self.request.sides if s.engine is not None}
        self.nameplates = show_engine_nameplates((w.left, w.top, w.width, w.height), names, _exclude_window_from_screen_capture)

    # ---- CPU 使用率の内訳 ----

    def _ai_label(self, side: str) -> str:
        return f"{side.upper()} の AI"

    def _start_cpu_breakdown(self) -> None:
        """Universal Engine Dock(画面の読み取り・オーバーレイ)・各サイドの思考エンジン・ゲームが、PC 全体の CPU の何 % を使ったかを測る
        (ログを残す時だけ)。AI 側と Universal Engine Dock 側のどちらが CPU を使っているかを切り分けるため。"""
        if not any(w.debug_log_path is not None for w in self.workers.values()):
            return
        groups = [ProcessGroup(APP_CPU_LABEL, os.getpid)]
        for side, client in self.clients.items():
            groups.append(ProcessGroup(self._ai_label(side), lambda c=client: getattr(c, "pid", None), descendants=True))
        if self.game_window is not None:
            groups.append(ProcessGroup(GAME_CPU_LABEL, lambda h=self.game_window.hwnd: window_pid(h)))
        labels = [g.label for g in groups] + [PC_CPU_LABEL]
        self._cpu_meter = ProcessCpuMeter(groups)
        self._cpu_meter.sample()  # 基準を取る(初回は値が出ない)
        self._pc_meter = CpuMeter()
        self._cpu_minute, self._cpu_total = CpuBreakdownStats(labels), CpuBreakdownStats(labels)
        self._log_all("CPU の内訳を1分ごとに記録します(PC 全体の CPU に占める割合。"
                      f"{APP_CPU_LABEL}=画面の読み取り・表示、AI=思考エンジン、ゲーム=ゲーム本体)")
        self._cpu_timer.start(CPU_BREAKDOWN_MS)

    def _sample_cpu(self) -> None:
        if self._cpu_meter is None or self._cpu_minute is None or self._cpu_total is None:
            return
        usage = self._cpu_meter.sample()
        pc = self._pc_meter.sample() if self._pc_meter is not None else None
        if pc is not None:
            usage[PC_CPU_LABEL] = pc
        self._cpu_minute.add(usage)
        self._cpu_total.add(usage)
        if self._cpu_minute.count() >= CPU_BREAKDOWN_LOG_SAMPLES:
            self._log_all(f"CPU の内訳(直近{self._cpu_minute.count()}秒): {self._cpu_minute.summary()}")
            self._cpu_minute.reset()

    def _finish_cpu_breakdown(self) -> None:
        self._cpu_timer.stop()
        if self._cpu_total is not None and self._cpu_total.count():
            self._log_all(f"CPU の内訳(支援モード全体 {self._cpu_total.count()}秒): {self._cpu_total.summary()}")
        self._cpu_meter = self._pc_meter = self._cpu_minute = self._cpu_total = None

    def _log_all(self, text: str) -> None:
        """両方のサイドのログに同じ行を書く(どちらのログを開いても内訳が分かるように)。"""
        for worker in self.workers.values():
            log = getattr(worker, "_log", None)
            if log is not None:
                log(f"[{(getattr(worker, '_now', None) or 0.0):7.2f}s] {text}")

    def _content_bounds(self) -> tuple[float, float, float, float] | None:
        """ゲームの描画領域(黒帯を除く。物理px)。情報パネルをこの中に収める。ウィンドウが分からなければ None。"""
        w = self.game_window
        return content_area((w.left, w.top, w.width, w.height)) if w is not None else None

    def _badge_anchor(self) -> QtCore.QRect | None:
        """バッジを置くゲームのウィンドウのタイトルバー(論理ピクセル)。分からなければ None(画面の右上に置く)。"""
        if self.game_window is None or not self.request.primary:
            return None  # ほかのゲームモードの終了ボタンがタイトルバーにある時は、重ならないよう画面の右上
        rect = title_bar_rect(self.game_window.hwnd)
        if rect is None:
            return None
        return to_logical_rect(*rect, screen_of_rect(*rect))  # タイトルバーのあるディスプレイの倍率で

    # ---- 動いている間 ----

    def is_running(self) -> bool:
        return self._running

    def _check_game_window(self) -> None:
        """ゲームのウィンドウが動いた・大きさが変わった・閉じたら止める(盤面の位置が変わるため)。"""
        w = self.game_window
        if not self._running or w is None:
            return
        if client_rect(w.hwnd) == (w.left, w.top, w.width, w.height):
            return
        self.stop()
        QtWidgets.QMessageBox.information(
            None, APP_TITLE, "ゲームのウィンドウが動いたか大きさが変わったので、支援モードを止めました。\nもう一度開始してください。"
        )

    def _on_fps(self, side: str, fps: float) -> None:
        if self.badge is not None and self.badge.fps_label is not None:
            self.badge.fps_label.set_fps(side, fps)

    def _on_status_changed(self, side: str, text: str) -> None:
        if self.badge is None or side not in self.workers:
            return
        if len(self.workers) == 1:
            prefix = "" if side != "2p" else "[2P] "
            self.badge.set_status(f"{prefix}支援モード中  {text}")
            return
        self._badge_lines[side] = f"{side.upper()}: {text}"
        self.badge.set_lines([self._badge_lines.get(s, f"{s.upper()}: 開始中") for s in self.workers])

    def _on_fatal_error(self, message: str) -> None:
        if not self._running:
            return  # 2つ動かしている時に、止めた後でもう片方のエラーが届いた
        self.stop()
        QtWidgets.QMessageBox.critical(None, "エラー", f"ぷよの支援モードでエラーが発生しました。\n{message}\n(crash_log.txt 参照)")

    # ---- 終了 ----

    def _close_clients(self) -> None:
        clients, self.clients = self.clients, {}
        for client in clients.values():
            try:
                client.close()
            except Exception:  # noqa: BLE001  1つが閉じられなくても残りを閉じる
                pass

    def stop(self) -> None:
        self._running = False
        self._window_watch.stop()
        self._finish_cpu_breakdown()
        workers, self.workers = self.workers, {}
        overlays, self.overlays = self.overlays, {}
        for worker in workers.values():
            worker.stop()
        for worker in workers.values():
            if not worker.wait(3000):
                worker.terminate()
                worker.wait()
        self._close_clients()
        for overlay in overlays.values():
            overlay.close()
        close_nameplates(self.nameplates)
        if self.badge is not None:
            self.badge.close()
            self.badge = None
        if self._cv_threads_before is not None:  # ワーカーを止めてから戻す
            cv2.setNumThreads(self._cv_threads_before)
            self._cv_threads_before = None


def start_assist(request: AssistRequest) -> PuyoAssistSession:
    return PuyoAssistSession(request)
