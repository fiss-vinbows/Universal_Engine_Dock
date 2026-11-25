"""Cold Clear 2 (TBP: Tetris Bot Protocol準拠) をサブプロセスとして呼び出すクライアント。

自前の思考ルーチン(evaluator.py, solver.py)はT-spin・Perfect Clear等の
高度なテクニックを一切考慮できない設計だったため、実戦で磨かれた強豪AIである
Cold Clear 2 (https://github.com/MinusKelvin/cold-clear-2) を統合する。
Cold Clear 2はRust製で、標準入出力でJSON(1行1メッセージ)をやり取りする
TBPプロトコルを実装した別プロセスとして動作する。

TBPのboard/座標系はこのプロジェクトのBoardStateと異なる点に注意:
- TBPのboardは40行×10列で、行インデックスは「下から数えた行」(y=0が最下段)。
  BoardStateは20行×10列で、行インデックスは「上から数えた行」(row=0が最上段)。
- 配置(Placement)は、ピース種類ごとに定義された「中心からの相対オフセット」
  (data.rsのPiece::cells)を、orientationに応じて回転させ、中心座標(x,y)に
  加算することで実際の4マスが決まる。中心の定義自体がSRSのtrue rotation
  centerに基づく特殊なものなので、着地マスの計算はこのモジュール内で
  Cold Clear 2のソース(data.rs)の定義をそのまま移植して行う。
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from uedock.engine_registry import bundled_engine
from uedock.paths import app_root, resource_root
from uedock.games.tetris.engine.board_state import BoardState

# 同梱する自作AI KasugAI(TBP 互換)。配布物では engines/kasugai/ に実行ファイルと重みを置く。
# ソースから起動した時に engines/ に無ければ、並べて置いた TetrisAI のビルド結果と重みを使う
_KASUGAI_DEV = app_root().parent / "TetrisAI"
KASUGAI_EXE = bundled_engine("kasugai/tetrisai-bot.exe", _KASUGAI_DEV / "target" / "release" / "tetrisai-bot.exe")
KASUGAI_WEIGHTS = bundled_engine("kasugai/run16.toml", _KASUGAI_DEV / "config" / "weights" / "run16.toml")

# Cold Clear 2 は Universal Engine Dock には同梱しない。ここは
# cold_clear_engine() と書いた時(シミュレーターの比較とテスト)に探す場所: engines/cold-clear-2/ に置いたもの、
# 無ければ並べて置いたビルド結果
COLD_CLEAR_EXE = bundled_engine(
    "cold-clear-2/cold-clear-2.exe",
    app_root().parent / "Tetris" / "external" / "cold-clear-2" / "target" / "release" / "cold-clear-2.exe",
)
# Cold Clear 2 の初心者向けの評価設定(TSS とミニスピンの加点を外したもの。ColdClearClient.__init__ 参照)
COLD_CLEAR_CONFIG = resource_root() / "assets" / "cold_clear_beginner.json"


# 後方互換用のデフォルト値（suggest_moveのみで使用）。
THINK_SECONDS = 0.6

# Cold Clear 2からの応答をこの秒数待っても届かない場合、プロセスが
# デッドロック等で応答不能に陥ったとみなして諦める。Windowsのパイプは
# selectで待てないため、専用の読み取りスレッド+タイムアウト付きキューで
# 実現する（応答なしでブロッキング読み込みし続けると、呼び出し元の
# AssistWorkerスレッドごと永久に固まり、支援モードの終了操作すら
# 効かなくなる恐れがあるため）。
RECV_TIMEOUT_SEC = 5.0

# エンジンが終了したときに理由として表示する、標準エラーの最後の行数
STDERR_TAIL_LINES = 10

# data.rsのPiece::cells()をそのまま移植したもの。north(spawn)状態での
# 「配置の中心」から見た4マスの相対オフセット(dx, dy)。
_PIECE_CELLS: dict[str, list[tuple[int, int]]] = {
    "I": [(-1, 0), (0, 0), (1, 0), (2, 0)],
    "O": [(0, 0), (1, 0), (0, 1), (1, 1)],
    "T": [(-1, 0), (0, 0), (1, 0), (0, 1)],
    "L": [(-1, 0), (0, 0), (1, 0), (1, 1)],
    "J": [(-1, 0), (0, 0), (1, 0), (-1, 1)],
    "S": [(-1, 0), (0, 0), (0, 1), (1, 1)],
    "Z": [(-1, 1), (0, 1), (0, 0), (1, 0)],
}


def _rotate_cell(dx: int, dy: int, orientation: str) -> tuple[int, int]:
    """data.rsのRotation::rotate_cellをそのまま移植。"""
    if orientation == "north":
        return dx, dy
    if orientation == "east":
        return dy, -dx
    if orientation == "south":
        return -dx, -dy
    if orientation == "west":
        return -dy, dx
    raise ValueError(f"unknown orientation: {orientation}")


@dataclass
class ColdClearMove:
    use_hold: bool
    piece: str
    landing_cells: list[tuple[int, int]]  # BoardState座標系 (row, col)。row=0が最上段
    nodes: int
    nps: float
    # TBPのPlacement(location+spinを含む生の辞書)。advance_thinkingで
    # 「この手を指した」とCold Clear 2へ伝え直すために保持する。
    placement: dict | None = None
    # 読み筋(このフォーク独自のTBP拡張 "plan")。この手に続く2手目以降の
    # (ミノ種, 着地マス)。次のミノが確定している範囲で最大4手。ライン消去の
    # 影響は含まない(各手は「その手を指す直前の盤面」での着地マス)ため、
    # 画面へ表示できる範囲は呼び出し側が判断する(app._plan_steps_on_screen参照)。
    plan: list[tuple[str, list[tuple[int, int]]]] | None = None
    # 狙い筋(自作エンジンの TBP 拡張 "aim"。例: "中あけREN 撃ち中\n毎手1ライン消す(残り7行)")。
    # 情報パネルの「狙い」に出す。Cold Clear 2 は返さないので None
    aim: str | None = None
    # C4W で続けられる REN の見込み(自作エンジンの TBP 拡張 "c4w_ren")。C4W でなければ 0
    c4w_ren: int = 0
    # Downstack(掘り)で続けられる REN の見込みと、見えているミノをすべて使って消し切れたか
    downstack_ren: int = 0
    downstack_ren_open: bool = False
    # 情報パネルの「最大REN」
    # (自作エンジンの TBP 拡張 "ren_target"。REN をつなげる意思が無ければ 0)。送らないエンジンでは None
    ren_target: int | None = None


def location_to_board_cells(piece: str, orientation: str, x: int, y: int, board_height: int) -> list[tuple[int, int]]:
    """TBPのPieceLocation(中心x,y + orientation)を、BoardState座標系(row,col)の4マスに変換する。

    TBPのyは「下から数えた行」、BoardStateのrowは「上から数えた行」なので、
    row = (board_height - 1) - actual_y で反転させる。
    """
    cells = []
    for dx, dy in _PIECE_CELLS[piece]:
        rdx, rdy = _rotate_cell(dx, dy, orientation)
        actual_x = x + rdx
        actual_y = y + rdy
        row = (board_height - 1) - actual_y
        col = actual_x
        cells.append((row, col))
    return cells


def board_state_to_tbp_board(board: BoardState, tbp_rows: int = 40) -> list[list[str | None]]:
    """BoardState(row=0が最上段)をTBPのboard形式(y=0が最下段)に変換する。"""
    tbp_board = [[None] * board.width for _ in range(tbp_rows)]
    for row in range(board.height):
        y = (board.height - 1) - row
        if 0 <= y < tbp_rows:
            for col in range(board.width):
                cell = board.grid[row][col]
                # 種類不明のブロック(UNKNOWN_BLOCK)も、AIには「何かある」
                # としてだけ伝える。着地済み盤面は占有情報だけあれば足りる。
                tbp_board[y][col] = "G" if cell in ("GARBAGE", "UNKNOWN") else cell
    return tbp_board


def engine_exe() -> Path:
    """エンジンを指定しない時に使う思考エンジンの実行ファイル(同梱の KasugAI)。"""
    return KASUGAI_EXE


def cold_clear_engine() -> tuple[Path, Path | None]:
    """Cold Clear 2 の(実行ファイル, 初心者向けの重み)。"""
    return COLD_CLEAR_EXE, (COLD_CLEAR_CONFIG if COLD_CLEAR_CONFIG.exists() else None)


def engine_weights() -> Path | None:
    """エンジンを指定しない時に --config で渡す評価の重み(同梱の KasugAI の重み。無ければ渡さない)。"""
    return KASUGAI_WEIGHTS if KASUGAI_WEIGHTS.exists() else None


# 起動直後のやり取りができない時に添える案内
NOT_TBP_HINT = ("\n\nTBP 互換(標準入出力で盤面を受け取り手を返す形)の思考エンジンではない可能性があります。"
                "独立して動くプログラム(自分の画面を持ち、ゲームを直接操作するもの)は登録できません。")


class ColdClearClient:
    """Cold Clear 2プロセスを1つ起動して使い回すクライアント。

    支援モード開始時にstart()を呼び、終了時にclose()を呼ぶこと。
    """

    label = "思考エンジン"  # エラーの文に出す名前(起動する時に実行ファイルの名前を入れる)

    def __init__(self, engine: tuple[Path, Path | None] | None = None, opening: str | None = None,
                 extra_args: list[str] | None = None) -> None:
        """engine: 使う(実行ファイル, 重み)。省略すると同梱の KasugAI。extra_args: 登録したエンジンの起動引数。"""
        self._board_height = 20
        # 起動した時の指定。応答しなくなって作り直す時(AssistWorker._try_restart_cold_clear)に同じものを使う
        self.engine = engine
        self.opening = opening
        self.extra_args = list(extra_args or [])
        exe = engine_exe() if engine is None else engine[0]
        if not exe.exists():
            raise FileNotFoundError(
                f"思考エンジンの実行ファイルが見つかりません: {exe}\n"
                "同梱の KasugAI は engines/kasugai/ に、ほかのエンジンはメイン画面の「エンジンの登録」で確かめてください。"
            )
        # start.batをpythonw(コンソールを持たない実行形式)で起動した場合、
        # 親プロセスにコンソールが存在しない状態でこの通常のコンソール
        # アプリケーション(cold-clear-2.exe)をsubprocess.Popenすることになる。
        # creationflagsを指定しないと、Windows側の新規コンソール確保が
        # 不安定になり、プロセス自体は起動するもののstdin/stdoutのパイプ
        # 通信が機能せず、起動直後のinfoメッセージ待ちで永久にタイムアウト
        # する不具合が実機で確認された（TimeoutError: Cold Clear 2から
        # 5.0秒間応答がありませんでした）。CREATE_NO_WINDOWを明示することで、
        # コンソールウィンドウを一切生成させず、パイプだけを確実に使わせる。
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        # 【初心者向けの評価設定】config/cold_clear_beginner.json。
        # 「難しい操作(潜り込み)を避ける」ためソフトドロップへの
        # 罰則を大きくしたが、それではTスピンも出なくなる。利用者の方針
        # 「TSD/TSTは初心者でも身につけるべき」に従い、既定の
        # 評価からTSS(1ライン)とミニスピンの加点だけを外した設定にした。
        # 自己対戦480手で潜り込み(スピン以外)1件・TSD以上39件。
        # 設定ファイルが無ければ既定で起動する。
        # engine.jsonで重みのファイル("config")を指定したときはそちらを渡す。
        # 指定したのに無いときは、黙って既定の重みで動かさずエラーにする(別の重みで試すことになるため)。
        # エラーの文に出す名前
        self.label = f"思考エンジン({exe.stem})"
        args = [str(exe)]
        weights = engine_weights() if engine is None else engine[1]
        if weights is not None:
            if not weights.exists():
                raise FileNotFoundError(
                    f"評価の重みのファイルが見つかりません: {weights}\nメイン画面の「エンジンの登録」で確かめてください。"
                )
            args += ["--config", str(weights)]
        if extra_args:
            args += list(extra_args)
        # 開幕に目指すもの。KasugAI だけが受け付ける
        if opening and "tetrisai-bot" in exe.name:
            args += ["--opening", opening]
        # エンジンの標準エラーも受け取る。自作エンジンは重みに問題があると理由を標準エラーに書いて
        # 終了するが、pythonw(コンソールなし)ではどこにも出ず「プロセスが終了しました」としか分からなかった。
        # エンジンは UTF-8 で書くので、文字コードも UTF-8 にする(既定の cp932 だと日本語が化ける)。
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creation_flags,
        )
        # stdoutの読み込みを専用スレッドに任せ、キュー経由で受け取ることで、
        # _recv()にタイムアウトを持たせられるようにする。Windowsのパイプは
        # selectで待てないため、これが一番シンプルな実現方法。
        self._read_queue: queue.Queue[str | None] = queue.Queue()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        # 標準エラーは読み続けないとパイプが詰まってエンジンが止まるので、別スレッドで最後の数行だけ残す
        self._stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stderr_thread.start()

        # infoは起動直後にエンジンから自発的に送られてくる。来ない・形が違う時は TBP 互換でない
        try:
            info = self._recv()
        except (RuntimeError, ValueError) as exc:
            raise RuntimeError(f"{exc}{NOT_TBP_HINT}") from exc
        if info.get("type") != "info":
            raise RuntimeError(f"{self.label}から予期しない初期メッセージ: {info}{NOT_TBP_HINT}")
        self._send({"type": "rules"})
        ready = self._recv()
        if ready.get("type") != "ready":
            raise RuntimeError(f"{self.label}がルールを受理しませんでした: {ready}")

    def _reader_loop(self) -> None:
        assert self._proc.stdout is not None
        try:
            for line in self._proc.stdout:
                self._read_queue.put(line)
        except (OSError, ValueError):
            # プロセス終了後にパイプが閉じられた等。下のNone投入で
            # _recv側にEOF相当として伝える。
            pass
        self._read_queue.put(None)

    def _stderr_loop(self) -> None:
        assert self._proc.stderr is not None
        # 自作のエンジンは UTF-8 で書くが、ほかのエンジン(.NET のプログラムなど)は Windows の既定(cp932)で書くことがある。
        # 行ごとに UTF-8 で読めなければ cp932 で読む
        stream = getattr(self._proc.stderr, "buffer", self._proc.stderr)
        try:
            for line in stream:
                if isinstance(line, bytes):
                    try:
                        line = line.decode("utf-8")
                    except UnicodeDecodeError:
                        line = line.decode("cp932", errors="replace")
                self._stderr_tail.append(line.rstrip())
        except (OSError, ValueError):
            pass

    def _exit_reason(self) -> str:
        """エンジンが終了したときに、標準エラーの最後の数行(理由)を添える文。"""
        self._stderr_thread.join(timeout=1.0)  # 終了直前に書いた行を読み終えるのを待つ
        tail = [line for line in self._stderr_tail if line]
        return "\nエンジンの出力:\n" + "\n".join(tail) if tail else ""

    def _drain_pending(self) -> None:
        """未読の応答を捨てる。新しい局面を渡す直前に呼ぶ。

        【なぜ必要か】start_thinkingは送信するだけで応答を読まない。一方
        poll_suggestionは「suggestを送って1行読む」ため、前の局面に対する
        応答が読まれないまま残っていると、次に読んだ1行がその古い応答に
        なる。要求と応答の対応が1つずつずれていき、ずれが溜まると最後は
        読み取りがブロックして応答待ちのタイムアウトになる。

        実機で、Cold Clear 2が5秒間応答しなくなりワーカースレッドが停止する
        事象が発生した。新しい局面を
        渡す時点で、それ以前の応答はすべて古い局面に対するものなので捨てる。
        """
        while True:
            try:
                line = self._read_queue.get_nowait()
            except queue.Empty:
                return
            if line is None:
                # プロセス終了(EOF)の合図。捨てると異常終了に気づけなくなる
                # ので必ず戻しておく。
                self._read_queue.put(None)
                return

    def _send(self, msg: dict) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def _recv(self, timeout: float = RECV_TIMEOUT_SEC) -> dict:
        try:
            line = self._read_queue.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(
                f"{self.label}から{timeout}秒間応答がありませんでした（応答不能に陥った可能性）"
            ) from None
        if line is None:
            raise RuntimeError(f"{self.label}のプロセスが終了しました（予期しないクラッシュの可能性）" + self._exit_reason())
        return json.loads(line)

    def start_thinking(
        self,
        board: BoardState,
        current_piece: str,
        hold_piece: str | None,
        next_queue: list[str],
        disallow_hold: bool = False,
        above: tuple = (),
        combo: int = 0,
        back_to_back: bool = False,
    ) -> None:
        """新しい局面を伝え、バックグラウンドでの継続思考を開始する。

        combo は今続いている REN(続けて消した回数)、back_to_back は B2B が続いているか。
        常に 0・False を送っていたので、考え直すたびに AI は REN と B2B を忘れていた。

        Cold Clear 2は指定した局面から時間の許す限り探索を深め続ける設計
        （実測でstart後にsuggestを繰り返し呼ぶとnodesが単調に増加し続ける
        ことを確認済み）。以前は「新しいミノがスポーンするたびに毎回start
        して短いsleep後すぐsuggestする」実装だったため、この継続思考の
        恩恵を全く受けられず、パーフェクトクリアのような複数手先の計画を
        要する手を、探索が浅いまま見送ってしまう問題があった（実機で
        「人間が見てもわかる局面でパーフェクトにならない手を提示した」
        として報告された）。startは新しいミノがスポーンした時だけ呼び、
        同じミノを操作している間はpoll_suggestionだけを繰り返し呼ぶことで、
        操作中の時間をそのまま思考時間として使えるようにする。

        disallow_hold=Trueの場合、ホールドしても実質何も変わらないよう
        hold_pieceの代わりにcurrent_pieceを渡す（ホールドの中身をcurrent_pieceと
        同じに偽装し、スワップする動機自体をなくす）。直前にホールド操作を
        行ったばかりの時に、tickをまたいでホールドを行ったり来たりする振動
        （AをホールドしてBを使う→次はBをホールドしてAを使う、を繰り返す）を
        防ぐために使う。TBPにホールド自体を禁止するオプションはないため、
        この偽装で代用している。
        """
        # 盤面認識のタイミング次第で、まれに「本来ならゲーム側で即座に消去
        # されているはずの、完全に埋まった行」が紛れ込むことがある
        # （ライン消去アニメーション中に認識した場合など）。この状態のまま
        # Cold Clear 2に渡すと、内部の評価ロジック(normal_clears配列への
        # インデックスアクセス)が範囲外になりRustパニックでプロセスごと
        # クラッシュすることを実際に確認した。事前にclear_lines()を通して
        # おくことで、そもそもそのような状態を渡さないようにする。
        board, _ = board.clear_lines()
        tbp_board = board_state_to_tbp_board(board)
        # 画面に見えない天井より上の行(above[0] が0行目のすぐ上。各行は列ごとの占有)も伝える
        for i, row in enumerate(above):
            tbp_board[board.height + i] = ["G" if filled else None for filled in row]
        effective_hold = current_piece if disallow_hold else hold_piece
        self._drain_pending()
        queue = [current_piece, *next_queue]
        self._board_height = board.height
        self._send(
            {
                "type": "start",
                "hold": effective_hold,
                "queue": queue,
                "combo": combo,
                "back_to_back": back_to_back,
                "board": tbp_board,
            }
        )

    def advance_thinking(self, placement: dict, new_pieces: list[str]) -> None:
        """「提示した手をそのまま指した」と伝え、探索木を保ったまま次の手番へ進む。

        TBPのplay(指した手)とnew_piece(新しく見えたNEXT)を送る。Cold Clear 2
        は内部の探索木(DAG)をその枝に進めるだけで捨てない(freestyle.rsの
        advance→dag.advance)ため、前の手番中に深めた探索がそのまま次の手番の
        初回結果に使われる。

        【なぜ必要か】表示は「置くまで変更しない」ため最初に届いた結果
        (実測で中央値約1.3万ノード)で固定される一方、毎手番startで局面を
        渡し直すと、その手番中に深めた探索(最大17万ノード)は全部捨てていた。
        利用者が提示どおりに置いた場合に限り、木を引き継いで質を上げる。
        提示と違う場所に置いた・盤面が一致しない場合は呼び出し側が従来どおり
        start_thinkingでやり直す。

        呼び出し側の責任: placementは実際に指された手(suggestで受け取った
        movesの要素そのもの)であること、new_piecesは
        Cold Clear 2がまだ知らないNEXTを見えた順に並べたものであること。
        """
        self._drain_pending()
        self._send({"type": "play", "move": placement})
        for piece in new_pieces:
            self._send({"type": "new_piece", "piece": piece})

    def send_battle(self, incoming_lines: int, opponent_board: BoardState | None = None, opponent_incoming: int = 0) -> None:
        """予告(受けているおじゃまの行数)と相手の盤面を伝える(自作エンジンの TBP 拡張 "battle")。

        相殺をわざと外してカウンターする判断には、予告と相手の盤面が要る。
        支援モードでは相殺外しを使わない。Cold Clear 2 は知らないメッセージを無視する(TBP の約束)。
        自作エンジンは start のたびにこの情報を消すので、考え始めた後と手を伝えた後に送り直す。
        """
        opponent = None
        if opponent_board is not None:
            opponent = {
                "board": board_state_to_tbp_board(opponent_board),
                "incoming": opponent_incoming,
                "gauge": 0,
                "back_to_back": False,
            }
        self._send({
            "type": "battle",
            "incoming": [{"lines": incoming_lines, "delay": 0}] if incoming_lines > 0 else [],
            "gauge": 0,
            "opponent": opponent,
        })

    def add_new_pieces(self, new_pieces: list[str]) -> None:
        """advance_thinking後に読めるようになったNEXTを追加で伝える。"""
        for piece in new_pieces:
            self._send({"type": "new_piece", "piece": piece})

    def poll_suggestion(self, current_piece: str) -> ColdClearMove | None:
        """start_thinkingで設定した局面について、現時点までの最善手を即座に問い合わせる。

        待機は一切行わない。start_thinking直後などまだ何も計算できていない
        場合は、Cold Clear 2がmovesを空リストで返すため、その場合はNoneを
        返す（呼び出し側は前回有効だった提案をそのまま表示し続ければよい）。
        """
        self._send({"type": "suggest"})
        result = self._recv()
        if result.get("type") != "suggestion":
            return None
        moves = result.get("moves", [])
        if not moves:
            return None
        move = moves[0]
        location = move["location"]
        piece = location["type"]
        use_hold = piece != current_piece

        cells = location_to_board_cells(
            piece, location["orientation"], location["x"], location["y"], self._board_height
        )
        move_info = result.get("move_info", {})
        plan: list[tuple[str, list[tuple[int, int]]]] = []
        for step in result.get("plan", [])[1:]:
            loc = step["location"]
            plan.append(
                (
                    loc["type"],
                    location_to_board_cells(
                        loc["type"], loc["orientation"], loc["x"], loc["y"], self._board_height
                    ),
                )
            )
        return ColdClearMove(
            use_hold=use_hold,
            piece=piece,
            landing_cells=cells,
            nodes=move_info.get("nodes", 0),
            nps=move_info.get("nps", 0.0),
            placement=move,
            plan=plan,
            aim=result["aim"] if isinstance(result.get("aim"), str) and result["aim"] else None,
            c4w_ren=result["c4w_ren"] if isinstance(result.get("c4w_ren"), int) else 0,
            downstack_ren=result["downstack_ren"] if isinstance(result.get("downstack_ren"), int) else 0,
            downstack_ren_open=result.get("downstack_ren_open") is True,
            ren_target=result["ren_target"] if isinstance(result.get("ren_target"), int) else None,
        )

    def suggest_move(
        self,
        board: BoardState,
        current_piece: str,
        hold_piece: str | None,
        next_queue: list[str],
        think_seconds: float = THINK_SECONDS,
        disallow_hold: bool = False,
    ) -> ColdClearMove | None:
        """後方互換用のヘルパー: start_thinking + 待機 + poll_suggestionをまとめて行う。

        テストや簡易的な検証用。実運用（AssistWorker）はstart_thinkingと
        poll_suggestionを直接使い、待機なしで段階的に提案を更新する。
        """
        self.start_thinking(board, current_piece, hold_piece, next_queue, disallow_hold=disallow_hold)
        time.sleep(think_seconds)
        return self.poll_suggestion(current_piece)

    def close(self) -> None:
        try:
            self._send({"type": "quit"})
        except Exception:  # noqa: BLE001 - 終了処理は失敗しても無視してよい
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                self._proc.kill()
                self._proc.wait()
            except OSError:
                pass
        except OSError:
            pass
        # Popenのstdin/stdoutパイプはterminate/waitだけでは閉じられず、
        # ResourceWarning(unclosed file)の原因になる。明示的に閉じる。
        # 【必ず例外を飲み込むこと】プロセスが既に異常終了していると、
        # パイプの後始末自体がOSError(Errno 22)を投げる。実機で、
        # Cold Clear 2が応答不能になった後の後始末でこれが発生し、支援モードの
        # 停止処理そのものが例外で中断して復帰できなくなった
        # 後始末は失敗しても
        # 呼び出し側の流れを止めてはならない。
        for pipe in (self._proc.stdin, self._proc.stdout):
            if pipe is None:
                continue
            try:
                pipe.close()
            except OSError:
                pass
