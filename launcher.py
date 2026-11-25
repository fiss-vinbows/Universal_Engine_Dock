"""Universal Engine Dock の起動スクリプト。実行ファイル(PyInstaller)の入口。ソースからは `python -m uedock`(start.bat)でよい。"""

import multiprocessing

if __name__ == "__main__":
    # テトリスのパフェ探索・認識は別プロセスで動く(education/pc_process.py、recognize_process.py)。
    # exe から子プロセスを起動した時は、ここで子プロセスとしての処理に入る(画面のモジュールを読み込む前に)
    multiprocessing.freeze_support()
    from uedock.app import main

    main()
