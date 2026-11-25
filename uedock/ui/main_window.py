"""メイン画面の枠(ゲームの中身に関わらない部分)。

- ゲームのウィンドウの検出と大きさの調整
- 対戦モード(1P・2P)・一人用モードのタブと、サイドごとの「ゲーム種別 → 思考エンジン → ゲームモードの設定」
- 支援モードの開始・終了(中身は各ゲームモードの start_assist)
- シミュレーターのボタン(各ゲームモードが登録したもの)
- エンジンの登録・プラグイン
- OBS 出力(OBS 出力プラグインが有効な時だけ。uedock/obs_output.py)
- プラグインの拡張(uedock/extension.py)が足す項目と、支援モードの前後の処理
"""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from .. import i18n
from ..assist import build_requests
from ..capture.game_window import (
    CLIENT_SIZE_PRESETS,
    find_game_window,
    list_windows,
    load_window_size,
    resize_client,
    save_saved_window,
    save_window_size,
)
from ..engine_registry import Registry, SideSelection, available_engines
from ..game_mode import (
    LAYOUT_SIDES,
    LAYOUT_SOLO,
    LAYOUT_VERSUS,
    NO_GAME,
    NO_GAME_LABEL,
    SIDE_LABELS,
    AssistSession,
    GameMode,
    all_modes,
)
from ..obs_output import ObsOutput
from ..paths import APP_TITLE
from ..plugin_api import FEATURE_DEBUG_LOG, FEATURE_OBS_OVERLAY
from ..plugin_loader import PluginManager, save_disabled, set_current
from .engine_dialog import EngineRegistryDialog
from .obs_dialog import ObsGuideDialog
from .plugin_dialog import PluginDialog
from .toggle_switch import ToggleSwitch
from .window_dialog import WindowPickDialog

MISSING_SUFFIX = "(見つかりません)"
# 支援モードが自分で止まった(ゲームのウィンドウが動いた等)のを確かめる間隔
SESSION_POLL_MS = 500


