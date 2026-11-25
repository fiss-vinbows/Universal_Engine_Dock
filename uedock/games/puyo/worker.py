"""ぷよの支援モードの中身

支援モード中の「画面キャプチャ → 認識 → 状態追跡 → (手番ごとに) 思考エンジンで思考 → オーバーレイ表示」
のループ(AssistWorker、別スレッド)と、オーバーレイ・終了バッジのウィンドウを持つ。
始め方・止め方は session.py、サイドごとの設定項目は side_panel.py。

Tetris版の実機で得た知見を踏襲している:
  - オーバーレイ自身が画面キャプチャに写り込み盤面を汚染しないよう、
    SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) でキャプチャから除外する
  - 提案の描画は repaint()+DwmFlush() で同期させ、前の提案との二重表示を防ぐ
  - ワーカースレッドの未処理例外は crash_log.txt に残す
"""

from __future__ import annotations

import ctypes
import threading
from collections import deque
import sys
import time
import traceback
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: E402

from ...capture.screen_capture import PrefetchCapture  # noqa: E402
from .capture.calibrate import CalibrationResult  # noqa: E402
from .engine.book_client import BookClient, book_text, is_book_move  # noqa: E402
from .engine.ama_client import (  # noqa: E402
    NoValidPlacementError,
    FIRE_REASON_TEXT_JA,
    FORM_TEXT_JA,
    AmaClient,
    BattleMove,
    PlayerState,
    action_text,
    diag_text,
    is_build_like,
)
from .engine.puyo_field import COLS, ROWS, PuyoField  # noqa: E402
from .engine.think_input import build_think_input  # noqa: E402
from .engine.margin_time import margin_target_point  # noqa: E402
from .engine.background import ImageSaver, ThinkWorker  # noqa: E402
from .engine.threat import AttackEpisode, TurnClock  # noqa: E402
from .engine.tracker import Pair, PlayerTracker, Snapshot, clear_spawn_cells, count_puyos, is_covered, settle_cells  # noqa: E402
from .vision.cell_classifier import EMPTY, GAME_COLORS, GARBAGE  # noqa: E402
from .overlay.renderer import OverlayDrawData, OverlayLayout, PanelRows, plan_dots, render_overlay, render_perf  # noqa: E402
from .engine.perf_meter import CpuMeter, FpsMeter  # noqa: E402
from ... import i18n  # noqa: E402
from ...paths import app_root, config_dir  # noqa: E402
from ...obs_output import SKIP_PROPERTY as OBS_SKIP_PROPERTY
from ...ui.screen_map import dpr_of, native_rect, screen_of_rect, to_logical_rect  # noqa: E402
from .vision.banner_detector import AllClearBannerDetector  # noqa: E402
from .vision.board_reader import cells_to_text, read_board, read_next  # noqa: E402
from .vision.color_mapper import ColorMapper  # noqa: E402
from .chain_score import ChainCount, ChainScoreCounter  # noqa: E402
from .vision.score_reader import ScoreReader  # noqa: E402

# 認識ループの周期(秒)。キャプチャ約20ms+認識数msなので、これで約30fps相当
TICK_INTERVAL_SEC = 0.03
# 観測の間隔・処理時間をまとめてログに出す周期(秒)
TICK_STATS_SECONDS = 60.0
# オーバーレイウィンドウを盤面より少し大きく取る余白(物理px)
OVERLAY_MARGIN = 40
# 情報パネルを自分の盤面の外側(1P は左、2P は右)に置くため、その側だけ余白を広く取る(物理px)
OVERLAY_LEFT_SPACE = 320
# デバッグ用に保存する切り出し画像の直近件数
FRAME_HISTORY_SIZE = 60
# 自分の連鎖を点滅から検出した時に保存する直前のフレーム数
FLASH_EVIDENCE_FRAMES = 6
# 再現した連鎖を待ち切って捨てた時の調査用。連鎖の間この間隔で画面を覚えておき、
# 捨てた時に保存する。待ちは最短4秒なので6秒分。ログを出している時だけ、1回の支援モードで保存するのはこの回数まで
CHAIN_EVIDENCE_INTERVAL_SECONDS = 0.5
CHAIN_EVIDENCE_FRAMES = 12
CHAIN_EVIDENCE_LIMIT = 10
# ゲームの点数表示(盤面の下の8桁)を読む。
# 数字の見本(ゲームの画面から作るので公開しない)。tools/make_puyo_score_digits.py で作る。無ければ読まない
SCORE_DIGITS_NAME = "puyo_score_digits.npz"
# 何 tick に1回読むか(1回 約4.5ms。連鎖の段の「40×8」は 0.1秒以上出ている)
SCORE_READ_EVERY = 2
# 連鎖が終わってから、この秒数まで(か次の組ぷよが出るまで)合計の増加を連鎖の点数に数える
CHAIN_SCORE_SETTLE_SEC = 1.0
# 連鎖が終わった後、ゲームの点数表示で確かめた連鎖の点数をパネルに出しておく秒数(その後は思考の解析に戻る)
FIRE_RESULT_HOLD_SEC = 2.0
# CPU 使用率と観測の FPS の表示を更新する間隔(秒)
PERF_INTERVAL_SEC = 1.0
# 盤面の積みが(この個数以上あった状態から)0になり、それが NEW_GAME_EMPTY_SECONDS 以上
# 続いたら「新しい試合」とみなし、追跡状態と色の割当をリセットする(試合ごとに使われる
# 4色が変わるため、前の試合の割当のままだと5色目エラーになる)。対戦では両盤面、
# 一人用モードでは自分の盤面だけで判定する。画面効果で一瞬だけ盤面が空に見える
# ことがある(一人用モードで実機確認)ため、継続時間を要求する
NEW_GAME_MIN_PUYOS = 8
NEW_GAME_EMPTY_SECONDS = 1.0
# 全消し直後は次の組ぷよを置くまで盤面が空のままになる。ボーナス持ちの間はこの秒数まで待つ
NEW_GAME_EMPTY_SECONDS_AFTER_ALL_CLEAR = 10.0
# 負けは3列目が12段目まで埋まった時なので、盤面が一気に消える時は必ず12個以上消える。これ以上消えて全消しにならなかった
# (all_clear_unconfirmed)ら試合終了とみなす(試合中に点滅を見逃した小さな全消しで相手のボーナスを消さないための下限)
GAME_OVER_MIN_DROPPED = 12
# 取り込み待ちがこの秒数を超えたら診断情報をログに残す
STUCK_WARN_SECONDS = 3.0


def _exclude_window_from_screen_capture(widget: QtWidgets.QWidget) -> bool:
    """このウィンドウを画面キャプチャ(mss等)の対象から除外する。成功したらTrueを返す。

    Windows 10 2004以降の SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) を使う。
    HWND はポインタサイズなので argtypes を明示しないと64bit環境で上位ビットが
    欠落して静かに失敗する(Tetris版で長期間の誤診断を招いた)。失敗は必ず呼び出し側へ返す。
    """
    if sys.platform != "win32":
        return False
    WDA_EXCLUDEFROMCAPTURE = 0x00000011
    try:
        import ctypes.wintypes

        set_affinity = ctypes.windll.user32.SetWindowDisplayAffinity
        set_affinity.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.DWORD]
        set_affinity.restype = ctypes.wintypes.BOOL
        hwnd = ctypes.wintypes.HWND(int(widget.winId()))
        if not set_affinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
            return False
        get_affinity = ctypes.windll.user32.GetWindowDisplayAffinity
        get_affinity.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)]
        get_affinity.restype = ctypes.wintypes.BOOL
        affinity = ctypes.wintypes.DWORD(0)
        if not get_affinity(hwnd, ctypes.byref(affinity)):
            return False
        return affinity.value == WDA_EXCLUDEFROMCAPTURE
    except (OSError, AttributeError):
        return False


class OverlayWindow(QtWidgets.QWidget):
    """盤面周辺だけをカバーする透過・クリックスルーの描画専用ウィンドウ"""

    def __init__(self, calibration: CalibrationResult, language: str | None = None,
                 bounds: tuple[float, float, float, float] | None = None) -> None:
        super().__init__()
        self.calibration = calibration
        # 情報パネルの表記("ja" / "en" / 言語プラグインのキー)。None ならメイン画面の「表示の言語」に従う(切り替えればすぐ変わる)
        self._language = language
        self.draw_data: OverlayDrawData | None = None
        self.perf_text = ""  # CPU 使用率と観測の FPS。盤面の上端の枠外に出す
        self.panel_rows = PanelRows()  # 情報パネルの種類ごとの行数(入らない種類は行を足す。描くたびに引き継ぐ)

        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground)

        # 画面キャプチャ(mss)は物理ピクセル座標、Qtのウィンドウ配置は論理ピクセル座標。
        # 倍率は盤面のあるディスプレイのもの
        region = calibration.capture_region()
        screen = screen_of_rect(region.left, region.top, region.width, region.height)
        self.dpr = dpr_of(screen)
        screen_left, _top, screen_width, _height = native_rect(screen) if screen is not None else (0, 0, 1 << 20, 0)
        screen_right = screen_left + screen_width - 1
        if bounds is not None:
            # ゲームの描画領域(bounds。物理px)が分かれば、
            # その外には広げない(OBS 出力はゲームの描画領域だけを写すので、外に出たパネルは切れる)
            screen_left = max(screen_left, round(bounds[0]))
            screen_right = min(screen_right, round(bounds[0] + bounds[2]) - 1)
        # 情報パネルは自分の盤面の外側に置く: 1P(左の盤面)は左、2P(右の盤面)は右に余白を広く取る
        self.panel_right = calibration.self_on_right()
        if self.panel_right:
            left_space = min(OVERLAY_MARGIN, region.left - screen_left)
            right_space = max(OVERLAY_MARGIN, min(OVERLAY_LEFT_SPACE, screen_right - (region.left + region.width)))
        else:
            left_space = min(OVERLAY_LEFT_SPACE, region.left - screen_left)  # ディスプレイの左端からはみ出さない範囲で
            right_space = OVERLAY_MARGIN
        left_space = max(0, left_space)
        self.origin = (region.left - left_space, region.top - OVERLAY_MARGIN)
        w = region.width + left_space + right_space
        h = region.height + OVERLAY_MARGIN * 2
        self.setGeometry(to_logical_rect(self.origin[0], self.origin[1], w, h, screen))

    @property
    def language(self) -> str:
        return self._language or i18n.language()

    def update_draw_data(self, data: OverlayDrawData | None) -> None:
        self.draw_data = data
        self.repaint()
        if sys.platform == "win32":
            try:
                ctypes.windll.dwmapi.DwmFlush()
            except OSError:
                pass

    def update_perf(self, text: str) -> None:
        """CPU 使用率と観測の FPS の表示を更新する(1秒ごと。提案の表示とは別に描き直す)"""
        self.perf_text = text
        self.update()

    def paintEvent(self, event: QtCore.QEvent) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 0))
        painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceOver)
        layout = OverlayLayout(
            self_geometry=self.calibration.self_player.geometry(),
            enemy_geometry=self.calibration.enemy_player.geometry(),
            origin=self.origin,
            dpr=self.dpr,
            language=self.language,
            panel_rows=self.panel_rows,
        )
        if self.panel_right:
            board_right = layout.to_local(*layout.self_geometry.grid_to_screen(COLS, 0)).x()
            layout.panel_right_space = self.width() - board_right
        if self.draw_data is not None:
            render_overlay(painter, layout, self.draw_data)
        render_perf(painter, layout, self.perf_text)
        painter.end()


# 終了ボタンの見た目はテトリスと同じ
STOP_BUTTON_STYLE = (
    "QPushButton { color: white; background: #111; font-weight: bold; border: 1px solid #555; padding: 2px 14px; }"
    "QPushButton:hover { background: #333; }"
)
STOP_BUTTON_TEXT = "支援モードを終了"


