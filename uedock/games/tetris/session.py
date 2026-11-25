"""テトリスの支援モードの開始と終了。

共通のメイン画面は uedock/game_mode.py の登録口を通じてここを呼ぶ:
1. start_assist(request) — 盤面の位置を決め、思考エンジンを起動する(メイン画面は見えている)
2. session.begin() — メイン画面を隠した後。オーバーレイ・終了ボタン・情報パネルを出し、観測を始める
3. session.stop() — 止める。終了ボタン・ゲームのウィンドウの移動・エラー・長時間の無提示で自分から止まることもある

プラグインの拡張(uedock/extension.py)は、思考エンジンの設定(client_options)・観測ループ(create_worker)・動かす直前
(session_ready)に処理を足せる。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6 import QtCore, QtWidgets

from uedock.game_mode import LAYOUT_VERSUS, AssistRequest, SideSetup
from uedock.games.tetris import assist_view
from uedock.games.tetris.assist_view import load_assist_view, worker_options
from uedock.games.tetris.capture.calibrate import CONFIG_PATH, CalibrationResult, load_calibration
from uedock.games.tetris.capture.window_detect import client_rect, layout_calibration
from uedock.games.tetris.engine.cold_clear_client import ColdClearClient
from uedock.games.tetris.engine.registry import GAME_ID
from uedock.ui.engine_nameplate import close_nameplates, show_engine_nameplates
from uedock.games.tetris.worker import (
    SAFETY_TIMEOUT_MS,
    AssistWorker,
    ControlBadge,
    OverlayWindow,
    _exclude_window_from_screen_capture,
    _new_debug_log_paths,
    attach_fps,
    attach_uptime,
    engine_panel_text,
)
from uedock.paths import APP_TITLE
from uedock.plugin_api import FEATURE_DEBUG_LOG

# ゲームのウィンドウが動いていないかを確かめる間隔(ms)
WINDOW_WATCH_MS = 1000
NO_POSITION_MESSAGE = (
    "テトリスの盤面の位置が分かりません。PC 版はウィンドウ表示で起動して「ゲームのウィンドウを検出」を、"
    "キャプチャボードの映像は「テトリスのキャリブレーション」を実行してください"
)
NO_2P_POSITION_MESSAGE = "テトリスの 2P は、ゲームのウィンドウ(Steam 版)が見つかった時だけ支援できます"


def side_view_path(side: str) -> Path:
    """サイドごとの支援の設定(テンプレ・DPC 等)のファイル。呼んだ時点の置き場所から求める(テストで差し替えられる)。"""
    path = assist_view.ASSIST_VIEW_PATH
    return path if side == "1p" else path.with_name(f"{path.stem}_{side}.json")


def board_side(side: str) -> str:
    """盤面の位置・ログの名前での側。一人用は 1P と同じ位置(左の盤面)。"""
    return "2p" if side == "2p" else "1p"


def queued_type():  # noqa: ANN201
    return QtCore.Qt.ConnectionType.QueuedConnection


class TetrisAssistSession(QtCore.QObject):
    """テトリスの支援モード1回分。受け持つサイドごとに、思考エンジン・観測のスレッド・オーバーレイ・情報パネルを持つ。"""

    def __init__(self, request: AssistRequest) -> None:
        super().__init__()
        self.request = request
        self.game_window = request.game_window
        self.calibrations: dict[str, CalibrationResult] = {s.side: self._calibration_for(s.side) for s in request.sides}
        self.clients: dict[str, ColdClearClient] = {}
        self.workers: dict[str, AssistWorker] = {}
        self.overlays: dict[str, OverlayWindow] = {}
        self.nameplates: list = []  # プレイヤー名の帯に重ねるエンジン名(uedock/ui/engine_nameplate.py)
        self._fps_label = None  # 上のバーの観測の FPS(attach_fps)
        self.badges: dict[str, ControlBadge] = {}
        self._running = False
        self._window_watch = QtCore.QTimer(self)
        self._window_watch.timeout.connect(self._check_game_window)
        # 提示が長い間届かなければ自分で止める(対局が終わったまま放置された時など)。提示が届くたびに数え直す
        self._safety = QtCore.QTimer(self)
        self._safety.setSingleShot(True)
        self._safety.timeout.connect(self.stop)
        try:
            for setup in request.sides:
                # プラグインの拡張が、思考エンジンの「目指すもの」(--opening)を足すことがある
                options: dict = {}
                for ext in request.extensions:
                    options.update(ext.client_options(GAME_ID, setup.side))
                self.clients[setup.side] = self._make_client(setup, options.get("opening"))
        except (FileNotFoundError, RuntimeError, TimeoutError, OSError) as exc:
            # TimeoutError は、起動直後の応答待ちで思考エンジンから返事が無かった時(pythonw でパイプが働かない等)
            self._close_clients()
            raise RuntimeError(f"テトリスの思考エンジンの起動に失敗しました:\n{exc}") from exc

    # ---- 盤面の位置と思考エンジン ----

    def _calibration_for(self, side: str) -> CalibrationResult:
        """そのサイドの盤面・HOLD・NEXT の位置。ゲームのウィンドウが見つかっていればその中の位置、無ければ
        手動キャリブレーション(Switch 用。1P・一人用の位置だけ)。分からなければ理由を付けて断る。"""
        if self.game_window is not None:
            return layout_calibration(self.game_window, board_side(side))
        if side == "2p":
            raise RuntimeError(NO_2P_POSITION_MESSAGE)
        if not CONFIG_PATH.exists():
            raise RuntimeError(NO_POSITION_MESSAGE)
        try:
            return load_calibration(CONFIG_PATH)
        except (KeyError, ValueError, TypeError) as exc:
            raise RuntimeError(f"テトリスのキャリブレーションが不正です。やり直してください:\n{exc}") from exc

    @staticmethod
    def _make_client(setup: SideSetup, opening: str | None = None) -> ColdClearClient:
        engine = setup.engine
        return ColdClearClient((Path(engine.exe), engine.weights), opening=opening, extra_args=list(engine.args) or None)

    # ---- 開始 ----

    def begin(self) -> None:
        sides = [s.side for s in self.request.sides]
        log_wanted = self.request.debug_log and self.request.plugins.has_feature(FEATURE_DEBUG_LOG)
        excluded = True
        for index, setup in enumerate(self.request.sides):
            side = setup.side
            calibration = self.calibrations[side]
            overlay = OverlayWindow(calibration)
            overlay.show()
            excluded = _exclude_window_from_screen_capture(overlay) and excluded

            # 終了ボタンは1つでよい(止める処理は共通)。ほかのゲームモードが出している時(primary でない)と、2つ目の側では出さない
            first = index == 0
            badge_side = "2p" if side == "2p" else ("1p_versus" if "2p" in sides else "1p")
            badge = ControlBadge(on_stop=self.stop, side=badge_side, show_stop=first and self.request.primary)
            if first and self.request.primary:
                attach_uptime(badge)
                self._fps_label = attach_fps(badge, sides)
            badge.info.set_slot("engine", engine_panel_text(setup.engine.name))
            badge.place(self.game_window, calibration)
            badge.show()
            # 情報パネルもゲームのウィンドウの上に重なるので、画面キャプチャから除外する
            excluded = _exclude_window_from_screen_capture(badge) and excluded
            excluded = _exclude_window_from_screen_capture(badge.info) and excluded

            # ログと録画は1つの切り替えで両方オンにする。開始のたびに新しい日時付きのファイル(上書きしない)
            log_path, video_path = _new_debug_log_paths(board_side(side)) if log_wanted else (None, None)
            # 1P と 2P を同時に動かす時は、撮影と認識を側ごとの子プロセスで行う(recognize_process.py)。2P は常に
            separate = side == "2p" or len(sides) > 1
            # プラグインの拡張が観測ループを作ることがある(uedock/extension.py の create_worker)。作らなければ標準のもの
            worker = None
            context = {"calibration": calibration, "client": self.clients[side], "log_path": log_path, "separate": separate,
                       "board_side": board_side(side), "badge": badge, "session": self}
            for ext in self.request.extensions:
                worker = ext.create_worker(GAME_ID, side, context)
                if worker is not None:
                    break
            if worker is None:
                worker = AssistWorker(
                    calibration,
                    self.clients[side],
                    log_path,
                    record_video=log_wanted,
                    video_path=video_path,
                    separate_recognition=separate,
                    side=board_side(side),
                    **worker_options(load_assist_view(side_view_path(side))),
                )
            queued = QtCore.Qt.ConnectionType.QueuedConnection
            worker.draw_data_ready.connect(overlay.update_draw_data, queued)
            worker.draw_data_ready.connect(self._keep_alive, queued)
            worker.next_debug_ready.connect(overlay.update_next_debug_data, queued)
            worker.fatal_error.connect(self._on_fatal_error, queued)
            worker.plan_text_ready.connect(badge.set_plan, queued)
            worker.fps_ready.connect(lambda fps, side=side: self._on_fps(side, fps), queued)
            self.workers[side] = worker
            self.overlays[side] = overlay
            self.badges[side] = badge
        self._running = True
        self._show_nameplates()
        # 除外できないと、自分が描いたドットを盤面のブロックとして読んでしまう。静かに壊れるので必ず知らせる
        if not excluded:
            QtWidgets.QMessageBox.warning(
                None, "警告",
                "オーバーレイを画面キャプチャから除外できませんでした。\n表示中のドットを盤面のブロックとして誤認識する可能性があります。",
            )
        for ext in self.request.extensions:
            ext.session_ready(GAME_ID, self)
        for worker in self.workers.values():
            worker.start()
        if self.game_window is not None:
            self._window_watch.start(WINDOW_WATCH_MS)
        self._safety.start(SAFETY_TIMEOUT_MS)

    def _show_nameplates(self) -> None:
        """プレイヤー名の帯に、使っているエンジンの名前を重ねる。"""
        if self.game_window is None or self.request.layout != LAYOUT_VERSUS:
            return
        w = self.game_window
        names = {board_side(s.side): s.engine.name for s in self.request.sides if s.engine is not None}
        self.nameplates = show_engine_nameplates((w.left, w.top, w.width, w.height), names, _exclude_window_from_screen_capture)

    # ---- 動いている間 ----

    def is_running(self) -> bool:
        return self._running

    def _on_fps(self, side: str, fps: float) -> None:
        if self._fps_label is not None:
            self._fps_label.set_fps(side, fps)

    def _keep_alive(self, draw_data) -> None:  # noqa: ANN001
        """提示が届いた: 自動で止めるまでの時間を数え直す。"""
        if draw_data is not None and self._safety.isActive():
            self._safety.start(SAFETY_TIMEOUT_MS)

    def _check_game_window(self) -> None:
        """ゲームのウィンドウが動いた・大きさが変わった・閉じたら止める(位置がずれて誤った提示をしないため)。"""
        w = self.game_window
        if not self._running or w is None:
            return
        if client_rect(w.hwnd) == (w.left, w.top, w.width, w.height):
            return
        self.stop()
        QtWidgets.QMessageBox.information(
            None, APP_TITLE, "ゲームのウィンドウが動いたか大きさが変わったので、支援モードを止めました。\nもう一度開始してください。"
        )

    def _on_fatal_error(self, message: str) -> None:
        if not self._running:
            return  # 2つ動かしている時に、止めた後でもう片方のエラーが届いた
        self.stop()
        QtWidgets.QMessageBox.critical(None, "エラー", f"思考エンジンとの通信が切断されました。テトリスの支援モードを終了します。\n{message}")

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
        self._safety.stop()
        workers, self.workers = self.workers, {}
        for side, worker in workers.items():
            # ワーカーは思考エンジンが応答しなくなると自分で作り直す。後始末は必ずワーカーが持っている現物に対して行う
            # (でないと作り直した方のプロセスが残る)
            self.clients[side] = getattr(worker, "real_engine", worker.cold_clear)
            worker.stop()
        for worker in workers.values():
            # ワーカーは思考エンジンの応答を待って止まっていることがある。一定時間で終わらなければ強制終了する
            if not worker.wait(3000):
                worker.terminate()
                worker.wait()
        self._close_clients()
        overlays, self.overlays = self.overlays, {}
        self._fps_label = None
        badges, self.badges = self.badges, {}
        for widget in (*overlays.values(), *badges.values()):
            widget.close()
        close_nameplates(self.nameplates)


def start_assist(request: AssistRequest) -> TetrisAssistSession:
    return TetrisAssistSession(request)
