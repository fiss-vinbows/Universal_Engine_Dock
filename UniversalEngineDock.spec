# -*- mode: python ; coding: utf-8 -*-
# Universal Engine Dock の PyInstaller 設定。
# ビルド: tools/build_exe.py を実行する(dist/UniversalEngineDock/ に UniversalEngineDock.exe と必要ファイルを揃える)。

block_cipher = None

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=[],
    # 読み取り専用データ。認識の見本画像は Path(__file__) と resource_root() の両方から読むので、元と同じ場所に置く
    datas=[
        ("uedock/games/puyo/vision/templates/*.png", "uedock/games/puyo/vision/templates"),
        ("uedock/games/tetris/vision/templates/*.png", "uedock/games/tetris/vision/templates"),
        ("assets/opening_6moves.json", "assets"),
        ("assets/cold_clear_beginner.json", "assets"),
        ("assets/icon.ico", "assets"),
    ],
    # プラグイン(exe の隣の plugins/)が使う本体のモジュール。exe の中から import できるように入れておく
    hiddenimports=["uedock.plugin_api", "uedock.game_mode", "uedock.extension"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "pandas"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="UniversalEngineDock",
    icon="assets/icon.ico",
    version="build/version_info.txt",  # tools/build_exe.py が書く
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # コンソールを出さない(start.bat の pythonw と同じ)
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="UniversalEngineDock",
)
