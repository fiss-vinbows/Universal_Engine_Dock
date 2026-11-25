"""OBS 出力の設定のしかた(メイン画面の「OBS の設定のしかた…」)。手順・コピーできるアドレス・つながっているか・位置合わせ用の枠。

OBS 側でする作業を3つの手順にまとめ、アドレスはボタンでコピーできるようにした(uedock/obs_output.py)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6 import QtCore, QtWidgets

from ..obs_output import FRAME_SIZE, letterbox
from .toggle_switch import ToggleSwitch

if TYPE_CHECKING:
    from ..obs_output import ObsOutput

REFRESH_MS = 500  # つながっているかの表示を確かめ直す間隔


def steps_text() -> str:
    w, h = FRAME_SIZE
    return (
        "<ol style='margin-left: -20px;'>"
        "<li>OBS の「ソース」の <b>＋</b> から <b>ブラウザ</b> を選び、名前を付けて OK を押します。</li>"
        f"<li>「URL」に下のアドレスを貼り、<b>幅 {w}</b>・<b>高さ {h}</b> にして OK を押します。</li>"
        "<li>追加したソースを「ソース」の一覧でゲームの取り込みより<b>上</b>に置き、選んだ状態で "
        "<b>Ctrl+F</b>(画面に合わせる)を押します。ゲームの取り込みも、同じく Ctrl+F で画面に合わせておきます。</li>"
        "</ol>"
    )


def tips_text(client_size: tuple[int, int] | None) -> str:
    """合わない時の注意。ゲームのウィンドウが 16:9 でなければ、黒帯の分を切り取る量を書く。"""
    tips = [
        "OBS の「設定 → 映像 → 基本(キャンバス)解像度」は 16:9(1920×1080 など)にしてください。",
        "ゲームの取り込みは、ゲームの描画領域だけにしてください(ウィンドウキャプチャなら「クライアント領域」をオン)。",
    ]
    if client_size is not None:
        side, top = letterbox(client_size)
        if side or top:
            where = f"左右を {side}px ずつ" if side else f"上下を {top}px ずつ"
            tips.append(
                f"今のゲームのウィンドウ({client_size[0]}×{client_size[1]})は 16:9 ではないので、黒帯があります。"
                f"ゲームの取り込みの{where}切り取ってから(Alt を押しながら端をドラッグ)、Ctrl+F で画面に合わせてください。"
            )
    tips.append("出るのは支援モードの間です。合っているかは、下の「位置合わせ用の枠を出す」で確かめられます。")
    return "<ul style='margin-left: -20px;'>" + "".join(f"<li>{t}</li>" for t in tips) + "</ul>"


class ObsGuideDialog(QtWidgets.QDialog):
    def __init__(self, output: "ObsOutput", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.output = output
        self.setWindowTitle("OBS の設定のしかた")
        self.setMinimumWidth(560)
        layout = QtWidgets.QVBoxLayout(self)

        steps = QtWidgets.QLabel(steps_text())
        steps.setWordWrap(True)
        layout.addWidget(steps)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("アドレス:"))
        self.url_edit = QtWidgets.QLineEdit(output.url)
        self.url_edit.setReadOnly(True)
        row.addWidget(self.url_edit, 1)
        self.copy_btn = QtWidgets.QPushButton("コピー")
        self.copy_btn.clicked.connect(self._copy)
        row.addWidget(self.copy_btn)
        layout.addLayout(row)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        window = output.game_window()
        self.tips = QtWidgets.QLabel(tips_text((window.width, window.height) if window is not None else None))
        self.tips.setWordWrap(True)
        layout.addWidget(self.tips)

        self.guide_switch = ToggleSwitch("位置合わせ用の枠を出す(OBS の画面にだけ出ます)")
        self.guide_switch.setChecked(output.guide)
        self.guide_switch.toggled.connect(output.set_guide)
        layout.addWidget(self.guide_switch)

        layout.addStretch(1)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        close_btn = QtWidgets.QPushButton("閉じる")
        close_btn.clicked.connect(self.reject)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    def _copy(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self.url_edit.text())
        self.copy_btn.setText("コピーしました")
        QtCore.QTimer.singleShot(1500, lambda: self.copy_btn.setText("コピー"))

    def _refresh(self) -> None:
        self.url_edit.setText(self.output.url)
        text = self.output.status_text() or "「OBS に出す」がオフです"
        color = "#1e8449" if self.output.connected() else ("#c0392b" if self.output.error else "#7f6000")
        self.status_label.setText(f"<span style='color: {color}; font-weight: bold;'>{text}</span>")

    def done(self, result: int) -> None:  # noqa: D401
        # 枠は確かめる時だけのものなので、閉じたら消す
        self._timer.stop()
        self.output.set_guide(False)
        super().done(result)