class SideBox(QtWidgets.QGroupBox):
    """1つのサイドの枠: ゲーム種別・思考エンジンと、ゲームモードの設定項目。"""

    changed = QtCore.pyqtSignal()

    def __init__(self, side: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(SIDE_LABELS[side], parent)
        self.side = side
        self._loading = False
        self._panel: QtWidgets.QWidget | None = None
        self._layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.game_combo = QtWidgets.QComboBox()
        self.game_combo.currentIndexChanged.connect(self._emit)
        form.addRow("ゲーム種別:", self.game_combo)
        self.engine_combo = QtWidgets.QComboBox()
        self.engine_combo.currentIndexChanged.connect(self._emit)
        self.engine_combo.currentIndexChanged.connect(self._tell_engine)
        form.addRow("思考エンジン:", self.engine_combo)
        self._layout.addLayout(form)
        self._layout.addStretch(1)

    def add_widget(self, widget: QtWidgets.QWidget) -> None:
        """枠の下(ゲームモードの設定項目の後)に項目を足す(プラグインの拡張が使う)。"""
        self._layout.insertWidget(self._layout.count() - 1, widget)

    def _emit(self) -> None:
        if not self._loading:
            self.changed.emit()

    def _tell_engine(self) -> None:
        """ゲームモードの設定項目に、今の思考エンジンを伝える(エンジンによって使えない項目を暗転させる用)。"""
        if self._panel is not None and hasattr(self._panel, "set_engine"):
            self._panel.set_engine(self.engine())

    def game(self) -> str:
        return self.game_combo.currentData() or NO_GAME

    def engine(self) -> str:
        return self.engine_combo.currentData() or ""

    def load(self, modes: dict[str, GameMode], selection: SideSelection, registry: Registry, plugins: PluginManager) -> None:
        """選択を画面に出す。ゲーム種別に合うエンジンだけを一覧に並べる。"""
        self._loading = True
        try:
            self.game_combo.clear()
            self.game_combo.addItem(NO_GAME_LABEL, NO_GAME)
            for mode in modes.values():
                self.game_combo.addItem(mode.name, mode.id)
            game = selection.game(self.side)
            self.game_combo.setCurrentIndex(max(0, self.game_combo.findData(game)))
            mode = modes.get(self.game())
            self.engine_combo.clear()
            if mode is not None:
                for name, spec in available_engines(mode, registry, plugins).items():
                    self.engine_combo.addItem(name if spec.exists() else name + MISSING_SUFFIX, name)
                wanted = selection.engine_name(self.side, mode)
                index = self.engine_combo.findData(wanted)
                self.engine_combo.setCurrentIndex(index if index >= 0 else max(0, self.engine_combo.findData(mode.default_engine())))
            self.engine_combo.setEnabled(mode is not None)
            self._set_panel(mode)
        finally:
            self._loading = False

    def _set_panel(self, mode: GameMode | None) -> None:
        """ゲームモードの設定項目(定跡・開幕テンプレなど)を入れ替える。"""
        if self._panel is not None:
            self._layout.removeWidget(self._panel)
            self._panel.deleteLater()
            self._panel = None
        if mode is not None and mode.side_panel is not None:
            self._panel = mode.side_panel(self.side, self)
            self._layout.insertWidget(1, self._panel)
            self._tell_engine()


# 起動した時のメイン画面の大きさ
INITIAL_SIZE = (1280, 720)


class MainWindow(QtWidgets.QWidget):
    def __init__(self, plugins: PluginManager | None = None) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(*INITIAL_SIZE)
        self.plugins = PluginManager.load() if plugins is None else plugins
        set_current(self.plugins)
        # 表示の言語(言語プラグイン)。画面を作る前に決める(作る時の文言から訳して出す)
        i18n.install()
        self._apply_language(i18n.load_choice())
        self.registry = Registry.load()
        self.selection = SideSelection.load()
        self.modes: dict[str, GameMode] = all_modes(self.plugins)
        self.sessions: list[AssistSession] = []
        self._simulators: list[QtWidgets.QWidget] = []  # 開いたシミュレーター(閉じるまで参照を持つ)
        # プラグインの拡張(uedock/extension.py)。{プラグインの ID: 拡張}。呼ぶのは有効なプラグインの分だけ
        self.extensions = self.plugins.make_extensions()
        self._locked_widgets: list[QtWidgets.QWidget] = []  # 支援モードの間は触れなくする項目(拡張が足したもの)

        root = QtWidgets.QVBoxLayout(self)

        # ゲームのウィンドウ
        row = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel()
        row.addWidget(self.status_label, 1)
        self.detect_btn = QtWidgets.QPushButton("ゲームのウィンドウを検出")
        self.detect_btn.setToolTip("Steam 版のゲームのウィンドウを探します。見つからなければ一覧から選べます(選んだものは覚えます)")
        self.detect_btn.clicked.connect(self._on_detect_clicked)
        row.addWidget(self.detect_btn)
        # 表示の言語は、言語プラグインが有効な時だけ出す
        self.language_label = QtWidgets.QLabel("表示の言語 (Language):")
        self.language_label.setToolTip("画面の文言と、ゲームの上の表示(情報パネルなど)の言語を切り替えます。ログ・設定ファイルは日本語のままです")
        row.addWidget(self.language_label)
        self.language_combo = QtWidgets.QComboBox()
        self.language_combo.currentIndexChanged.connect(self._on_language_chosen)
        row.addWidget(self.language_combo)
        root.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("ゲームのウィンドウの大きさ:"))
        self.window_size_combo = QtWidgets.QComboBox()
        for width, height in CLIENT_SIZE_PRESETS:
            self.window_size_combo.addItem(f"{width}×{height}")
        self.window_size_combo.setCurrentIndex(max(0, self.window_size_combo.findText(load_window_size())))
        self.window_size_combo.currentTextChanged.connect(save_window_size)
        row.addWidget(self.window_size_combo)
        self.resize_window_btn = QtWidgets.QPushButton("この大きさに合わせる")
        self.resize_window_btn.setToolTip("ゲームの描画領域(タイトルバー・枠を除く)をこの大きさ(画面の物理ピクセル)にします")
        self.resize_window_btn.clicked.connect(self._on_resize_window_clicked)
        row.addWidget(self.resize_window_btn)
        row.addStretch(1)
        root.addLayout(row)

        # 手動キャリブレーション(キャプチャボード対応)。用意のあるゲームモードの分だけボタンを出す
        self.calibrate_row = QtWidgets.QWidget()
        self._calibrate_layout = QtWidgets.QHBoxLayout(self.calibrate_row)
        self._calibrate_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.calibrate_row)

        # 対戦モード(1P・2P)・一人用モード
        self.tabs = QtWidgets.QTabWidget()
        self.side_boxes: dict[str, SideBox] = {}
        self._layouts = (LAYOUT_VERSUS, LAYOUT_SOLO)
        for layout_id, title in ((LAYOUT_VERSUS, "対戦モード"), (LAYOUT_SOLO, "一人用モード")):
            page = QtWidgets.QWidget()
            panels = QtWidgets.QHBoxLayout(page)
            for side in LAYOUT_SIDES[layout_id]:
                box = SideBox(side)
                box.changed.connect(lambda s=side: self._on_side_changed(s))
                self.side_boxes[side] = box
                panels.addWidget(box, 1)
            self.tabs.addTab(page, title)
        self.tabs.setCurrentIndex(self._layouts.index(self.selection.layout))
        self.tabs.currentChanged.connect(self._on_layout_changed)
        root.addWidget(self.tabs, 1)

        # ログの出力は、ログ出力プラグインが有効な時だけ出す
        self.debug_log_switch = ToggleSwitch("認識結果と提案をログに出力する(debug_logs/)")
        root.addWidget(self.debug_log_switch)

        # OBS 出力は、OBS 出力プラグインが有効な時だけ出す。
        # 設定はオン・オフだけ。OBS 側の手順は「OBS の設定のしかた…」にまとめた
        self.obs = ObsOutput(lambda: find_game_window(), parent=self)
        self.obs_row = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(self.obs_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.obs_switch = ToggleSwitch("OBS に表示を出す(配信・録画用)")
        self.obs_switch.setToolTip("ドット・情報パネル・エンジン名を、OBS の「ブラウザ」ソースに出します。\n"
                                   "Universal Engine Dock の表示は自分の画面の読み取りに写らないようにしているので、OBS の画面の取り込みには写りません")
        self.obs_switch.setChecked(self.obs.enabled)
        self.obs_switch.toggled.connect(self._on_obs_toggled)
        row.addWidget(self.obs_switch)
        self.obs_help_btn = QtWidgets.QPushButton("OBS の設定のしかた…")
        self.obs_help_btn.clicked.connect(self._open_obs_guide)
        row.addWidget(self.obs_help_btn)
        self.obs_status = QtWidgets.QLabel()
        self.obs_status.setWordWrap(True)
        row.addWidget(self.obs_status, 1)
        self.obs.changed.connect(self._refresh_obs_status)
        root.addWidget(self.obs_row)

        self.assist_btn = QtWidgets.QPushButton("支援モード開始")
        self.assist_btn.clicked.connect(self._on_assist_clicked)
        root.addWidget(self.assist_btn)

        # シミュレーター(各ゲームモードが登録したもの)
        self.simulator_row = QtWidgets.QWidget()
        self._simulator_layout = QtWidgets.QHBoxLayout(self.simulator_row)
        self._simulator_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.simulator_row)

        row = QtWidgets.QHBoxLayout()
        self.engine_btn = QtWidgets.QPushButton("エンジンの登録…")
        self.engine_btn.clicked.connect(self._open_engines)
        row.addWidget(self.engine_btn)
        self.plugin_btn = QtWidgets.QPushButton("プラグイン…")
        self.plugin_btn.clicked.connect(self._open_plugins)
        row.addWidget(self.plugin_btn)
        row.addStretch(1)
        self._tool_layout = row  # プラグインの拡張が足すボタンの置き場(add_tool_widget)
        root.addLayout(row)

        self._poll = QtCore.QTimer(self)
        self._poll.setInterval(SESSION_POLL_MS)
        self._poll.timeout.connect(self._check_sessions)

        for ext in self.extensions.values():
            ext.install(self)
        self._rebuild_mode_buttons()
        self._refresh_sides()
        self._refresh_window_status()

    # ---- プラグインの拡張が使う窓口 ----

    def add_side_widget(self, side: str, widget: QtWidgets.QWidget) -> None:
        """サイドの枠に項目を足す。支援モードの間は触れなくする。"""
        self.side_boxes[side].add_widget(widget)

    def add_tool_widget(self, widget: QtWidgets.QWidget) -> None:
        """下の行(エンジンの登録・プラグインのボタンの並び)に項目を足す。支援モードの間は触れなくする。"""
        self._tool_layout.insertWidget(self._tool_layout.count() - 1, widget)
        self._locked_widgets.append(widget)

    def active_extensions(self) -> list:
        """有効なプラグインの拡張。"""
        enabled = {p.id for p in self.plugins.enabled}
        return [ext for plugin_id, ext in self.extensions.items() if plugin_id in enabled]

    # ---- ゲームモードの一覧に合わせて作り直す部分 ----

    @staticmethod
    def _clear(layout: QtWidgets.QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _rebuild_mode_buttons(self) -> None:
        """キャリブレーションとシミュレーターのボタンを、今のゲームモードの一覧から作り直す。"""
        self._clear(self._calibrate_layout)
        self.calibrate_buttons: dict[str, QtWidgets.QPushButton] = {}
        for mode in self.modes.values():
            if mode.calibrate is None:
                continue
            btn = QtWidgets.QPushButton(f"{mode.name}のキャリブレーション(キャプチャボード対応)")
            btn.clicked.connect(lambda _checked=False, m=mode: m.calibrate(self.selection.layout, self))
            self.calibrate_buttons[mode.id] = btn
            self._calibrate_layout.addWidget(btn)
        self._calibrate_layout.addStretch(1)
        self.calibrate_row.setVisible(bool(self.calibrate_buttons))

        self._clear(self._simulator_layout)
        self.simulator_buttons: list[QtWidgets.QPushButton] = []
        for mode in self.modes.values():
            for entry in mode.simulators:
                btn = QtWidgets.QPushButton(entry.label)
                btn.clicked.connect(lambda _checked=False, e=entry: self._open_simulator(e))
                self.simulator_buttons.append(btn)
                self._simulator_layout.addWidget(btn)
        self._simulator_layout.addStretch(1)
        self.simulator_row.setVisible(bool(self.simulator_buttons))
        self.debug_log_switch.setVisible(self.plugins.has_feature(FEATURE_DEBUG_LOG))
        obs_allowed = self.plugins.has_feature(FEATURE_OBS_OVERLAY)
        self.obs_row.setVisible(obs_allowed)
        self.obs.set_allowed(obs_allowed)
        self._refresh_obs_status()
        self._refresh_languages()
        enabled = {p.id for p in self.plugins.enabled}
        for plugin_id, ext in self.extensions.items():
            ext.refresh(self, plugin_id in enabled)

    # ---- 表示の言語 ----

    def _apply_language(self, key: str) -> None:
        """表示の言語を key にする。有効な言語プラグインに無いキーなら日本語。"""
        lang = self.plugins.languages().get(key)
        if lang is None:
            i18n.set_language(i18n.JA)
        else:
            i18n.set_language(key, lang.catalog, lang.word_break)

    def _refresh_languages(self) -> None:
        """言語の一覧を、有効な言語プラグインから作り直す。言語プラグインが無ければ隠し、日本語に戻す。"""
        languages = self.plugins.languages()
        if i18n.language() != i18n.JA and i18n.language() not in languages:
            self._apply_language(i18n.JA)
            i18n.retranslate_all()
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        self.language_combo.addItem(i18n.Raw("日本語"), i18n.JA)  # 言語の名前は、どの言語でもその言語の書き方で出す
        for key, lang in languages.items():
            self.language_combo.addItem(i18n.Raw(lang.label), key)
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(i18n.language())))
        self.language_combo.blockSignals(False)
        self.language_label.setVisible(bool(languages))
        self.language_combo.setVisible(bool(languages))

    def _on_language_chosen(self, index: int) -> None:
        key = self.language_combo.itemData(index) or i18n.JA
        if key == i18n.language():
            return
        self._apply_language(key)
        i18n.save_choice(key)
        i18n.retranslate_all()
        self._refresh_window_status()
        self._refresh_obs_status()

    def _refresh_sides(self) -> None:
        for box in self.side_boxes.values():
            box.load(self.modes, self.selection, self.registry, self.plugins)

    def _open_simulator(self, entry) -> None:  # noqa: ANN001
        window = entry.open(self)
        self._simulators = self._open_simulators()
        self._simulators.append(window)
        window.show()

    def _open_simulators(self) -> list[QtWidgets.QWidget]:
        """開いているシミュレーター。閉じると消えるもの(WA_DeleteOnClose)は、Qt 側が消えた後に触ると落ちるので除く。"""
        from PyQt6 import sip

        return [w for w in self._simulators if not sip.isdeleted(w) and w.isVisible()]

    # ---- サイドの選択 ----

    def _on_side_changed(self, side: str) -> None:
        box = self.side_boxes[side]
        game_changed = box.game() != self.selection.game(side)
        self.selection.set_game(side, box.game())
        if not game_changed and box.game() != NO_GAME and box.engine():
            self.selection.set_engine(side, box.game(), box.engine())
        self.selection.save()
        if game_changed:  # ゲーム種別に合うエンジンと設定項目に入れ替える
            box.load(self.modes, self.selection, self.registry, self.plugins)

    def _on_layout_changed(self, index: int) -> None:
        self.selection.layout = self._layouts[index]
        self.selection.save()

    # ---- ゲームのウィンドウ ----

    def _refresh_window_status(self) -> None:
        window = find_game_window()
        if window is not None:
            self.status_label.setText(f"ゲームのウィンドウ: {window.title}({window.width}×{window.height})")
        else:
            self.status_label.setText("ゲームのウィンドウが見つかりません(ゲームを起動して「ゲームのウィンドウを検出」)")

    def _on_detect_clicked(self) -> None:
        """ゲームのウィンドウを探す。見つからなければ表示中のウィンドウから選んでもらい、次から探せるよう覚える。"""
        if find_game_window() is None:
            entries = list_windows()
            if not entries:
                QtWidgets.QMessageBox.warning(self, "エラー", "表示中のウィンドウが見つかりません")
                return
            dialog = WindowPickDialog(entries, self)
            if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted or dialog.selected() is None:
                return
            save_saved_window(dialog.selected())
        self._refresh_window_status()

    def _on_resize_window_clicked(self) -> None:
        """ゲームのウィンドウの描画領域を選んだ大きさにする。"""
        window = find_game_window()
        if window is None:
            QtWidgets.QMessageBox.warning(
                self, "エラー", "ゲームのウィンドウが見つかりません。先に「ゲームのウィンドウを検出」を押してください。"
            )
            return
        size = CLIENT_SIZE_PRESETS[max(0, self.window_size_combo.currentIndex())]
        got = resize_client(window.hwnd, size)
        self._refresh_window_status()
        if got is None:
            QtWidgets.QMessageBox.warning(
                self, "エラー", "ウィンドウの大きさを変えられませんでした(全画面表示か、ゲームが管理者として動いている可能性があります)。"
            )
        elif tuple(got) != tuple(size):
            QtWidgets.QMessageBox.information(
                self, "ウィンドウの大きさ",
                f"{size[0]}×{size[1]} にできず、{got[0]}×{got[1]} になりました(画面に収まらないか、ゲームが大きさを決めています)。",
            )

    # ---- 支援モード ----

    def assist_running(self) -> bool:
        return bool(self.sessions)

    def _on_assist_clicked(self) -> None:
        if self.assist_running():
            self.stop_assist()
        else:
            self.start_assist()

    def start_assist(self) -> bool:
        """今の選択で支援モードを始める。始められなければ理由を出して False。"""
        self._refresh_window_status()
        debug_log = self.plugins.has_feature(FEATURE_DEBUG_LOG) and self.debug_log_switch.isChecked()
        extensions = self.active_extensions()
        requests, error = build_requests(
            self.selection, self.modes, self.registry, self.plugins, find_game_window(), debug_log, tuple(extensions)
        )
        for ext in extensions:
            if error is not None:
                break
            error = ext.prepare(self, requests)
        if error is not None:
            QtWidgets.QMessageBox.warning(self, "支援モードを始められません", error)
            return False
        # 1. 思考エンジンの起動(メイン画面は見えたまま、起動中と出す)
        self.status_label.setText("思考エンジンを起動しています(思考時間の計測で数十秒かかることがあります)…")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        QtWidgets.QApplication.processEvents()
        try:
            for mode, request in requests:
                self.sessions.append(mode.start_assist(request))
        except Exception as exc:  # noqa: BLE001  1つのゲームモードの失敗で、始めた分を残さない
            QtWidgets.QApplication.restoreOverrideCursor()
            self.stop_assist()
            self._refresh_window_status()
            QtWidgets.QMessageBox.warning(self, "支援モードを始められません", str(exc) or type(exc).__name__)
            return False
        QtWidgets.QApplication.restoreOverrideCursor()
        self._refresh_window_status()
        # 2. メイン画面を隠してから観測を始める(ゲームに重なったままだと盤面の位置合わせに写り込む)
        self.hide()
        QtWidgets.QApplication.processEvents()
        try:
            for session in self.sessions:
                session.begin()
        except Exception as exc:  # noqa: BLE001
            self.stop_assist()
            QtWidgets.QMessageBox.warning(self, "支援モードを始められません", str(exc) or type(exc).__name__)
            return False
        self._set_running(True)
        for ext in extensions:
            ext.started(self, list(self.sessions))
        return True

    def stop_assist(self, show: bool = True) -> None:
        for ext in self.active_extensions():
            try:
                ext.stopped(self)
            except Exception:  # noqa: BLE001  拡張の不具合で支援モードを止められなくならないように
                pass
        sessions, self.sessions = self.sessions, []
        for session in sessions:
            try:
                session.stop()
            except Exception:  # noqa: BLE001  1つが止められなくても残りを止める
                pass
        self._set_running(False)
        if show:
            self.show()  # 支援モードの間は隠している

    def _check_sessions(self) -> None:
        """支援モードが自分で止まっていたら(ゲームのウィンドウが動いた等)、残りも止めて画面を戻す。"""
        if self.sessions and not all(s.is_running() for s in self.sessions):
            self.stop_assist()

    def _set_running(self, running: bool) -> None:
        self.assist_btn.setText("支援モード終了" if running else "支援モード開始")
        # 動いている間は、選択と登録を変えさせない(始めた時の設定とずれる)
        for widget in (self.tabs, self.engine_btn, self.plugin_btn, self.calibrate_row, *self._locked_widgets):
            widget.setEnabled(not running)
        if running:
            self._poll.start()
        else:
            self._poll.stop()

    # ---- OBS 出力 ----

    def _on_obs_toggled(self, on: bool) -> None:
        self.obs.set_enabled(on)
        self._refresh_obs_status()
        if on and self.obs.server is not None and not self.obs.connected():
            self._open_obs_guide()  # 初めての人が手順を探さなくてよいように、オンにした時に手順を出す

    def _open_obs_guide(self) -> None:
        ObsGuideDialog(self.obs, self).exec()

    def _refresh_obs_status(self) -> None:
        self.obs_status.setText(self.obs.status_text())

    # ---- 登録・プラグイン ----

    def _reserved_engine_names(self) -> dict[str, set[str]]:
        """登録に使えない名前(同梱・プラグインのエンジン)。"""
        return {
            mode.id: {e.name for e in mode.builtin_engines} | {e.name for e in self.plugins.engines(mode.id)}
            for mode in self.modes.values()
        }

    def _open_engines(self) -> None:
        dialog = EngineRegistryDialog(self.registry, self.modes, self._reserved_engine_names(), self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.registry.engines = dialog.engines
            self.registry.save()
            self._refresh_sides()

    def _open_plugins(self) -> None:
        dialog = PluginDialog(self.plugins, self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            save_disabled(dialog.disabled)
            self.plugins.disabled = set(dialog.disabled)
            self.modes = all_modes(self.plugins)  # プラグインのゲームモード・エンジンが増減する
            self._rebuild_mode_buttons()
            self._refresh_sides()

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        self.stop_assist(show=False)
        self.obs.stop()
        for window in self._open_simulators():
            window.close()
        super().closeEvent(event)