class ControlBadge(QtWidgets.QWidget):
    """支援モード中に常時表示する、クリックスルーなしの上のバー。

    ゲームのウィンドウが分かっている時は、そのタイトルバーの中央に置く。
    テトリスと同じく
    起動時間・CPU 使用率・観測の FPS(add_status_labels)・「支援モードを終了」と、プラグインが足す部品(add_widget)だけを置く。
    検出したウィンドウの名前と状態の文言はバーに出さない(状態の文言は、バーにマウスを載せた時のツールチップで見られる)。"""

    def __init__(self, on_stop, anchor: QtCore.QRect | None = None) -> None:  # noqa: ANN001
        super().__init__()
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
            # クリックしても前面にしない
            | QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.anchor = anchor  # タイトルバー(論理ピクセル)。None なら画面の右上
        self.setProperty(OBS_SKIP_PROPERTY, True)  # 上のバーは OBS 出力に出さない(配信には要らない)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        stop_btn = QtWidgets.QPushButton(STOP_BUTTON_TEXT)
        # 標準の淡い灰色では白いタイトルバーに埋もれて見つからなかったので黒地にする
        stop_btn.setStyleSheet(STOP_BUTTON_STYLE)
        stop_btn.clicked.connect(on_stop)
        stop_btn.setFixedWidth(stop_btn.sizeHint().width())
        self.stop_button = stop_btn
        layout.addWidget(stop_btn)
        self._layout = layout
        self.status_text = "支援モード中"  # 状態の文言(ツールチップ)
        self.setToolTip(self.status_text)
        self.extra_widgets: list[QtWidgets.QWidget] = []  # プラグインが足した部品(add_widget)
        self.status_labels: list[QtWidgets.QLabel] = []  # 起動時間・CPU 使用率・観測の FPS(add_status_labels)
        self.fps_label = None
        self._resize()

    def add_status_labels(self, sides: list[str] | None = None) -> None:
        """左端に起動時間・PC 全体の CPU 使用率・観測の FPS を置く。"""
        from uedock.ui.status_labels import CpuUsageLabel, FpsLabel, UptimeLabel

        self.fps_label = FpsLabel(sides or ["1p"])
        self.status_labels = [UptimeLabel(), CpuUsageLabel(), self.fps_label]
        for index, label in enumerate(self.status_labels):
            self._layout.insertWidget(index, label)
        self._resize()

    def add_widget(self, widget: QtWidgets.QWidget) -> None:
        """終了ボタンの右に部品を足す(プラグインの拡張が使う)。"""
        self.extra_widgets.append(widget)
        self._layout.addWidget(widget)
        self._resize()

    def set_status(self, text: str) -> None:
        self.set_lines([text])

    def set_lines(self, lines: list[str]) -> None:
        """状態の文言(1P と 2P を同時に動かす時は2行)。バーには出さず、ツールチップにする。"""
        self.status_text = "\n".join(lines)
        self.setToolTip(self.status_text)

    def _resize(self) -> None:
        hint = self._layout.sizeHint()
        width = hint.width()
        if self.anchor is not None:
            self.setFixedSize(width, max(24, self.anchor.height()))
            self.move(self.anchor.x() + (self.anchor.width() - width) // 2,
                      self.anchor.y() + (self.anchor.height() - self.height()) // 2)
            return
        self.setFixedSize(width, max(24, hint.height()))
        screen = QtWidgets.QApplication.primaryScreen().geometry()
        self.move(screen.width() - self.width() - 20, 20)


# 手番の途中で再計算するのは「入力が変わった時」だけ: 相手の消去の開始/終了、自分への予告おじゃまが
# 前回の思考からこれ以上(1段=6個)増えた時。ラベルによる固定はしない
RETHINK_PENDING_INCREASE = 6
# 「全消し」の文字がこの一致度以上で見えたら、そのプレイヤーは全消しを持っているとする
ALL_CLEAR_BANNER_MIN_SCORE = 0.9
# 相手の発火を想定した先読み:
# 攻撃されていない時、相手が今すぐ撃てる攻撃がこの個数以上なら、それを受けた状態での次の手も先に計算しておく。
# 相手の連鎖が始まった直後の手番で、見積もった着弾量が想定に近ければその手順を出現時点で送る
ANTICIPATE_MIN_GARBAGE = 12
# 観測の空白: 1tick(取得+認識+思考)がこれ以上かかったらログに残す
OBSERVATION_GAP_WARN_SEC = 0.25
# 相手の連鎖を再現した着弾量を、連鎖終了後に予告が無いままこの秒数続いたら解除する
INCOMING_CARRY_CLEAR_SECONDS = 2.0
# 再計算の結果がこれら(=対応不要の構築)で、入力(着弾量)も変わっていなければ表示中の提案を変えない
# (ビームサーチの乱数で構築の手は毎回変わるため、そのまま出すと提案が振動する)
BUILD_LABELS = ("build", "accept")

class AssistWorker(QtCore.QThread):
    """支援モードのメインループ(別スレッド)。

    毎tick: 1回のキャプチャで自分・相手の盤面とネクストを読み、各 PlayerTracker に
    渡す。自分側の turn_ready が出たら、その時点の両者の状態で ama(battle) を呼び、
    提案を draw_data_ready で通知する。提案は次の turn_ready まで固定する
    (ネクスト・盤面が変わらない限り表示を切り替えない、という方針)。
    """

    draw_data_ready = QtCore.pyqtSignal(object)
    status_changed = QtCore.pyqtSignal(str)
    fatal_error = QtCore.pyqtSignal(str)
    fps_ready = QtCore.pyqtSignal(float)  # 観測の FPS(1秒ごと)。上のバーに出す

    def __init__(
        self,
        calibration: CalibrationResult,
        client: AmaClient,
        debug_log_path: Path | None,
        solo: bool = False,
        side: str = "1p",
        haipuyo: bool = False,
        peek: bool = False,
        enemy_score: bool = False,
    ) -> None:
        super().__init__()
        self.calibration = calibration
        # 相手の盤面の点数欄も読む(片側だけを支援している時。両方を支援している時は、相手側の支援が読んだ値を
        # パネルの相手の行に出すので読まない。session.py)
        self.enemy_score = enemy_score and not solo
        # 支援する側。1P と 2P を同時に動かせるよう、画像の保存先を分ける
        self.side = side
        self.history_dir = app_root() / ("debug_frames_history" if side == "1p" else f"debug_frames_history_{side}")
        self.client = client
        self.debug_log_path = debug_log_path
        self.solo = solo  # 一人用モード: 相手を見ず、連鎖構築だけを提案する
        # 配ぷよ読み: 要求に試合の最初から置いた組ぷよと置いた数を入れる(ブリッジは HAIPUYO_BRIDGE_ARGS で起動する)
        self.haipuyo = haipuyo
        # 相手のツモを覗く: 要求に自分と相手の置いた数を入れる(ブリッジは PEEK_BRIDGE_ARGS で起動する)
        self.peek = peek
        self._last_data: OverlayDrawData | None = None  # 直近の描画内容
        self.last_cpu: float | None = None  # 直近に測った PC 全体の CPU 使用率(%、1秒ごと。ログ用)
        self._enemy_fire: tuple[int, int, bool] | None = None  # 相手が撃っている(撃った)連鎖。パネルの相手の欄に出す
        self._enemy_fire_end = 0.0  # 相手の連鎖が終わった時刻(perf_counter)
        # 自分が撃っている(撃った)連鎖 (連鎖数, 点数, 連鎖中か)。パネルの自分の欄に出す
        self._self_fire: tuple[int, int, bool] | None = None
        self._self_fire_end = 0.0
        self._self_fire_first: tuple[int, int, bool] | None = None
        # ゲームの点数表示(盤面の下)。見本が無ければ読まない(_update_score)。盤面ごとに連鎖の点数を数える
        self._score_reader: ScoreReader | None = ScoreReader.load(config_dir() / SCORE_DIGITS_NAME)
        self._score_tick = 0
        self._scores: dict[str, ChainScoreCounter] = {"self": ChainScoreCounter(), "enemy": ChainScoreCounter()}
        self._think_submitted = 0.0  # 最後に反映した思考を依頼した時刻(perf_counter)
        self._pending_at_think = 0  # 前回の思考時の自分への予告おじゃま(再計算の判断用)
        self._fifth_color_logged: tuple | None = None  # 5色目の診断を残した (手番, 盤面の由来)
        self._incoming_at_think = 0  # 前回の思考時の着弾量(構築のままの再計算を維持するかの判断用)
        self._think_pending = False  # turn_ready で思考できなかった(ネクスト未認識等)ので、認識が揃い次第やり直す
        self._last_form: str | None = None  # 前の手番で提案が最も近かった形(移行の表示用)
        self._running = True
        self.trackers = {"self": PlayerTracker(), "enemy": PlayerTracker()}
        self.mapper = ColorMapper()
        self._history_index = 0
        # 先読み: (期待する設置後の盤面, その盤面に対する提案)。提案を出した直後に
        # 「提案通りに置いた後の盤面」で次の手を計算しておき、次の手番で観測が一致したら即表示する
        # (期待する設置後の盤面, その時の(現在の組ぷよ, ネクスト), 提案)。組ぷよも一致した時だけ使う
        self._precomputed: tuple[list[list[str]], tuple[Pair | None, Pair | None], BattleMove] | None = None
        self._precomputed_attack: tuple | None = None  # 先読みを計算した時の攻撃の状況(_attack_snapshot)
        # 相手の発火を想定した先読み(盤面, (組ぷよ, ネクスト), 手, 想定した着弾量)。相手の連鎖が始まった直後の出現で使う
        self._precomputed_alt: tuple | None = None
        self._last_move: BattleMove | None = None  # 最後に表示した現在の手
        # 固定した提案 (手番, 手)(提案を固定する拡張が記録する。_committed_move)。固定した後の思考の手が違っても、
        # 表示と次の手の先読みはこちらで行う
        self._fixed_move: tuple[int, BattleMove] | None = None
        # 計算中の先読みの依頼(結果が届くか、実行前に取り消すと None)と、最後に依頼した先読みの前提(重複の依頼を避ける)
        self._pre_inflight: dict | None = None
        self._pre_basis: tuple | None = None
        self._max_seen = {"self": 0, "enemy": 0}  # 現在の試合で観測した積みの最大個数
        self._banner_seen = {"self": False, "enemy": False}  # 「全消し」の文字が見えているか(ログ用)
        # 開始前の初手: 組ぷよの並び(2組または3組) → 計算した手。開始の出現で、出現した組ぷよ・ネクストに合うものを先読みとして使う
        self._opening_moves: dict[tuple[Pair, ...], BattleMove] = {}
        self._opening_requested: set[tuple[Pair, ...]] = set()  # 思考スレッドに依頼済みの並び
        self._banner_detectors = {"self": AllClearBannerDetector(), "enemy": AllClearBannerDetector()}
        self._empty_since: float | None = None  # 判定対象の盤面がすべて空になった時刻
        self._stuck_logged: set[tuple[str, float]] = set()  # (誰, 最後に取り込めた時刻)
        self._recent_frames: deque[tuple[float, np.ndarray]] = deque(maxlen=FLASH_EVIDENCE_FRAMES)
        self._now: float | None = None  # この tick の時刻(思考入力の期限の見積もりに使う)
        self._game_start_t = 0.0  # 現在の試合が始まった時刻(診断用)
        # マージンタイム: 自分の最初の組ぷよが出た時刻と、最後に観測した時刻。経過秒数からおじゃま1個に必要な点を出す
        self._first_spawn_t: float | None = None
        self._frame_t = 0.0
        # 新方針の第1段階(記録のみ): 相手の攻撃エピソードと自分の平均手時間
        self._attack: AttackEpisode | None = None  # 進行中の相手の連鎖
        self._attack_count = 0
        self._turn_clock = TurnClock()
        # 実行できなかった再計算の理由(自分が取り込み待ちの間に相手の攻撃を検出した等)を次の tick へ持ち越す
        self._deferred_rethink = ""
        # 自分に降るおじゃま(個)。相手の連鎖を盤面から再現できた時だけブリッジが返し、予告が消える
        # (落下・相殺)まで保持する。予告のアイコン数(0〜6)では大きな攻撃を過小評価するため
        self._incoming_carry = 0
        self._carry_zero_since: float | None = None
        # 先読み(提案通りに置いた後の盤面で次の手を事前計算)。観測の空白を減らす検証のために切れる
        # 常に有効。分析文書第3弾の提案で一度無効にしたが、2手先の表示が無いと置くのが遅くなり相手の
        # 攻撃に間に合わなくなった。観測の空白の対策は先読みを止めるのではなく、
        # 思考を別スレッドに分ける(非同期化)方向で行う
        self.enable_precompute = True
        # 観測を止めない: 思考は思考スレッド、画像保存は保存スレッドで行う。
        # 思考スレッドは run() で作る(テストでは作らず、その場で実行して結果を反映する)
        self._think_worker: ThinkWorker | None = None
        self._client_lock = threading.Lock()  # ブリッジの呼び出しを直列にする(思考スレッドと共有)
        self._saver = ImageSaver()
        self._self_spawn_t = 0.0  # 自分の組ぷよが出現した時刻
        # 取り込みを待たずに考えた手(思考の依頼に early_board を付けたもの)を反映している最中か
        self._applying_early = False
        self._t0_perf = time.perf_counter()  # 観測ループの開始時刻(perf_counter)
        self._chain_frames: deque[tuple[float, np.ndarray]] = deque(maxlen=CHAIN_EVIDENCE_FRAMES)
        self._chain_evidence_saved = 0
        self._chain_saver = ImageSaver(maxsize=CHAIN_EVIDENCE_FRAMES * 2)

    # ---- プラグインが継承して足す処理(既定は何もしない) ----
    # 提案を固定する(一度出した手を手番の途中で変えない)拡張は、このクラスを継承して次を上書きする(uedock/extension.py の create_worker で作る)。

    def _fixes_moves(self) -> bool:
        """提案を固定する拡張が動いているか。動いている間は、相手の攻撃が絡む局面や小さい消去で先読みの手を
        すぐには出さず、相手の発火を想定した先読みも計算する。"""
        return False

    def _committed_turn(self) -> int | None:
        """提案を固定した手番(無ければ None)。"""
        return None

    def _on_tracker_event(self, t: float, who: str, ev, tr: PlayerTracker) -> None:  # noqa: ANN001
        """追跡の出来事(spawn など)を受け取る。"""

    def _on_initial_reset(self) -> None:
        """開始前の画面で初手を誤検出していた(追跡が取り消した): その手番で出した提案を消し、本当の初手で出し直す。"""
        self._fixed_move = None
        self._last_data = None
        self.draw_data_ready.emit(None)

    def _precomputed_ready(self, me: PlayerTracker) -> None:
        """組ぷよが出た時点で、その手番の先読みの手が使えるかもしれない(出現・初手・遅れて届いた先読み)。"""

    def _on_take_in(self, me: PlayerTracker) -> None:
        """盤面を取り込んで、その手番の思考を始める直前。"""

    def _on_move(self, me: PlayerTracker, move: BattleMove) -> None:
        """その手番の提案を出した(表示を更新した)。"""

    def _before_results(self, t: float, frame_bgr: np.ndarray, me: PlayerTracker) -> None:
        """毎 tick、思考の結果を反映する前。"""

    def _after_results(self, me: PlayerTracker) -> None:
        """毎 tick、思考の結果を反映した後。"""

    def _on_new_game(self) -> None:
        """新しい試合として追跡状態をリセットした。"""

    def _on_stop(self) -> None:
        """観測ループを止める。"""

    def _header_extra(self) -> str:
        """ログの最初の行に足す設定(先頭に空白を付ける)。"""
        return ""

    def _reset_game(self, t: float = 0.0) -> None:
        self._game_start_t = t
        self._first_spawn_t = None
        for counter in self._scores.values():
            counter.reset()
        if self._think_worker is not None:
            self._think_worker.cancel("main")
            self._think_worker.cancel("pre")
            self._think_worker.cancel("pre_atk")
        self._stuck_logged.clear()
        self._last_form = None
        self._last_data = None
        self._enemy_fire = None
        self._enemy_fire_end = 0.0
        self._self_fire = None
        self._self_fire_end = 0.0
        self._think_submitted = 0.0
        self.trackers = {"self": PlayerTracker(), "enemy": PlayerTracker()}
        self.mapper = ColorMapper()
        self._precomputed = None
        self._precomputed_alt = None
        self._fixed_move = None
        self._pre_inflight = None
        self._pre_basis = None
        self._max_seen = {"self": 0, "enemy": 0}
        self._banner_detectors = {"self": AllClearBannerDetector(), "enemy": AllClearBannerDetector()}
        self._opening_moves = {}
        self._opening_requested = set()
        self._on_new_game()
        self._empty_since = None
        self._attack = None
        self._attack_count = 0
        self._turn_clock = TurnClock()
        self._deferred_rethink = ""
        self._incoming_carry = 0
        self._think_pending = False
        self._pending_at_think = 0
        self.draw_data_ready.emit(None)

    def _on_game_over(self, t: float, who: str, tr: PlayerTracker) -> None:
        """盤面が一気に空になり全消しにならなかった(負け)時、両者の全消しボーナスの記録を消す。

        ボーナス持ちの間は新しい試合の検出を10秒待つ(NEW_GAME_EMPTY_SECONDS_AFTER_ALL_CLEAR)ので、全消しの直後に
        試合が終わると次の試合が始まってもリセットされず、前の試合の組ぷよが残って5色目で約2秒思考できず、次の試合を
        全消しボーナス持ちとして戦った。試合が終われば
        ボーナスは無意味なので消して、通常の1秒待ちでリセットさせる。"""
        if tr.chain_dropped < GAME_OVER_MIN_DROPPED:
            return
        for w, other in self.trackers.items():
            if other.all_clear:
                other.all_clear = False
                self._log(f"[{t:7.2f}s] {who}の盤面が一気に消えた({tr.chain_dropped}個、負け): 試合終了として{w}の全消しボーナスを消します")

    def _log_fifth_color(self, t: float, me: PlayerTracker, en: PlayerTracker, my_pairs: list, frame_bgr: np.ndarray) -> None:
        """試合の途中(盤面が大きい)で5色目が出て思考できなかった時、どこに5色目があるかと画面を残す(手番ごとに1回)。
        原因(盤面の誤認・ネクストの誤認・試合の切り替わり)を画像で調べるための診断。"""
        if self._fifth_color_logged == (me.turn, me.last_take_in_reason):
            return
        self._fifth_color_logged = (me.turn, me.last_take_in_reason)
        known = set(self.mapper.mapping)
        board_colors = {c for row in me.settled for c in row if c in GAME_COLORS}
        pair_colors = {c for p in my_pairs if p for c in p}
        enemy_colors = {c for row in en.settled for c in row if c in GAME_COLORS}
        path = self.history_dir / f"fifth_color_self_{t:07.2f}s_turn{me.turn}.png"
        self._saver.save(path, frame_bgr)
        self._log(
            f"           5色目の診断: 色の割当={self.mapper.mapping} 自分の盤面の色={sorted(board_colors)}"
            f"(未割当 {sorted(board_colors - known)}) 組ぷよ={my_pairs}(未割当 {sorted(pair_colors - known)})"
            f" 相手の盤面の色={sorted(enemy_colors)} 盤面の由来={me.last_take_in_reason} 画像={path.name}\n"
            f"{_indent(cells_to_text(me.settled))}"
        )

    def _check_new_game(self, t: float, snaps: dict[str, Snapshot]) -> None:
        targets = ["self"] if self.solo else ["self", "enemy"]
        if any(is_covered(snaps[who].cells, snaps[who].eyeless_cells) for who in targets):
            return  # 演出に覆われている間は判定しない
        counts = {who: count_puyos(settle_cells(snaps[who].cells)) for who in targets}
        for who, c in counts.items():
            self._max_seen[who] = max(self._max_seen[who], c)
        if all(c == 0 for c in counts.values()) and all(self._max_seen[w] >= NEW_GAME_MIN_PUYOS for w in targets):
            # 全消しボーナスを持っている間は、全消し直後の空盤面(次の手を置くまで続く)と区別できないので長く待つ
            wait = NEW_GAME_EMPTY_SECONDS_AFTER_ALL_CLEAR if any(self.trackers[w].all_clear for w in targets) else NEW_GAME_EMPTY_SECONDS
            if self._empty_since is None:
                self._empty_since = t
            elif t - self._empty_since >= wait:
                self._log(f"[{t:7.2f}s] 盤面の積みが消えたまま{wait:.0f}秒: 新しい試合として追跡状態と色の割当をリセット")
                self._reset_game(t)
                self.status_changed.emit("新しい試合を検出")
        else:
            self._empty_since = None

    def stop(self) -> None:
        self._running = False
        self._on_stop()
        if self._think_worker is not None:
            self._think_worker.stop()
        self._saver.stop()
        self._chain_saver.stop()

    def run(self) -> None:  # noqa: D102
        self._think_worker = ThinkWorker()
        self._client_lock = self._think_worker.lock
        try:
            # 画面の取得を別スレッドで1枚先に進め、読み取りと並行させる
            with PrefetchCapture() as capture:
                self._loop(capture)
        except Exception as exc:  # noqa: BLE001
            crash_log = app_root() / "crash_log.txt"
            with open(crash_log, "a", encoding="utf-8") as f:
                f.write(f"\n===== クラッシュ(AssistWorker) {datetime.now().isoformat()} =====\n")
                traceback.print_exc(file=f)
            self.fatal_error.emit(str(exc))

    def _release_carry_if_pending_cleared(self, t: float, me: PlayerTracker) -> None:
        """自分への予告が消えたら、保持している着弾見込みを解除する。

        相手の連鎖中は解除しない: 自分の連鎖の相殺で予告の表示が一時的に0になっても、残りの段の着弾は続く
        """
        if me.pending_garbage == 0 and self._incoming_carry > 0 and not self.trackers["enemy"].chain_active:
            self._log(f"[{t:7.2f}s] 予告が消えたので着弾量 {self._incoming_carry}個 を解除")
            self._incoming_carry = 0

    def _save_flash_evidence(self, t: float) -> None:
        """自分の連鎖を点滅から検出した時、直前のフレームを保存してログに残す(誤検出の調査用)。"""
        names = []
        for ft, frame in self._recent_frames:
            path = self.history_dir / f"flash_self_{t:07.2f}s_{ft:07.2f}.png"
            self._saver.save(path, frame, [cv2.IMWRITE_PNG_COMPRESSION, 1])
            names.append(path.name)
        self._log(f"[{t:7.2f}s] 点滅検出の直前フレームを保存: {', '.join(names)}")

    def _remember_chain_frame(self, t: float, frame: np.ndarray) -> None:
        """連鎖の間だけ画面を間引いて覚える(連鎖が無ければ手放す)。ログを出していない時は何もしない。"""
        if self.debug_log_path is None:
            return
        if not any(tr.chain_active for tr in self.trackers.values()):
            self._chain_frames.clear()
        elif not self._chain_frames or t - self._chain_frames[-1][0] >= CHAIN_EVIDENCE_INTERVAL_SECONDS:
            self._chain_frames.append((t, frame))

    def _save_chain_evidence(self, t: float, who: str, frame: np.ndarray) -> None:
        """再現した連鎖を待ち切って捨てた時、連鎖の間の画面を保存してログに残す。"""
        if self.debug_log_path is None or self._chain_evidence_saved >= CHAIN_EVIDENCE_LIMIT:
            return
        self._chain_evidence_saved += 1
        names = []
        for ft, f in [*self._chain_frames, (t, frame)]:
            path = self.history_dir / f"chain_gave_up_{who}_{t:07.2f}s_{ft:07.2f}.png"
            if self._chain_saver.save(path, f, [cv2.IMWRITE_PNG_COMPRESSION, 1]):
                names.append(path.name)
        self._log(f"[{t:7.2f}s] 再現を捨てた場面の画面を保存: {', '.join(names)}")

    def _loop(self, capture: PrefetchCapture) -> None:
        region = self.calibration.capture_region()
        origin = (region.left, region.top)
        players = {"self": self.calibration.self_player, "enemy": self.calibration.enemy_player}
        # ログの先頭に動作モードを残す
        self._log(
            f"===== 支援モード開始 {datetime.now().isoformat(timespec='seconds')} "
            f"モード={'一人用(相手を見ない)' if self.solo else '対戦'} "
            f"先読み={self.enable_precompute} "
            f"{self._header_extra().strip()}{' ' if self._header_extra() else ''}配ぷよ読み={self.haipuyo} 相手のツモを覗く={self.peek} エンジン={getattr(getattr(self, 'client', None), 'exe_path', '?')} 起動引数={getattr(getattr(self, 'client', None), 'args', '?')[1:]} 優先度を下げる={getattr(getattr(self, 'client', None), 'low_priority', '?')} ====="
        )
        if self.calibration is not None:
            # 盤面の位置(画面の物理px)。ガイドのドットや読み取りがずれた時に、開始時の位置合わせを確かめるため
            def box(player) -> str:  # noqa: ANN001
                (x0, y0), _, (x1, y1), _ = player.board_quad
                return f"x={x0:.0f} y={y0:.0f} 幅={x1 - x0:.0f} 高さ={y1 - y0:.0f}"

            self._log(f"盤面の位置: 自分 {box(self.calibration.self_player)} / 相手 {box(self.calibration.enemy_player)}")
        self._log("点数表示の読み取り: " + (f"有効(見本 {len(self._score_reader.names)}個、"
                                       f"{'自分と相手の盤面' if self.enemy_score else '自分の盤面'})" if self._score_reader is not None
                                       else f"見本(config/{SCORE_DIGITS_NAME})が無いので読みません"))
        t0 = time.perf_counter()
        self._t0_perf = t0
        stats = TickStats()
        cpu_meter, fps_meter = CpuMeter(), FpsMeter()
        perf_at = 0.0
        while self._running:
            tick_start = time.perf_counter()
            frame_bgr = capture.grab_bgr(region)
            grab_end = time.perf_counter()
            t = time.perf_counter() - t0
            self._now = t
            self._recent_frames.append((t, frame_bgr))
            self._remember_chain_frame(t, frame_bgr)

            snaps: dict[str, Snapshot] = {}
            banner_all_clear: list[str] = []
            for who, pc in players.items():
                board = read_board(frame_bgr, pc.geometry(), origin, banner_detector=self._banner_detectors[who])
                # 「全消し」の文字の有無が変わったらログに残す(文字の画素は read_board が分類から除いている)
                if (board.banner_score > 0) != self._banner_seen[who]:
                    self._banner_seen[who] = board.banner_score > 0
                    self._log(f"[{t:7.2f}s] {who:5s} 全消しの文字を{'検出(一致度 %.2f)' % board.banner_score if self._banner_seen[who] else '見なくなった'}")
                if board.banner_score >= ALL_CLEAR_BANNER_MIN_SCORE:
                    banner_all_clear.append(who)
                n1 = read_next(frame_bgr, pc.next_rects[0], origin)
                n2 = read_next(frame_bgr, pc.next_rects[1], origin)
                snaps[who] = Snapshot(t=t, cells=board.cells, next1=n1, next2=n2, pending_garbage=board.pending_garbage, eyeless_cells=board.eyeless_cells)
            self._check_new_game(t, snaps)
            self._frame_t = t
            self.trackers["self"].target_point = self._target_point()
            # 自分・相手の両方を更新し終えてから思考する(相手の更新前に思考すると
            # 同じフレームで起きた相手の発火・予告変化を見落とす)
            think = False
            # 再計算の理由(入力が変わった時だけ)。前の tick で実行できなかった理由があれば引き継ぐ
            rethink = self._deferred_rethink
            for who, snap in snaps.items():
                tr = self.trackers[who]
                if who in banner_all_clear:
                    self._all_clear_from_banner(t, who)
                for ev in tr.update(snap):
                    self._log(f"[{t:7.2f}s] {who:5s} {ev.kind:12s} {ev.detail}")
                    if ev.kind == "spawn_wait" and ev.detail.split(" ", 1)[0] in ("#1", "#10", "#20"):
                        # 診断: 着地後に次の spawn を検出できない時の画面を残す
                        no = ev.detail.split(" ", 1)[0][1:]
                        self._saver.save(self.history_dir / f"spawn_wait_{who}_{t:07.2f}s_{no}.png", frame_bgr)
                    if who == "self" and ev.kind == "initial_reset":
                        self._on_initial_reset()
                    self._on_tracker_event(t, who, ev, tr)
                    if who == "self" and ev.kind == "spawn":
                        if self._first_spawn_t is None:
                            self._first_spawn_t = t
                        self._turn_clock.spawn(t)
                        self._self_spawn_t = t
                        self._on_self_spawn(tr)
                    if who == "self" and ev.kind == "turn_ready":
                        think = True
                    if who == "self" and ev.kind == "chain_start":
                        self._on_self_chain_start()
                    if who == "self" and ev.kind == "chain_step":
                        self._on_self_chain_step(tr)
                        if "点滅から" in ev.detail:
                            self._save_flash_evidence(t)
                    if who == "self" and ev.kind == "chain_end":
                        self._on_self_chain_end(tr)
                    if ev.kind == "all_clear_unconfirmed":
                        self._on_game_over(t, who, tr)
                    if ev.kind == "chain_expected_gave_up":
                        self._save_chain_evidence(t, who, frame_bgr)
                    if not self.solo and who == "enemy":
                        self._record_attack(t, tr, ev.kind)
                    if who == "enemy" and ev.kind == "spawn" and self._scores["enemy"].settled(t, 0.0):
                        self._close_chain_score("enemy", "次の組ぷよが出た")
                    if not self.solo and who == "enemy" and ev.kind == "spawn" and "initial" in ev.detail:
                        # 相手(CPU)の初手 = 対戦が始まった。自分の初手を動くのを待たずに確定できるようにする
                        me_tr = self.trackers["self"]
                        # 両者のツモは同じなので、相手の初手(と枠)は自分の初手の候補になる(自分の開始の判定に使う)
                        me_tr.start_hint = (tr.current_pair, tr.next1, tr.next2)
                        if me_tr.current_pair is None and not me_tr.start_confirmed:
                            me_tr.start_confirmed = True
                            self._log(f"[{t:7.2f}s] 相手の初手を検出: 自分の初手を動くのを待たずに確定します")
                    # 相手の仕掛けは自分の手番の途中でも提案を更新する(思考は手番開始時にしか走らない
                    # ため、操作中の仕掛けに次の手番まで対応できなかった)。再計算するのは入力が変わる
                    # 「相手の消去開始(点滅から復元した盤面で着弾量が確定する)」「連鎖終了」「予告が1段以上増加」の時
                    if not self.solo and who == "enemy" and ev.kind in ("chain_start", "chain_end"):
                        self._on_enemy_chain(tr, ev.kind)
                    if not self.solo and who == "enemy" and ev.kind == "chain_start":
                        rethink = "相手の消去開始を検出"
                        self._precomputed = None  # 相手の状態が変わったので古い先読みは使わない
                    if not self.solo and who == "enemy" and ev.kind == "chain_end":
                        rethink = "相手の連鎖が終了"
                    if not self.solo and who == "enemy" and ev.kind in ("chain_start", "chain_end"):
                        self._refresh_precompute_for_fixed()
                    if who == "self" and ev.kind == "pending":
                        self._release_carry_if_pending_cleared(t, tr)
                    if who == "self" and ev.kind == "pending" and tr.pending_garbage - self._pending_at_think >= RETHINK_PENDING_INCREASE:
                        rethink = "予告おじゃまが増加"
                # 診断: 手番が進んでいるのに取り込めない状態が続いている場合、原因の手がかりと
                # その瞬間の画像を一度だけ記録する
                stuck_for = t - max(tr.last_take_in_t, self._game_start_t)
                if tr.settling and not tr.chain_active and tr.turn > 0 and stuck_for > STUCK_WARN_SECONDS and (who, tr.last_take_in_t) not in self._stuck_logged:
                    self._stuck_logged.add((who, tr.last_take_in_t))
                    path = self.history_dir / f"stuck_{who}_{int(t):04d}s.png"
                    self._saver.save(path, frame_bgr)
                    self._log(
                        f"[{t:7.2f}s] {who:5s} 取り込めない状態が{stuck_for:.1f}秒続いています "
                        f"unknown={tr.last_unknown} covered={is_covered(snap.cells, snap.eyeless_cells)} poppable={tr.last_poppable} chain_active={tr.chain_active} "
                        f"pending={tr.pending_garbage} 画像={path.name}\n"
                        f"           生の認識結果:\n{_indent(tr.last_raw_text)}\n"
                        f"           確定盤面:\n{_indent(cells_to_text(tr.settled))}"
                    )
            me = self.trackers["self"]
            self._update_score(t, frame_bgr, players, origin)
            # 着弾量の保持は「予告が見えている間」だけ。相手の連鎖が終わって予告も無い状態が続いたら
            # (自分の攻撃で相殺し切った等)解除する
            en_tr = self.trackers["enemy"]
            if self._incoming_carry > 0 and not en_tr.chain_active and me.pending_garbage == 0:
                if self._carry_zero_since is None:
                    self._carry_zero_since = t
                elif t - self._carry_zero_since >= INCOMING_CARRY_CLEAR_SECONDS:
                    self._log(f"[{t:7.2f}s] 相手の連鎖が終わり予告も無いので着弾量 {self._incoming_carry}個 を解除")
                    self._incoming_carry = 0
                    self._carry_zero_since = None
            else:
                self._carry_zero_since = None
            self._deferred_rethink = rethink
            self._before_results(t, frame_bgr, me)
            self._poll_think_results()
            self._after_results(me)
            self._opening_preview(t, me)
            ready = not me.settling and not me._await_spawn_after_chain and me.current_pair is not None
            retry = self._think_pending and me.next1 is not None and (me.next2 is not None or self._last_data is None)
            if ready and (think or retry or rethink):
                if retry and not think:
                    self._log(f"[{t:7.2f}s] 認識が揃ったので同じ手番で思考をやり直します")
                if rethink and not think:
                    self._log(f"[{t:7.2f}s] {rethink}: 操作中の組ぷよの提案を再計算")
                # 同じ tick の turn_ready/認識回復と重なっても再計算の理由を失わない。
                # 思考できなかった(入力不足)なら理由を次の tick へ持ち越す
                # 新しい手番(turn_ready)では前の手番の表示を「維持」してはいけないので new_turn を渡す
                if self._think(t, frame_bgr, note=f"{rethink}: 提案を更新" if rethink else "", new_turn=think):
                    self._deferred_rethink = ""

            elapsed = time.perf_counter() - tick_start
            if elapsed >= OBSERVATION_GAP_WARN_SEC:
                self._log(f"[{t:7.2f}s] 観測の空白 {elapsed * 1000:.0f}ms (この間の画面変化は見えていない)")
            report = stats.add(t, tick_start, grab_end - tick_start, elapsed)
            if report:
                self._log(f"[{t:7.2f}s] {report}")
            fps_meter.tick(t)
            if t - perf_at >= PERF_INTERVAL_SEC:
                perf_at = t
                cpu = cpu_meter.sample()
                # CPU 使用率をログにも残す: 1分ごとの統計と置きミスの診断に出す
                self.last_cpu = cpu
                if cpu is not None:
                    stats.cpu.append(cpu)
                # CPU 使用率・観測の FPS は上のバー(テトリスと同じ表示)に出す
                self.fps_ready.emit(fps_meter.fps())
            if elapsed < TICK_INTERVAL_SEC:
                time.sleep(TICK_INTERVAL_SEC - elapsed)

    def _all_clear_from_banner(self, t: float, who: str) -> None:
        """「全消し」の文字がはっきり見えたら、そのプレイヤーは全消しを持っているとする。連鎖の終わりの盤面を読み違えて(1個残った等)
        全消しを記録できず、双方が全消しの時の構えが働かなかった"""
        tr = self.trackers[who]
        if not tr.all_clear:
            tr.all_clear = True
            self._log(f"[{t:7.2f}s] {who:5s} 全消しの文字から全消しを持っているとします")

    def _opening_preview(self, t: float, me: PlayerTracker) -> None:
        """開始前(操作中の組ぷよが無く、盤面が空、ネクスト枠に第1手・第2手が見えている)に初手を先に計算して
        表示する。
        第3手は見えないので2組で solo し、第2手は開始後の通常思考に任せる。"""
        if me.current_pair is not None or me.turn != 0 or me.next1 is None or me.next2 is None or count_puyos(me.settled) != 0:
            return
        # 開始の瞬間にすぐ送れるよう、2通りの解釈の初手を思考スレッドで先に計算しておく:
        #  - 枠がまだ入れ替わっていない: 枠の第1・2手(第3手は未読)
        #  - 直前の入れ替わりが開始だった: 枠から消えた組(tracker._prestart_first)と枠の第1・2手
        # 開始の直前は枠に第1・2手が出ているので、開始の入れ替わりの後も照合キー(出現した組ぷよ, ネクスト)は同じになる
        seqs: list[tuple[Pair, ...]] = [(me.next1, me.next2)]
        if me._prestart_first is not None:
            seqs.append((me._prestart_first, me.next1, me.next2))
        for seq in seqs:
            if seq in self._opening_moves or seq in self._opening_requested:
                continue
            self._opening_requested.add(seq)
            trial = self.mapper.fork()
            try:
                pairs = [trial.pair(*p) for p in seq]
            except ValueError as exc:
                self._log(f"[{t:7.2f}s] 開始前の初手計算をスキップ: {exc}")
                continue
            kind = "open3" if len(seq) > 2 else "open2"
            self._dispatch(kind, {"t": t, "seq": seq},
                           lambda pairs=pairs: self.client.solo(PuyoField(), pairs, history=[]))

    def _apply_opening(self, ctx: dict, move: BattleMove | None, exc: BaseException | None) -> None:
        """開始前の初手の計算結果を残す。開始の後に届き、出現した組ぷよと合えば、その場で送る。"""
        t, seq = ctx["t"], ctx["seq"]
        if exc is not None:
            if isinstance(exc, (ValueError, RuntimeError, NoValidPlacementError)):
                self._log(f"[{t:7.2f}s] 開始前の初手計算をスキップ: {exc}")
                return
            raise exc
        assert move is not None
        self._opening_moves[seq] = move
        self._log(f"[{t:7.2f}s] 開始前の初手: 第1手={seq[0]} 第2手={seq[1]} 第3手={seq[2] if len(seq) > 2 else None} → x={move.x} {move.rotation} form={move.form} ({move.think_ms}ms)")
        me = self.trackers["self"]
        if me.current_pair is not None:
            if me.turn == 0 and (seq[0], seq[1]) == (me.current_pair, me.next1) and self._select_opening(me):
                self._log("           開始前の初手が開始の後に届いた: 先読みとして使います")
                self._precomputed_ready(me)
            return
        # 表示は今の枠の解釈に合うもの(入れ替わりの後なら3組の方)
        preferred = (me._prestart_first, me.next1, me.next2) if me._prestart_first is not None else (me.next1, me.next2)
        if seq != preferred:
            return
        child_color, axis_color = seq[0]
        data = OverlayDrawData(
            axis_cell=move.axis_cell, child_cell=move.child_cell, axis_color=axis_color, child_color=child_color,
            # 第3手が見えるまで(開始の約0.3秒前に枠が入れ替わるまで)は2組だけの計算なので、開始時に変わりうる
            # 「仮」の提案と明示する
            label=move.label, self_analysis=move.self_analysis,
            note="開始前: 第1手の提案(第1〜3手から)" if len(seq) > 2 else "開始前: 仮の提案(第3手が見えたら変わります)",
            form=self._form_text(move),
        )
        self._last_data = data
        self.draw_data_ready.emit(data)

    def _select_opening(self, me: PlayerTracker) -> bool:
        """試合開始の出現で、計算済みの初手から出現した組ぷよ・ネクストに合うものを先読みとして置く(3組で計算した方を優先)。"""
        best = None
        for seq, move in self._opening_moves.items():
            if (seq[0], seq[1]) != (me.current_pair, me.next1):
                continue
            if best is None or (len(seq) > len(best[0]) and (me.next2 is None or seq[2] == me.next2)):
                best = (seq, move)
        if best is None:
            return False
        empty = [[EMPTY] * COLS for _ in range(ROWS)]
        self._precomputed = (empty, (me.current_pair, me.next1), best[1])
        self._precomputed_attack = None
        return True

    def _attack_involved(self, me: PlayerTracker) -> bool:
        """相手の攻撃が絡む局面か(自分への予告・着弾見込み・相手の連鎖中・相手への予告)。"""
        en = self.trackers["enemy"]
        return attack_involved(me.pending_garbage, self._incoming_carry, self.solo, en.chain_active, en.pending_garbage,
                               getattr(self.client, "engine", "ama"))

    def _early_think_valid(self, ctx: dict, me: PlayerTracker) -> bool:
        """取り込みを待たずに考えた結果(_think_early_in_danger)が、まだ使えるか: 手番・組ぷよが同じで、取り込みがまだか、
        取り込んだ盤面が考えた盤面と同じ。"""
        board = ctx.get("early_board")
        if board is None or ctx["key"][:4] != self._think_key(me)[:4]:
            return False
        return me.settling or cells_to_text(me.settled) == board

    def _on_self_spawn(self, me: PlayerTracker) -> None:
        if self._scores["self"].settled(self._now or 0.0, 0.0):
            self._close_chain_score("self", "次の組ぷよが出た")
        self._on_self_spawn_display(me)

    def _on_self_spawn_display(self, me: PlayerTracker) -> None:
        """手番が切り替わった: 前の手番のドット(置いたツモの色)を出したままにしない。2手先の提案があればそれを新しい組ぷよの提案として
        先に出し(色はネクストから読んだ新しい組ぷよ)、無ければドットを消してパネルだけ残す。
        turn_ready で思考が終われば正式な提案で上書きされる。"""
        if me.turn == 0 and count_puyos(me.settled) == 0:
            self._select_opening(me)  # 試合開始: 計算済みの初手から、出現した組ぷよに合うものを使う
        self._precomputed_ready(me)
        data = self._last_data
        if data is None:
            return
        if (
            data.next_axis_cell is not None and data.next_child_cell is not None
            and me.current_pair is not None and (data.next_child_color, data.next_axis_color) == me.current_pair
        ):
            promoted = replace(
                data,
                axis_cell=data.next_axis_cell, child_cell=data.next_child_cell,
                axis_color=data.next_axis_color, child_color=data.next_child_color,
                next_axis_cell=None, next_child_cell=None, next_axis_color="", next_child_color="",
                note="先読み(確認中)", fire_chain=(0, 0), waiting=False,  # 連鎖中に隠していたら再表示
                chaining=me.chain_active,
            )
        else:
            promoted = replace(data, waiting=True, note="次の手を計算中", next_axis_cell=None, next_child_cell=None, chaining=me.chain_active)
        self._last_data = promoted
        self.draw_data_ready.emit(promoted)

    def _on_self_chain_start(self) -> None:
        """自分の発火を検出した: 置き終わった組ぷよのドットを連鎖の間(約8秒)出したままにしない
。2手先の提案は残しておき、次の組ぷよが出た spawn で
        _on_self_spawn が色の一致を見て差し替える。"""
        me = self.trackers["self"]
        pending = self._scores["self"].chain
        resumed = pending is not None and pending.ended_at is not None  # 終わりを見た連鎖の続き(次の組ぷよがまだ出ていない)
        self._open_chain_score("自分の発火を検出")
        exp = me.chain_expected
        if resumed and self._self_fire is not None and self._self_fire[0] > 0:
            # 追跡が連鎖を途中で区切った: 続きの段だけの再現(1連鎖 40点など)に戻さず、連鎖全体の値を連鎖中として出し続ける
            self._self_fire = (self._self_fire[0], self._self_fire[1], True)
        else:
            # 撃っている連鎖(点滅から再現した連鎖数・点数)を自分の欄に出す。再現できなければ段数を観測で数えて出す(_on_self_chain_step)
            self._self_fire = (exp.steps, exp.score, True) if exp is not None else (0, 0, True)
            self._self_fire_first = self._self_fire  # 連鎖の始めに出した値(終わりに訂正したかを比べる)
        data = self._last_data
        if data is None:
            return
        if data.waiting:
            shown = replace(data, chaining=True, self_firing=self._self_fire)
        else:
            shown = replace(data, waiting=True, note="連鎖中: 次の手を待機", chaining=True, self_firing=self._self_fire)
        self._last_data = shown
        self.draw_data_ready.emit(shown)

    # ---- ゲームの点数表示 ----

    def _open_chain_score(self, why: str, who: str = "self") -> None:
        """who の盤面の連鎖の点数を数え始める(始まる前の合計が分かっている時だけ)。前に数えていた連鎖は確定する。"""
        if self._score_reader is None:
            return
        prev = self._scores[who].open(why)
        if prev is not None:
            self._finish_chain_score(who, prev, "次の連鎖が始まった")

    def _score_sides(self) -> tuple[str, ...]:
        return ("self", "enemy") if self.enemy_score else ("self",)

    def _update_score(self, t: float, frame_bgr: np.ndarray, players: dict, origin: tuple[int, int]) -> None:
        """盤面の下の点数表示を読み、連鎖の点数(始まる前と後の合計の差)を数える。自分の盤面と、片側だけを支援している時は
        相手の盤面も読む(相手の点数はパネルの相手の欄に出す)。"""
        if self._score_reader is None:
            return
        self._score_tick += 1
        sides = self._score_sides()
        for who in sides:
            if self._scores[who].settled(t, CHAIN_SCORE_SETTLE_SEC):
                self._close_chain_score(who, "連鎖の終わりから時間がたった")
        if self._score_tick % SCORE_READ_EVERY:
            return
        for who in sides:
            player = players.get(who)
            if player is None:
                continue
            reading = self._score_reader.read(frame_bgr, player.geometry(), origin)
            if reading is None:
                continue
            event, cs, prev = self._scores[who].feed(reading)
            if prev is not None:
                self._finish_chain_score(who, prev, "次の連鎖が始まった")
            label = "点数表示" if who == "self" else "相手の点数表示"
            if event == "step" and cs is not None and reading.step is not None:
                a, b = reading.step
                self._log(f"[{t:7.2f}s] {label}: {cs.steps}段目 {a}×{b}")
            elif event == "total" and cs is not None:
                self._log(f"[{t:7.2f}s] {label}: 合計 {reading.total:,}(連鎖の点数 ここまで {cs.actual:,}点・{cs.steps}段)")
                self._show_chain_score(who, cs, final=False)

    def _show_chain_score(self, who: str, cs: ChainCount, final: bool) -> None:
        """数えた連鎖の点数をパネルの who の欄に出す。連鎖中に再現を打ち切っていれば(点数が分からない)ここまでの点数を出す。
        終わったら(final)ゲームの点数表示の値に置き換え、FIRE_RESULT_HOLD_SEC の間出しておく。"""
        fire = self._self_fire if who == "self" else self._enemy_fire
        if fire is None:
            if not final:
                return
            fire = (cs.steps, 0, False)  # 段を観測で数えられなかった連鎖も、点数表示で確かめた点数は出す
        actual = cs.actual
        if final:
            # 段数は、観測・点数表示で数えた段と、連鎖の終わりを最初に見た時の再現の多い方
            fire = (max(fire[0], cs.steps, cs.replay[0] if cs.replay else 0), actual, False)
            end = time.perf_counter() + FIRE_RESULT_HOLD_SEC
            if who == "self":
                self._self_fire_end = end
            else:
                self._enemy_fire_end = end
        elif fire[2] and (fire[1] == 0 or cs.live) and actual > 0:
            # 再現が無い・打ち切った連鎖: 点数表示で数えた「ここまで」の点数を出し、以後も点数表示で更新する
            cs.live = True
            fire = (max(fire[0], cs.steps), actual, True)
        else:
            return
        if who == "self":
            self._self_fire = fire
        else:
            self._enemy_fire = fire
        data = self._last_data
        if data is not None:
            self._last_data = replace(data, self_firing=fire) if who == "self" else replace(data, enemy_firing=fire)
            self.draw_data_ready.emit(self._last_data)

    def _close_chain_score(self, who: str, why: str) -> None:
        """who の盤面で数えていた連鎖の点数を確定してログとパネルに出す。"""
        cs = self._scores[who].take()
        if cs is not None:
            self._finish_chain_score(who, cs, why)

    def _finish_chain_score(self, who: str, cs: ChainCount, why: str) -> None:
        actual = cs.actual
        if actual <= 0:
            return
        tr = self.trackers[who]
        # 再現は連鎖の終わりを最初に見た時の値(確定する時には追跡が次の連鎖に移っていることがある)
        steps, score = cs.replay if cs.replay is not None else (tr.chain_steps, tr.chain_score)
        replay = f"{steps}連鎖 {score:,}点" if score > 0 else f"{steps}連鎖(点数は再現できず)"
        label = "連鎖の点数" if who == "self" else "相手の連鎖の点数"
        self._log(f"           {label}(ゲームの点数表示): {cs.steps}段 {actual:,}点 / 再現 {replay}"
                  + (f" 差 {score - actual:+,}点" if score > 0 else "") + f"(確定: {why})")
        self._show_chain_score(who, cs, final=True)

    def _on_self_chain_step(self, me: PlayerTracker) -> None:
        """自分の連鎖の段が消えた: 再現が外れていたら(再現を打ち切った・観測の段数が再現より多い)、出している連鎖を訂正する。"""
        fire = self._self_fire
        if fire is None or not fire[2]:
            return
        exp = me.chain_expected
        if exp is not None and me.chain_steps <= exp.steps:
            return  # 再現どおりに進んでいる
        corrected = (me.chain_steps, 0, True)  # 観測で数えた段数(点数は分からない)
        if corrected == fire:
            return
        self._self_fire = corrected
        data = self._last_data
        if data is not None:
            self._last_data = replace(data, self_firing=corrected)
            self.draw_data_ready.emit(self._last_data)

    def _on_self_chain_end(self, me: PlayerTracker) -> None:
        """自分の連鎖が終わった: 放った連鎖数・点数(発火直前盤面から再現できた時)をパネルに出す。"""
        shown_fire = self._self_fire_first
        self._self_fire_first = None
        self._self_fire_end = time.perf_counter()
        self._scores["self"].end(self._now or 0.0, (me.chain_steps, me.chain_score))
        self._self_fire = (me.chain_steps, me.chain_score, False) if me.chain_steps > 0 else None
        counted = self._scores["self"].chain
        if counted is not None and counted.replay is not None and counted.replay[0] > me.chain_steps:
            # 途中で区切られた連鎖の続きが終わった: 続きの段だけでなく、最初に終わりを見た時の連鎖全体の値を出す
            self._self_fire = (counted.replay[0], counted.replay[1], False)
        data = self._last_data
        if data is None:
            return
        # 連鎖中に出していた値と違えば訂正したことをメモに出す(点滅から再現した盤面が外れていた時など)
        note = data.note
        if (shown_fire is not None and shown_fire[0] > 0 and self._self_fire is not None
                and (shown_fire[0], shown_fire[1]) != self._self_fire[:2] and shown_fire[1] > 0):
            after = (f"{me.chain_steps}連鎖 {me.chain_score:,}点" if me.chain_score > 0 else f"{me.chain_steps}連鎖(点数は再現できず)")
            note = f"点数を訂正: {shown_fire[0]}連鎖 {shown_fire[1]:,}点 → {after}"
            self._log(f"           {note}")
        elif note == "連鎖中: 次の手を待機":
            note = ""
        shown = replace(data, note=note, chaining=False, self_firing=self._self_fire)
        self._last_data = shown
        self.draw_data_ready.emit(shown)

    def _on_enemy_chain(self, en: PlayerTracker, kind: str) -> None:
        """相手の連鎖が始まった・終わった: 撃っている連鎖(点滅から再現した連鎖数・点数)をパネルの相手の欄に出す。
        連鎖中に届く思考の相手の解析は撃つ前の盤面のもので、先の手で撃てる最大(撃っている連鎖とは別)を出してしまう
。連鎖が終わった後の思考が届いたら解析に戻す(_emit_move)"""
        if kind == "chain_start":
            exp = en.chain_expected
            self._enemy_fire = (exp.steps, exp.score, True) if exp is not None else (0, 0, True)
            if self.enemy_score:
                self._open_chain_score("相手の発火を検出", "enemy")
        else:
            self._enemy_fire = (en.chain_steps, en.chain_score, False)
            self._enemy_fire_end = time.perf_counter()
            self._scores["enemy"].end(self._now or 0.0, (en.chain_steps, en.chain_score))
        data = self._last_data
        if data is None:
            return
        shown = replace(data, enemy_firing=self._enemy_fire)
        self._last_data = shown
        self.draw_data_ready.emit(shown)

    def _record_attack(self, t: float, en: PlayerTracker, kind: str) -> None:
        """相手の連鎖を攻撃エピソードとして記録する(新方針の第1段階: 提案は変えない)。"""
        me = self.trackers["self"]
        if kind == "chain_start":
            self._attack_count += 1
            self._attack = AttackEpisode(
                attack_id=self._attack_count,
                started_at=t,
                before=[row[:] for row in (en.chain_before or en.settled)],
                bonus=en.chain_bonus,
                my_pending_at_start=me.pending_garbage,
            )
            self._attack.add_estimate(t, "前回解析の相手本線", self._last_enemy_main_garbage())
            self._log(
                f"[{t:7.2f}s] 攻撃#{self._attack_count} 開始: 発火直前の相手盤面(由来={en.chain_before_source})\n"
                f"{_indent(cells_to_text(self._attack.before))}"
            )
        elif self._attack is not None and kind == "chain_step":
            self._attack.steps = en.chain_steps
            self._attack.dropped = en.chain_dropped
        elif self._attack is not None and kind == "chain_end":
            ep = self._attack
            ep.ended_at = t
            ep.steps = max(ep.steps, en.chain_steps)
            ep.dropped = max(ep.dropped, en.chain_dropped)
            ep.my_pending_at_end = me.pending_garbage
            if self._incoming_carry > 0:
                self._log(f"[{t:7.2f}s] 自分への着弾見込み {self._incoming_carry}個 を予告が消えるまで保持")
            avg = self._turn_clock.average
            clock = "平均手時間 未計測" if avg is None else f"平均手時間 {avg:.2f}s(n={self._turn_clock.samples}) → この連鎖の間に約{self._turn_clock.moves_within(ep.duration or 0.0)}手"
            self._log(f"[{t:7.2f}s] {ep.summary()} / {clock}")
            self._attack = None

    def _build_states(self, me: PlayerTracker, en: PlayerTracker, my_settled: list[list[str]], my_pairs_src: list[tuple[str, str]],
                      ahead: bool = False) -> tuple[PlayerState, PlayerState | None, list[str], list[str]]:
        """思考エンジンに渡す自分・相手の状態を作る。

        自分側の変換失敗(5色目など)は ValueError のまま呼び出し側に伝える。
        相手側の変換失敗は自分の提案を止めない: 相手状態を None にして相手を見ない思考に
        切り替える。
        """
        # 状態の生成は録画検証(tools/replay_video.py)と共通(src/engine/think_input.py)。
        # 色の割当は変換を試してから採用する(途中で失敗しても仮の登録を残さない)
        inp = build_think_input(self.mapper, me, en, my_settled, my_pairs_src, solo=self.solo, incoming_hint=self._incoming_carry,
                                now=self._now, haipuyo=self.haipuyo or isinstance(self.client, BookClient), ahead=ahead, peek=self.peek)
        self.mapper = inp.mapper
        if inp.enemy_error:
            self._log(f"           相手の認識エラー: {inp.enemy_error} → 相手を仮の状態にして思考します")
        return inp.my_state, inp.en_state, inp.my_pairs, inp.en_pairs

    def _last_enemy_main_garbage(self) -> int:
        """直近の提案時に解析した相手の本線のおじゃま個数(解析が無ければ 0)。"""
        data = self._last_data
        if data is None or data.enemy_analysis is None:
            return 0
        return data.enemy_analysis.max.garbage

    def _emit_move(self, me: PlayerTracker, move: BattleMove, next_move: BattleMove | None = None, note: str = "") -> None:
        assert me.current_pair is not None
        child_color, axis_color = me.current_pair
        data = OverlayDrawData(
            axis_cell=move.axis_cell,
            child_cell=move.child_cell,
            axis_color=axis_color,
            child_color=child_color,
            label=move.label,
            action=action_text(move, move.incoming or self._incoming_carry or me.pending_garbage),
            fire_chain=move.chain_after,
            self_analysis=move.self_analysis,
            enemy_analysis=move.enemy_analysis,
            note=note or (book_text(move) if is_book_move(move) else FIRE_REASON_TEXT_JA.get(move.fire_reason or "", "")),
            form=self._form_text(move),
            chaining=me.chain_active,
        )
        # 相手の連鎖中は撃っている連鎖を出したままにする。連鎖が終わった後に依頼した思考なら解析(撃てる最大)に戻す
        # (連鎖中に依頼した思考の相手の解析は撃つ前の盤面のもの)
        if (self._enemy_fire is not None and not self._enemy_fire[2] and not self.trackers["enemy"].chain_active
                and self._think_submitted >= self._enemy_fire_end):
            self._enemy_fire = None
        data.enemy_firing = self._enemy_fire
        # 自分も同じ: 連鎖中は撃っている連鎖、終わった後は、その後に依頼した思考が届くまで撃った連鎖を出す
        if (self._self_fire is not None and not self._self_fire[2] and not me.chain_active
                and self._think_submitted >= self._self_fire_end):
            self._self_fire = None
        data.self_firing = self._self_fire
        # 最終的な構想: 配ぷよ読みで、提案を固定する拡張が動いている時だけ、撃つ直前の盤面をドットで出す
        if self.haipuyo and self._fixes_moves() and move.plan_field is not None:
            data.plan_dots = plan_dots(self.mapper.rows_to_cells(move.plan_field), me.settled, move.plan_trigger)
            c, s, k = move.plan_chain
            data.plan_text = f"構想: {c}連鎖 {s:,}点 @{k}"
        self._last_data = data
        self._on_move(me, move)
        if next_move is not None and me.next1 is not None:
            data.next_axis_cell = next_move.axis_cell
            data.next_child_cell = next_move.child_cell
            data.next_child_color, data.next_axis_color = me.next1
        self.draw_data_ready.emit(data)

    def _self_crop(self, frame_bgr: np.ndarray) -> np.ndarray:
        """自分の盤面とネクスト枠の範囲を切り出した複製(フレーム全体は両方の盤面を含み大きいので、記録用に小さくする)。"""
        if self.calibration is None:
            return frame_bgr
        region = self.calibration.capture_region()
        pc = self.calibration.self_player
        boxes = [pc.geometry().bounding_box(), *pc.next_rects]
        x0 = max(0, min(b[0] for b in boxes) - region.left)
        y0 = max(0, min(b[1] for b in boxes) - region.top)
        x1 = max(b[0] + b[2] for b in boxes) - region.left
        y1 = max(b[1] + b[3] for b in boxes) - region.top
        return frame_bgr[y0:y1, x0:x1].copy()

    def _form_text(self, move: BattleMove) -> str:
        """パネルに出す「狙い: 弥生時代」「移行: 弥生時代 → フキゲンGTR」の文字列。"""
        if move.form is None:
            return "狙い: (定型なし)" if self._last_form is None else f"定型を離脱: {FORM_TEXT_JA.get(self._last_form, self._last_form)}"
        name = FORM_TEXT_JA.get(move.form, move.form)
        if self._last_form is not None and self._last_form != move.form:
            return f"移行: {FORM_TEXT_JA.get(self._last_form, self._last_form)} → {name}"
        return f"狙い: {name}"

    def _battle(self, my_state: PlayerState, en_state: PlayerState | None) -> BattleMove:
        """対戦モードなら battle で思考する。一人用モード、または相手の状態が作れなかった
        (en_state=None)ときは build(相手を考慮しない)で思考する。"""
        if self.solo:
            return self.client.solo(my_state.field, my_state.pairs, history=my_state.history)
        # 相手の認識に失敗した時は空の相手を仮に渡す(記録用に enemy_unknown を添える)
        enemy_unknown = en_state is None
        if en_state is None:
            en_state = PlayerState(PuyoField(), list(my_state.pairs))
        return self.client.battle(
            my_state,
            en_state,
            enemy_unknown=enemy_unknown,
            target_point=self._target_point(),
        )

    def _target_point(self) -> int:
        """今のおじゃま1個に必要な点(マージンタイム)。自分の最初の組ぷよが出る前は 70点。"""
        if self._first_spawn_t is None:
            return margin_target_point(0)
        return margin_target_point(self._frame_t - self._first_spawn_t)

    def _dispatch(self, kind: str, ctx: dict, fn, urgent: bool = False) -> None:
        """思考を依頼する。思考スレッドがあれば渡して観測を続け、無ければ(テスト等)その場で実行して結果を反映する。"""
        if self._think_worker is None:
            try:
                result, exc = fn(), None
            except Exception as e:  # noqa: BLE001 結果の反映側で種類ごとに扱う
                result, exc = None, e
            self._handle_think_result(kind, ctx, result, exc)
        else:
            self._think_worker.submit(kind, ctx, fn, urgent=urgent)

    def _poll_think_results(self) -> None:
        """思考スレッドの結果を観測ループで反映する(毎 tick)。"""
        if self._think_worker is None:
            return
        for kind, ctx, result, exc in self._think_worker.poll():
            self._handle_think_result(kind, ctx, result, exc)

    def _handle_think_result(self, kind: str, ctx: dict, result, exc: BaseException | None) -> None:
        if kind == "main":
            self._apply_think(ctx, result, exc)
        elif kind == "pre":
            self._apply_precompute(ctx, result, exc)
        elif kind == "pre_atk":
            self._apply_precompute_alt(ctx, result, exc)
        elif kind in ("open2", "open3"):
            self._apply_opening(ctx, result, exc)

    def _think_key(self, me: PlayerTracker) -> tuple:
        """思考結果がまだ有効かを確かめる鍵(手番・操作中の組ぷよ・ネクスト・確定盤面)。"""
        return (me.turn, me.current_pair, me.next1, me.next2, cells_to_text(me.settled))

    def _think(self, t: float, frame_bgr: np.ndarray, note: str = "", new_turn: bool = False) -> bool:
        """現在の組ぷよの提案を計算して表示する。note は再計算の理由(相手の仕掛け等)。
        new_turn=True は手番の開始(turn_ready)で、前の手番の表示を維持する分岐を使わない。
        思考を依頼できた(または提案を出した)ら True、入力不足で思考できなかったら False。

        入力が揃っていない(ネクスト未認識、自分側の色変換失敗)時は _think_pending を立てて戻り、
        同じ手番で認識が揃ったら _loop がやり直す。
        ama の呼び出しは思考スレッドで行い、結果は _apply_think で反映する。
        """
        me, en = self.trackers["self"], self.trackers["enemy"]
        self._think_pending = True
        if me.current_pair is None or me.next1 is None:
            self.status_changed.emit("ネクスト認識待ち")
            return False
        if new_turn:
            self._on_take_in(me)
        # ネクネクが読めていない(演出でネクスト枠が隠れ、盤面から設置を検出した直後)なら
        # 2組で思考する(ama は2組以上あれば探索できる)。ネクネクが読めたら _think_pending でやり直す
        my_pairs_src = [me.current_pair, me.next1] + ([me.next2] if me.next2 is not None else [])

        try:
            my_state, en_state, my_pairs, en_pairs = self._build_states(me, en, me.settled, my_pairs_src)
        except ValueError as exc:
            self._log(f"           思考スキップ: {exc}")
            self.status_changed.emit(f"認識エラー: {exc}")
            if "5色目" in str(exc) and count_puyos(me.settled) <= NEW_GAME_MIN_PUYOS:
                # 新しい試合の検出を取りこぼした可能性が高い。色の割当だけ作り直して次の手番から復帰する
                self._log("           盤面が小さいので色の割当を作り直します(新しい試合の検出漏れ)")
                self.mapper = ColorMapper()
            elif "5色目" in str(exc):
                self._log_fifth_color(t, me, en, my_pairs_src, frame_bgr)
            return False

        # 先読みが当たっていれば、事前計算の提案(前の手番で「2」として見せていた手)を即表示する。
        # ただし事前計算は相手の状態が古い。相手の潰し・予告おじゃまなど「対応」が必要な局面
        # (最新の相手状態で計算した手が構築以外)では、一貫性より対応を優先して上書きする
        pre = self._precomputed
        self._precomputed = None
        if self._think_worker is not None and self._think_worker.cancel("pre"):
            self._pre_inflight = None  # 実行前の先読みを取り消した(実行中のものは結果が届く)
        if new_turn:
            # 相手の発火を想定した先読みは出現時点で使い終わっている(再計算では残す: 相手の消去開始の再計算の後の出現で使う)
            self._precomputed_alt = None
            if self._think_worker is not None:
                self._think_worker.cancel("pre_atk")
        # 再計算(note あり)では古い先読みを一度表示する経路を使わない(相手の状態が変わっている)
        # 提案を固定する拡張が動いている時は、即表示した先読みの手がそのまま固定されるので、相手の攻撃が絡む局面では使わない
        instant_ok = not (self._fixes_moves() and self._attack_involved(me))
        # 同じく、小さい消去の先読みも即表示しない(組ぷよが2組しか見えない先読みは判断が粗いので、3組目まで見た思考を待つ)
        if pre is not None and self._fixes_moves() and pre[2].label == "harass":
            instant_ok = False
        instant = None
        if not note and instant_ok and pre is not None and pre[0] == me.settled and pre[1] == (me.current_pair, me.next1):
            instant = pre[2]
            self._emit_move(me, instant)
            self._log(f"           先読み一致: 事前計算の提案を即表示 x={instant.x} {instant.rotation}")
        ctx = {
            "t": t, "frame": frame_bgr, "note": note, "new_turn": new_turn, "instant": instant,
            "en_state": en_state, "my_pairs": my_pairs, "en_pairs": en_pairs,
            "key": self._think_key(me), "submitted": time.perf_counter(),
        }
        self._think_pending = False  # 結果の反映でやり直しの要否を決め直す
        self._dispatch("main", ctx, lambda: self._battle(my_state, en_state), urgent=True)
        sent = self._committed_move(me) if (note and not new_turn) else None
        if sent is not None:
            # 確定した手の再計算(相手の連鎖の開始・終了など): 今の手は変えられないので、次の手の先読みを再計算より
            # 先に計算する。再計算の後に先読みすると、次の組ぷよの出現に間に合わず思考を待って自由落下した
            self._dispatch_precompute(me, en, sent, urgent=True)
        return True

    def _apply_think(self, ctx: dict, move: BattleMove | None, exc: BaseException | None) -> None:
        """思考スレッドの結果を反映する(観測ループで呼ぶ)。依頼した後に状況が変わっていれば捨てる。"""
        me, en = self.trackers["self"], self.trackers["enemy"]
        t, note, new_turn, instant = ctx["t"], ctx["note"], ctx["new_turn"], ctx["instant"]
        wait_ms = (time.perf_counter() - ctx["submitted"]) * 1000
        early = ctx.get("early_board") is not None
        if (ctx["key"] != self._think_key(me) and not early) or (early and not self._early_think_valid(ctx, me)):
            self._log(f"           思考結果を破棄: 依頼後に手番・組ぷよ・盤面が変わった(依頼 {t:.2f}s、待ち {wait_ms:.0f}ms)")
            if ctx["key"][0] == me.turn and me.current_pair is not None:
                self._think_pending = True  # 同じ手番で盤面が変わった: 今の状態で思考し直す
            return
        if self._think_worker is not None and wait_ms >= 200:
            self._log(f"           思考結果の反映まで {wait_ms:.0f}ms(この間も観測は続いている)")
        if isinstance(exc, NoValidPlacementError):
            # 置ける手が無い(盤面の誤認・ゲームオーバー等)。ループを止めず提案を消して観測を続け、
            # 次の手番・再計算の理由が来た時に再探索する(同じ状態で毎フレーム再試行はしない)
            self._log(f"           思考スキップ: {exc} → 提案を消して観測を続けます")
            self.status_changed.emit("置ける手がありません(盤面の認識を確認)")
            self._last_data = None
            self._precomputed = None
            self.draw_data_ready.emit(None)
            return
        if exc is not None:
            raise exc
        assert move is not None
        self._think_submitted = ctx["submitted"]  # 相手の連鎖の後の思考か(_emit_move で相手の撃った連鎖の表示を外す判断)
        if instant is not None:
            fresh = move
            if instant.label == "build" and is_build_like(fresh):
                # 思考も構築(伸ばす・受けて組み続けるを含む): 乱数・ネクネクの違いで置き場所が変わるだけなので先読みの手を
                # 表示したまま、判断の内容(ラベル・理由・着弾量)だけ思考のものにする
                move = instant
                move.self_analysis = fresh.self_analysis
                move.enemy_analysis = fresh.enemy_analysis
                if fresh.label != "build":
                    move.label, move.reason, move.plan_steps, move.request = fresh.label, fresh.reason, fresh.plan_steps, fresh.request
                    move.incoming, move.incoming_source, move.incoming_chain = fresh.incoming, fresh.incoming_source, fresh.incoming_chain
                    self._log(f"           先読みの手を表示したまま(思考は x={fresh.x} {fresh.rotation} [{fresh.label}] 理由={fresh.reason}。中身は構築)")
                self._emit_move(me, move)
            elif self._committed_turn() == me.turn:
                # 先読みの手を既に固定した: 表示も固定した手に合わせる
                # (食い違うと人間が追えず、記録も実際と違うものになる)
                move = instant
                move.self_analysis = fresh.self_analysis
                move.enemy_analysis = fresh.enemy_analysis
                self._emit_move(me, move, note="先読みの手で確定済み")
                self._log(f"           確定した手: 先読みの手で確定済みのため提案を変えません"
                          f"(思考は x={fresh.x} {fresh.rotation} [{fresh.label}])")
            else:
                reason = FIRE_REASON_TEXT_JA.get(move.fire_reason or "", "")
                self._emit_move(me, move, note="対応が必要: 提案を変更" + (f" ({reason})" if reason else ""))
                self._log(f"           対応のため提案を変更: x={move.x} {move.rotation} [{move.label}]")
        elif (sent := self._committed_move(me)) is not None and (sent.axis_cell, sent.child_cell) != (move.axis_cell, move.child_cell):
            # この手番の手を確定した(先読み・相手の発火の想定を出現時点で固定した、または再計算の前に固定した)後で、思考が
            # 別の手を選んだ: 固定した提案は変えないので、表示と次の手の先読みは確定した手で行う。思考の手の盤面で先読みすると
            # 次の手番で盤面が合わず、安定待ちと思考の間に組ぷよが自由落下した
            fresh = move
            move = sent
            move.self_analysis = fresh.self_analysis
            move.enemy_analysis = fresh.enemy_analysis
            move.incoming, move.incoming_source, move.incoming_chain = fresh.incoming, fresh.incoming_source, fresh.incoming_chain
            self._emit_move(me, move, note="確定した手を表示中")
            self._log(f"           確定した手: 確定した手を表示中のため提案を変えません(思考は x={fresh.x} {fresh.rotation} [{fresh.label}]、"
                      f"確定した手 x={move.x} {move.rotation}。次の手は確定した手の盤面で先読み)")
        else:
            last = self._last_data
            same_placement = last is not None and (move.axis_cell, move.child_cell) == (last.axis_cell, last.child_cell)
            incoming_changed = abs((move.incoming or 0) - self._incoming_at_think) >= RETHINK_PENDING_INCREASE
            if (
                note and not new_turn and last is not None
                and move.label in BUILD_LABELS and last.label in BUILD_LABELS and not incoming_changed
            ):
                # 構築の再計算で構築のまま、かつ着弾量も変わっていない(入力が同じ): ビームサーチの乱数で手が
                # 変わるだけなので表示中の提案を維持し、解析値だけ更新する。着弾量が変わった再計算は採用する
                self._log(f"           再計算の結果は構築のまま(x={move.x} {move.rotation} [{move.label}])で着弾量も同程度: 表示中の提案を維持")
                last.self_analysis = move.self_analysis
                last.enemy_analysis = move.enemy_analysis
                last.chaining = me.chain_active
                self.draw_data_ready.emit(last)
                self._pending_at_think = me.pending_garbage
                # 相手の消去開始などで先読みを捨てた後は、表示中の手で次の手を先読みし直す
                if self._precomputed is None and self._last_move is not None:
                    self._dispatch_precompute(me, en, self._last_move)
                return
            if note and not new_turn and same_placement:
                # 置き場所は同じでも判断の意味(ラベル・理由・連鎖表示)は変わりうるので表示内容は更新する
                self._log(f"           再評価: 置き場所は同じ(x={move.x} {move.rotation} [{move.label}])、判断内容を更新")
            if note:
                reason = FIRE_REASON_TEXT_JA.get(move.fire_reason or "", "")
                note = note + (f" ({reason})" if reason else "")
            elif ctx["en_state"] is None and not self.solo:
                note = "相手の認識エラー: 相手を見ずに提案"
            self._applying_early = early  # 取り込みを待たずに考えた手は、確定待ちのままでも固定してよい
            try:
                self._emit_move(me, move, note=note)
            finally:
                self._applying_early = False
            if early:
                self._log(f"           取り込みを待たずに考えた手を反映(出現の{(self._now or t) - self._self_spawn_t:.2f}秒後、思考の待ち {wait_ms:.0f}ms)")
        self._last_form = move.form
        self._pending_at_think = me.pending_garbage
        self._incoming_at_think = move.incoming or 0
        if move.incoming is not None:
            # 相手の連鎖を再現できた時だけ返る。予告が消えるまで保持し、自分の attack として ama に渡す
            if self._incoming_carry and move.incoming != self._incoming_carry:
                self._log(f"           着弾量を更新 {self._incoming_carry}個 → {move.incoming}個 ({move.incoming_source})")
            self._incoming_carry = move.incoming
            if self._attack is not None:
                self._attack.add_estimate(t, f"再現({move.incoming_source})", move.incoming)
        self._think_pending = me.next2 is None  # ネクネク未読なら、読めた時に3組でやり直す
        self.status_changed.emit(f"手番 {me.turn}  思考 {move.think_ms}ms")
        self._log_think(t, me, en, ctx["my_pairs"], ctx["en_pairs"], move, ctx["frame"], board_text=ctx.get("early_board"))

        self._last_move = move
        self._dispatch_precompute(me, en, move)

    def _refresh_precompute_for_fixed(self) -> None:
        """相手の連鎖の開始・終了で攻撃の状況が変わった: この手番の手を確定していれば、確定した手の盤面で次の手の先読みを
        すぐやり直す。再計算は自分の連鎖中などで保留されることがあり、それを待つと次の組ぷよの出現に間に合わなかった
        """
        me = self.trackers["self"]
        sent = self._committed_move(me)
        if sent is not None and me.next1 is not None and me.next2 is not None:
            self._dispatch_precompute(me, self.trackers["enemy"], sent, urgent=True)

    def _committed_move(self, me: PlayerTracker) -> BattleMove | None:
        """この手番で固定した提案(していなければ None)。"""
        sent = self._fixed_move
        if not self._fixes_moves() or self._committed_turn() != me.turn or sent is None or sent[0] != me.turn:
            return None
        return sent[1]

    def _attack_snapshot(self, me: PlayerTracker) -> tuple:
        """先読みを計算した時の攻撃の状況(相手の連鎖中か、思考が見積もった着弾量)。出現時に同じなら、相手の攻撃中でも
        先読みの手は今の状況で考えた手と同じ前提で計算されている。着弾量は予告の数ではなく見積もり(保持している着弾見込み、
        無ければ直前の思考の着弾量)で比べる: 予告の数は相手の連鎖が進むと増えるので、同じ攻撃でも食い違った
。見積もりが無い時だけ予告の有無を使う"""
        en = self.trackers["enemy"]
        if en.chain_active:
            # 相手の連鎖中は「どの連鎖か」で比べる。着弾量は思考も先読みもブリッジが同じ連鎖を点滅から再現して見積もるので、
            # こちら側の見積もりが後から更新されても前提は同じ
            return (True, self._attack_count)
        est = self._incoming_carry or self._incoming_at_think
        return (False, est if est else bool(me.pending_garbage))

    def _dispatch_precompute(self, me: PlayerTracker, en: PlayerTracker, move: BattleMove, urgent: bool = False,
                             expected_cells: list[list[str]] | None = None) -> None:
        """次の手を先読みする: 提案通りに置いた後の盤面で、ネクストを現在の組ぷよとして計算する
        (現在の手を表示した後に、思考スレッドで計算する)。同じ盤面・組ぷよ・手・攻撃の状況の先読みを
        計算中か計算済みなら、もう一度は依頼しない(確定した手の再計算で先に依頼した分と重ならないように)。"""
        if not (self.enable_precompute and (move.field_after is not None or expected_cells is not None) and me.next2 is not None):
            return
        place_key = (move.axis_cell, move.child_cell) if expected_cells is None else ("observed", cells_to_text(expected_cells))
        basis = (self._think_key(me), place_key, self._attack_snapshot(me))
        if basis == self._pre_basis and (self._pre_inflight is not None or self._precomputed is not None):
            return
        expected = self.mapper.rows_to_cells(move.field_after) if expected_cells is None else [row[:] for row in expected_cells]
        try:
            pre_my, pre_en, _, _ = self._build_states(me, en, expected, [me.next1, me.next2], ahead=True)
        except ValueError as exc2:
            self._log(f"           先読みスキップ: {exc2}")
            return
        pre_ctx = {"key": self._think_key(me), "move": move, "expected": expected, "pairs": (me.next1, me.next2),
                   "attack": self._attack_snapshot(me)}
        self._pre_basis = basis
        self._pre_inflight = pre_ctx
        self._dispatch("pre", pre_ctx, lambda: self._battle(pre_my, pre_en), urgent=urgent)
        # 相手がこの場面で撃ってきた場合の想定: 相手が今すぐ撃てる最大の攻撃を受けた状態で、次の手も計算しておく
        # (提案を固定する拡張が、相手の発火の直後の出現ですぐ使う)
        imm = move.enemy_analysis.immediate if move.enemy_analysis is not None else None
        if (
            self._fixes_moves() and not self.solo and pre_en is not None and imm is not None
            and imm.garbage >= ANTICIPATE_MIN_GARBAGE and not self._attack_involved(me)
        ):
            atk_my = replace(pre_my, attack=pre_my.attack + imm.garbage)
            atk_en = replace(pre_en, attack_frame=max(1, imm.count // 2))
            alt_ctx = {"key": self._think_key(me), "expected": expected, "pairs": (me.next1, me.next2), "assumed": imm.garbage}
            self._dispatch("pre_atk", alt_ctx, lambda: self._battle(atk_my, atk_en))

    def _apply_precompute_alt(self, ctx: dict, pre_move: BattleMove | None, exc: BaseException | None) -> None:
        """相手の発火を想定した先読みの結果を残す(依頼した後に手番・盤面が変わっていれば捨てる)。"""
        if exc is not None:
            if isinstance(exc, (ValueError, RuntimeError)):
                return
            raise exc
        me = self.trackers["self"]
        if ctx["key"] != self._think_key(me) or pre_move is None or not pre_move.path:
            return
        self._precomputed_alt = (ctx["expected"], ctx["pairs"], pre_move, ctx["assumed"])

    def _incoming_estimate(self) -> int:
        """今の着弾量の見積もり(保持している着弾見込み → 直前の思考の着弾量 → 相手の連鎖を点滅から再現した量)。"""
        est = self._incoming_carry or self._incoming_at_think
        if est:
            return est
        en = self.trackers["enemy"]
        if en.chain_active and en.chain_expected is not None and en.chain_expected.steps > 0:
            bonus = 30 if en.chain_bonus else 0
            return en.chain_expected.score // 70 + bonus
        return 0

    def _apply_precompute(self, ctx: dict, pre_move: BattleMove | None, exc: BaseException | None) -> None:
        """先読み(2手先)の結果を反映する。依頼した後に手番・盤面が変わっていれば捨てる。"""
        me = self.trackers["self"]
        if ctx is self._pre_inflight:
            self._pre_inflight = None
        if exc is not None:
            if isinstance(exc, (ValueError, RuntimeError)):
                self._log(f"           先読みスキップ: {exc}")
                return
            raise exc
        if ctx["key"] != self._think_key(me):
            # 出現の後に届いた先読み: ちょうど次の手番(組ぷよが先読みどおり)で、まだ手を確定していなければ使う。
            # 捨てて考え直すと思考を待つ間に自由落下した
            if (
                pre_move is not None and pre_move.path and me.turn == ctx["key"][0] + 1
                and ctx["pairs"] == (me.current_pair, me.next1)
                and self._fixes_moves() and self._committed_turn() != me.turn
            ):
                self._precomputed = (ctx["expected"], ctx["pairs"], pre_move)
                self._precomputed_attack = ctx.get("attack")
                if me.settling:
                    me.set_expected(ctx["expected"])
                self._log(f"           先読みが出現の後に届いた(turn={me.turn}): 使えるか確かめます")
                self._precomputed_ready(me)
            elif pre_move is not None and pre_move.path and me.turn == ctx["key"][0] and ctx["pairs"] == (me.next1, me.next2):
                # 同じ手番のうちに盤面の取り込みが進んだ(自分の連鎖中など)だけ: 次の手番の出現で盤面と組ぷよを確かめてから
                # 使うので残す(捨てると、自分の連鎖中に相手が連鎖を始めた後の出現で先読みが無かった)
                self._precomputed = (ctx["expected"], ctx["pairs"], pre_move)
                self._precomputed_attack = ctx.get("attack")
            return  # 次の手番が始まった等: 古い先読みは使わない
        if self._last_data is None:
            return
        assert pre_move is not None
        expected = ctx["expected"]
        self._precomputed = (expected, ctx["pairs"], pre_move)
        self._precomputed_attack = ctx.get("attack")
        me.set_expected(expected)
        # 2手先として小さく表示する(提案通りに置いた後の盤面での着地マス)
        self._emit_move(me, ctx["move"], next_move=pre_move)

    # ---- デバッグ出力 ----

    def _log(self, text: str) -> None:
        if self.debug_log_path is None:
            return
        with open(self.debug_log_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")

    def _log_think(
        self,
        t: float,
        me: PlayerTracker,
        en: PlayerTracker,
        my_pairs: list[str],
        en_pairs: list[str],
        move: BattleMove,
        frame_bgr: np.ndarray,
        board_text: str | None = None,
    ) -> None:
        """board_text は思考に使った自分の盤面(取り込みを待たずに考えた時。省略時は確定した盤面)。"""
        if self.debug_log_path is None:
            return
        # 取り込みを待たずに考えた手は、確定した盤面(前の手番のもの)ではなく考えた盤面を残す
        origin = "取り込み前の観測" if board_text is not None else me.last_take_in_reason
        board_text = board_text if board_text is not None else cells_to_text(me.settled)
        self._log(
            f"           提案: x={move.x} {move.rotation} [{move.label}] eval={move.eval} form={move.form} fire={move.fire_reason} "
            + (f"理由={move.reason} " if move.reason else "")
            + (f"跳考{move.choukou}回 " if move.choukou else "")
            + (f"着弾{move.incoming}個({move.incoming_source},{move.incoming_chain}連鎖) " if move.incoming is not None else "")
            + (f"保持{self._incoming_carry}個 " if self._incoming_carry > 0 else "")
            + (f"自分:最大{move.self_analysis.max.count}連鎖{move.self_analysis.max.score}点 " if move.self_analysis else "")
            + (f"相手:最大{move.enemy_analysis.max.count}連鎖{move.enemy_analysis.max.score}点 " if move.enemy_analysis else "")
            + f"({move.think_ms}ms)\n"
            + (f"           {diag_text(move.diag)}\n" if move.diag else "")
            + (f"           要求: {move.request}\n" if move.request else "")
            + f"           自分 {my_pairs} 予告{me.pending_garbage}{' 全消し持ち' if me.all_clear else ''}"
            f" 盤面の由来={origin}\n{_indent(board_text)}\n"
            f"           相手 {en_pairs} 予告{en.pending_garbage} 連鎖中={en.chain_active}"
            f"{' 全消し持ち' if en.all_clear else ''}{' 連鎖にボーナス付き' if en.chain_bonus else ''}\n{_indent(cells_to_text(en.settled))}"
        )
        if self._attack is not None and move.enemy_analysis is not None:
            self._attack.add_estimate(t, "思考時の相手本線", move.enemy_analysis.max.garbage)
        if frame_bgr is None:
            return  # 取り込みを待たずに考えた時に、まだフレームが無い
        path = self.history_dir / f"{self._history_index % FRAME_HISTORY_SIZE:03d}_turn{me.turn}.png"
        self._saver.save(path, frame_bgr)
        self._history_index += 1


def column_height(board: list[list[str]], col: int) -> int:
    """その列の高さ(最下段から数えた段数。出現位置は数えない)。"""
    cells = clear_spawn_cells(board)
    return next((len(cells) - r for r in range(len(cells)) if cells[r][col] != EMPTY), 0)


def attack_involved(self_pending: int, incoming_carry: int, solo: bool, enemy_chain: bool, enemy_pending: int, engine: str) -> bool:
    """相手の攻撃が絡む局面か。思考が受け・潰しに手を変えることがあるので、先読みの手をそのまま使わない条件にする

    自分への予告・着弾見込み・相手の連鎖中は常に絡む。相手への予告(自分が送ったおじゃまがまだ落ちていない)は
    ama では判断が変わりうるので絡むとするが、自作AI(yamaneko)の判断は相手への予告を使わないので絡まないとする
。"""
    if self_pending or incoming_carry:
        return True
    if solo:
        return False
    return bool(enemy_chain or (enemy_pending and engine != "yamaneko"))


def observed_match(me: PlayerTracker, expected: list[list[str]]) -> bool:
    """出現時点の観測盤面が期待盤面どおりか。観測盤面そのものか、白い目の無い色のマス(着地予測の点・攻撃が当たる演出)を
    空にした盤面のどちらかが一致すればよい(本物のぷよには目がある。目が見えない瞬間の本物のぷよは、そのままの観測盤面で一致する)。
    どちらも、操作中の組ぷよの着地予測の点(3列目の積みのすぐ上の2マスにある組ぷよの色)は空として比べる。"""
    pair = me.current_pair
    obs = me.observed_cells
    if obs is not None and boards_match(strip_spawn_ghost(obs, expected, pair), expected):
        return True
    eyed = me.observed_cells_eyed
    return eyed is not None and eyed is not obs and boards_match(strip_spawn_ghost(eyed, expected, pair), expected)


def strip_spawn_ghost(observed: list[list[str]], expected: list[list[str]], pair: Pair | None) -> list[list[str]]:
    """出現の直後に3列目の積み(期待盤面)のすぐ上の2マスに出る、操作中の組ぷよの着地予測の点を空にした盤面を返す。
    組ぷよは3列目に縦向きで出るので、点の位置は期待盤面の3列目の高さで決まる。色が組ぷよのどちらかの色の時だけ空にする
。"""
    if pair is None:
        return observed
    col = 2
    top = next((r for r in range(len(expected)) if expected[r][col] != EMPTY), len(expected))
    rows = [r for r in (top - 1, top - 2) if r >= 0]
    colors = set(pair)
    hits = [r for r in rows if observed[r][col] != EMPTY and observed[r][col] in colors]
    if not hits:
        return observed
    out = [row[:] for row in observed]
    for r in hits:
        out[r][col] = EMPTY
    return out


def boards_match(observed: list[list[str]], expected: list[list[str]]) -> bool:
    """出現時点で、観測盤面が先読みの期待盤面どおりか(13段目と出現位置は除く)。期待盤面がおじゃまのマスは、観測が何色でも
    埋まっていればよい: 相手の攻撃の演出中は、おじゃまが色ぷよに読まれることがあり、先読みを送れずに自由落下した
    """
    obs, exp = clear_spawn_cells(observed), clear_spawn_cells(expected)
    for r in range(1, len(exp)):
        for c in range(len(exp[r])):
            e, o = exp[r][c], obs[r][c]
            if e == o:
                continue
            if e == GARBAGE and o != EMPTY:
                continue
            return False
    return True


def field_edge_offset(frame_bgr: np.ndarray, geometry, origin: tuple[int, int] = (0, 0)) -> int:  # noqa: ANN001
    """盤面の右の枠(盤面の内側と枠の境目)の横位置を、校正した位置からのずれ(px、右が正)で返す。
    校正した境目から外側(枠の側)へ0.7マス、内側へ0.1マスの範囲で、横方向の明るさの差を縦に足して最も強い位置を境目と
    みなす(内側まで探すと壁際のぷよの輪郭を取り違える。縦は見える盤面の2〜11段目。上の方は攻撃の演出がかかりやすい)。
    左の枠は、自分の盤面がキャプチャの範囲の左端に接していて測れないので見ない。値そのものより、普段の値との差を見る
    (実機の画面で、画面を横に7pxずらすと値も7px変わることを確かめた)。"""
    h, w = frame_bgr.shape[:2]
    cell = geometry.cell_size()
    outer, inner = max(4, int(cell * 0.7)), max(2, int(cell * 0.1))
    x_top, y0 = geometry.grid_to_screen(float(COLS), 1.0)
    x_bot, y1 = geometry.grid_to_screen(float(COLS), 10.0)
    x = int(round((x_top + x_bot) / 2 - origin[0]))
    ya, yb = max(0, int(min(y0, y1) - origin[1])), min(h, int(max(y0, y1) - origin[1]))
    xa, xb = max(0, x - inner), min(w, x + outer + 1)
    if xb - xa < 3 or yb - ya < 3:
        return 0
    gray = cv2.cvtColor(np.ascontiguousarray(frame_bgr[ya:yb, xa:xb]), cv2.COLOR_BGR2GRAY).astype(np.int32)
    gx = np.abs(np.diff(gray, axis=1)).sum(axis=0)
    return int(xa + int(np.argmax(gx)) + 1 - x)


def board_diff(observed: list[list[str]] | None, expected: list[list[str]]) -> list[str]:
    """boards_match で違うとされたマスを「12段目3列目 期待red 読みgreen」の形で返す(13段目と出現位置は除く)。"""
    if observed is None:
        return ["観測盤面なし"]
    obs, exp = clear_spawn_cells(observed), clear_spawn_cells(expected)
    out = []
    for r in range(1, len(exp)):
        for c in range(len(exp[r])):
            e, o = exp[r][c], obs[r][c]
            if e == o or (e == GARBAGE and o != EMPTY):
                continue
            out.append(f"{len(exp) - r}段目{c + 1}列目 期待{e} 読み{o}")
    return out


class TickStats:
    """観測ループの診断: 観測の間隔・画面取得の待ち・1回の処理時間を TICK_STATS_SECONDS ごとにまとめてログに出す
。"""

    def __init__(self) -> None:
        self.since: float | None = None
        self.last_start: float | None = None
        self.intervals: list[float] = []
        self.grabs: list[float] = []
        self.works: list[float] = []
        self.cpu: list[float] = []  # その間の PC 全体の CPU 使用率(%、1秒ごと)

    def add(self, t: float, tick_start: float, grab: float, elapsed: float) -> str:
        if self.last_start is not None:
            self.intervals.append(tick_start - self.last_start)
        self.last_start = tick_start
        self.grabs.append(grab)
        self.works.append(elapsed)
        if self.since is None:
            self.since = t
        if t - self.since < TICK_STATS_SECONDS or not self.intervals:
            return ""

        def ms(v: list[float]) -> str:
            v = sorted(v)
            return f"中央 {v[len(v) // 2] * 1000:.0f}ms・90% {v[len(v) * 9 // 10] * 1000:.0f}ms"

        text = (f"観測の統計({len(self.works)}回): 間隔 {ms(self.intervals)} / 画面取得の待ち {ms(self.grabs)}"
                f" / 1回の処理(取得込み) {ms(self.works)}")
        if self.cpu:
            cpu = sorted(self.cpu)
            text += f" / CPU 中央 {cpu[len(cpu) // 2]:.0f}%・最大 {cpu[-1]:.0f}%"
        self.__init__()
        self.last_start = tick_start
        return text


def _indent(text: str) -> str:
    return "\n".join("               " + line for line in text.splitlines())


# メイン画面の「CPU の使用」(YamaNEKO の探索スレッド数)の選択肢 (表示, キー)。「自動」は今までどおり
