"""UniversalEngineDock.exe をビルドして dist/UniversalEngineDock/ に配布物一式を揃え、dist/UniversalEngineDock-<版>.zip にまとめる。

使い方: venv\\Scripts\\python tools\\build_exe.py [版(例 0.1.0)]
前提: venv に pyinstaller が入っていること。同梱する思考エンジンなどが、並べて置いたリポジトリでビルド済みであること
    - KasugAI:  ../TetrisAI/target/release/tetrisai-bot.exe と ../TetrisAI/config/weights/ の run16.toml・styles.toml・b2b.toml
    - YamaNEKO: ../Puyo-YamaNEKO/engine/bin/yamaneko-bridge.exe と ../Puyo-YamaNEKO/weights/ の重み
    - 継続パフェの計算(pc-odds): ../Tetris/native/pc-odds/target/release/pc-odds.exe
ama と Cold Clear 2 は同梱しない。

セキュリティソフトの誤検知を減らすため、PyInstaller の起動部分(ブートローダー)は手元でビルドしたものを使う。
PyInstaller に付属の起動部分は多くの exe に使われ、その中にマルウェアもあるので、同じ形として疑われやすい。
作り方(Visual Studio の C++ が要る。PyInstaller を入れ直したら作り直す):
    PyPI にある同じ版の pyinstaller の sdist を展開し、bootloader フォルダで
    python waf distclean configure --target-arch=64bit build_release build_releasew
    できた build/release/run.exe・build/releasew/runw.exe を venv/Lib/site-packages/PyInstaller/bootloader/Windows-64bit-intel/ に写す
"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT.parent  # 並べて置いたリポジトリの場所
DIST = ROOT / "dist" / "UniversalEngineDock"
SITE_PACKAGES = ROOT / "venv" / "Lib" / "site-packages"

# 同梱する思考エンジンなど: (並べて置いたリポジトリのファイル, 配布物の engines/ からの場所)
ENGINE_FILES: list[tuple[Path, str]] = [
    (WORK / "TetrisAI" / "target" / "release" / "tetrisai-bot.exe", "kasugai/tetrisai-bot.exe"),
    (WORK / "TetrisAI" / "config" / "weights" / "run16.toml", "kasugai/run16.toml"),
    # 打ち方の表(--opening の「style=名前」で、重みと同じフォルダから読む)と、表が指す重み。
    # 表が無いと、style= を渡された KasugAI は起動してすぐ終わる
    (WORK / "TetrisAI" / "config" / "weights" / "styles.toml", "kasugai/styles.toml"),
    (WORK / "TetrisAI" / "config" / "weights" / "b2b.toml", "kasugai/b2b.toml"),
    (WORK / "Tetris" / "native" / "pc-odds" / "target" / "release" / "pc-odds.exe", "pc-odds/pc-odds.exe"),
    # YamaNEKO は自分の場所から ../../weights/ を読むので、元と同じ並びで置く。読むのはこの4つだけ
    (WORK / "Puyo-YamaNEKO" / "engine" / "bin" / "yamaneko-bridge.exe", "yamaneko/engine/bin/yamaneko-bridge.exe"),
    *((WORK / "Puyo-YamaNEKO" / "weights" / name, f"yamaneko/weights/{name}")
      for name in ("c4_free.json", "learned_remain_v1.json", "gtr_forms.json", "opening_book.json")),
]
# 実行ファイルに入れる説明(エクスプローラーのプロパティに出る)
ENGINE_DESCRIPTIONS = {
    "tetrisai-bot.exe": "KasugAI (Tetris engine, TBP)",
    "pc-odds.exe": "pc-odds (perfect clear calculator)",
    "yamaneko-bridge.exe": "YamaNEKO (Puyo engine)",
}
AUTHOR = "fiss-vinbows"
PRODUCT = "Universal Engine Dock"
VERSION_FILE = ROOT / "build" / "version_info.txt"  # spec が読む

# 同梱する思考エンジンなどのライセンス文: (並べて置いたリポジトリのファイル, licenses/ での名前)
ENGINE_LICENSES: list[tuple[Path, str]] = [
    (WORK / "TetrisAI" / "LICENSE", "KasugAI-LICENSE.txt"),
    (WORK / "Tetris" / "LICENSE", "pc-odds-LICENSE.txt"),
    (WORK / "Puyo-YamaNEKO" / "LICENSE", "YamaNEKO-LICENSE.txt"),
    # YamaNEKO の盤面処理は ama の core を使う(MIT。許諾文の同梱が条件)
    (WORK / "Puyo-YamaNEKO" / "third_party" / "ama_core" / "LICENSE", "YamaNEKO-ama-LICENSE.txt"),
]
# KasugAI の実行ファイルに入る Rust のライブラリ(TetrisAI/Cargo.lock の版のライセンス文を集める)
KASUGAI_LOCK = WORK / "TetrisAI" / "Cargo.lock"
KASUGAI_CRATES = ["serde", "serde_core", "serde_json", "serde_spanned", "itoa", "memchr", "zmij", "toml", "toml_datetime", "toml_edit",
                  "toml_write", "winnow", "indexmap", "hashbrown", "equivalent"]
CARGO_SOURCES = Path.home() / ".cargo" / "registry" / "src"

# exe に入れるパッケージと、そのライセンス文の入った dist-info の名前(先頭一致)
BUNDLED_PACKAGES = ["pyqt6-", "pyqt6_qt6-", "pyqt6_sip-", "numpy-", "opencv_python-", "mss-", "pillow-"]


def check_engines() -> None:
    """同梱するファイルが揃っているか、ビルドの前に確かめる(PyInstaller は時間がかかるので先に止める)。"""
    missing = [str(src) for src, _ in ENGINE_FILES + ENGINE_LICENSES if not src.exists()]
    if missing:
        sys.exit("同梱する思考エンジンなどが見つかりません(並べて置いたリポジトリでビルドしてください):\n  " + "\n  ".join(missing))


def check_bootloader() -> None:
    """PyInstaller の起動部分が、付属のままではなく手元でビルドしたものか確かめる(誤検知を減らすため。冒頭の説明)。"""
    name = "PyInstaller/bootloader/Windows-64bit-intel/runw.exe"
    record = next(SITE_PACKAGES.glob("pyinstaller-[0-9]*.dist-info")) / "RECORD"
    with record.open(encoding="utf-8", newline="") as f:
        shipped = next(row[1] for row in csv.reader(f) if row[0] == name)
    digest = base64.urlsafe_b64encode(hashlib.sha256((SITE_PACKAGES / name).read_bytes()).digest()).decode().rstrip("=")
    if shipped == f"sha256={digest}":
        print("注意: PyInstaller に付属の起動部分を使います。セキュリティソフトに疑われやすいので、手元でビルドしたものに替えてください(冒頭の説明)")


def version_info(version: str, description: str, filename: str, product: str):  # noqa: ANN201
    """エクスプローラーのプロパティに出る製品名・版など。何も書かれていない exe は疑われやすい。"""
    from PyInstaller.utils.win32 import versioninfo as vi

    digits = [int(n) for n in re.findall(r"\d+", version)[:4]]
    numbers = tuple(digits + [0] * (4 - len(digits)))
    strings = [("CompanyName", AUTHOR), ("FileDescription", description), ("FileVersion", version), ("InternalName", filename),
               ("LegalCopyright", f"Copyright (C) 2026 {AUTHOR}"), ("OriginalFilename", filename), ("ProductName", product),
               ("ProductVersion", version)]
    return vi.VSVersionInfo(
        ffi=vi.FixedFileInfo(filevers=numbers, prodvers=numbers, mask=0x3F, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
        kids=[vi.StringFileInfo([vi.StringTable("040904B0", [vi.StringStruct(k, v) for k, v in strings])]),
              vi.VarFileInfo([vi.VarStruct("Translation", [0x0409, 1200])])],
    )


def copy_engines(target: Path, version: str) -> None:
    from PyInstaller.utils.win32 import versioninfo, winutils

    if target.exists():
        shutil.rmtree(target)
    for src, rel in ENGINE_FILES:
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.suffix == ".exe":
            shutil.copy2(src, dest)
            mask_home_path(dest)
            blank_embedded_notes(dest)
            info = version_info(version, ENGINE_DESCRIPTIONS[dest.name], dest.name, PRODUCT)
            versioninfo.write_version_info_to_executable(str(dest), info)
            winutils.update_exe_pe_checksum(str(dest))  # 中身を変えたので合わせる(合わないと壊れた exe とみなされうる)
        else:
            dest.write_bytes(without_notes(src))


def without_notes(src: Path) -> bytes:
    """重み・設定ファイルから、開発中の覚え書き(コメント)を除いたもの。値は変えない。"""
    text = src.read_text(encoding="utf-8-sig")
    if src.suffix == ".json":
        data = {k: v for k, v in json.loads(text).items() if k != "note" and not k.startswith("_comment")}
        return json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
    stripped = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    stripped = re.sub(r"[ \t]+#[^\"'\n]*$", "", stripped, flags=re.M) + "\n"
    if tomllib.loads(stripped) != tomllib.loads(text):
        sys.exit(f"コメントを除くと値が変わります: {src}")
    return stripped.encode("utf-8")


def blank_embedded_notes(exe: Path) -> None:
    """実行ファイルに埋め込まれた設定ファイルの、日本語のコメント行(開発中の覚え書き)を同じ長さの空白にする。"""
    data = exe.read_bytes()

    def blank(m: re.Match[bytes]) -> bytes:
        return m.group(1) + b" " * len(m.group(2)) if any(b >= 0x80 for b in m.group(2)) else m.group(0)

    # コメント行が続くので、行末の改行は消費しない
    exe.write_bytes(re.sub(rb"((?<=\n)# ?|(?<=\x00)# )([^\r\n\x00]+)(?=\r?\n)", blank, data))


def mask_home_path(exe: Path) -> None:
    """実行ファイルに残った、ビルドした人のユーザーフォルダの名前(Rust のエラー位置の文字列)を、同じ長さの文字で伏せる。"""
    data = exe.read_bytes()
    for sep in (b"\\", b"/"):
        name = Path.home().name.encode()
        data = data.replace(b"Users" + sep + name + sep, b"Users" + sep + b"_" * len(name) + sep)
    exe.write_bytes(data)


def copy_distributable_plugins(source: Path, target: Path) -> list[str]:
    """配布してよいプラグイン(distributable=True)だけを写す。フォルダの中に1つでも配布しないものがあればフォルダごと写さない。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from uedock.plugin_loader import discover_folders

    if target.exists():
        shutil.rmtree(target)  # 前回の配布物に残った(今は配布しない)プラグインを消す
    found, errors = discover_folders(source)
    if errors:
        sys.exit(f"読み込めないプラグインがあります: {errors}")
    by_folder: dict[Path, list] = {}
    for folder, plugin in found:
        by_folder.setdefault(folder, []).append(plugin)
    copied = []
    for folder, plugins in sorted(by_folder.items()):
        if all(p.distributable for p in plugins):
            shutil.copytree(folder, target / folder.name, ignore=shutil.ignore_patterns("__pycache__"))
            copied += [p.id for p in plugins]
    print(f"配布するプラグイン: {copied or 'なし'}")
    return copied


def copy_licenses(target: Path) -> None:
    """同梱するもののライセンス文を licenses/ に写す(MIT・BSD・Apache は許諾文の同梱、GPL・LGPL は全文の同梱が条件)。"""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    # PyInstaller が同梱する Python 本体(PSF License)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.exists():
        sys.exit(f"Python のライセンス文が見つかりません: {python_license}")
    shutil.copy2(python_license, target / f"python-{sys.version_info.major}.{sys.version_info.minor}-LICENSE.txt")
    for prefix in BUNDLED_PACKAGES:
        infos = [d for d in SITE_PACKAGES.iterdir() if d.name.lower().startswith(prefix) and d.name.endswith(".dist-info")]
        if not infos:
            sys.exit(f"ライセンス文が見つかりません: {prefix}")
        info = infos[0]
        name = info.name[: -len(".dist-info")]
        files = [f for f in info.rglob("*") if f.is_file() and any(k in f.name.upper() for k in ("LICEN", "COPYING", "NOTICE"))]
        if not files:
            sys.exit(f"ライセンス文が見つかりません: {info}")
        for f in files:
            # 同梱の部品ごとに同じ名前(LICENSE.md など)があるので、dist-info からの階層を名前に残す
            rel = f.relative_to(info).as_posix().removeprefix("licenses/").replace("/", "_")
            shutil.copy2(f, target / f"{name}-{rel}")


def copy_engine_licenses(target: Path) -> None:
    """同梱する思考エンジンと、その中に入る Rust のライブラリのライセンス文を licenses/ に写す。"""
    for src, name in ENGINE_LICENSES:
        shutil.copy2(src, target / name)
    versions = {p["name"]: p["version"] for p in tomllib.loads(KASUGAI_LOCK.read_text(encoding="utf-8"))["package"]}
    for crate in KASUGAI_CRATES:
        folders = list(CARGO_SOURCES.glob(f"*/{crate}-{versions[crate]}"))
        files = [f for d in folders for f in sorted(d.iterdir()) if f.is_file() and f.name.upper().startswith(("LICEN", "COPYING", "NOTICE"))]
        if not files:
            sys.exit(f"ライセンス文が見つかりません: {crate} {versions[crate]}")
        for f in files:
            shutil.copy2(f, target / f"rust-{crate}-{versions[crate]}-{f.name}")


def make_zip(version: str) -> Path:
    archive = shutil.make_archive(str(ROOT / "dist" / f"UniversalEngineDock-{version}"), "zip", root_dir=DIST.parent, base_dir=DIST.name)
    return Path(archive)


def main() -> None:
    version = sys.argv[1] if len(sys.argv) > 1 else "dev"
    check_engines()
    check_bootloader()
    pyinstaller = ROOT / "venv" / "Scripts" / "pyinstaller.exe"
    if not pyinstaller.exists():
        sys.exit(f"pyinstaller が入っていません(venv\\Scripts\\pip install pyinstaller): {pyinstaller}")
    VERSION_FILE.parent.mkdir(exist_ok=True)
    VERSION_FILE.write_text(str(version_info(version, PRODUCT, "UniversalEngineDock.exe", PRODUCT)), encoding="utf-8")
    subprocess.run([str(pyinstaller), "--noconfirm", "--clean", str(ROOT / "UniversalEngineDock.spec")], check=True, cwd=ROOT)

    # 利用者が触るファイル・外部の実行ファイルは exe と同じフォルダに置く(uedock/paths.py の app_root() 参照)
    copy_engines(DIST / "engines", version)
    (DIST / "config").mkdir(exist_ok=True)
    # Universal Engine Dock 自体は GPL v3
    for name in ("README.md", "README.en.md", "LICENSE"):
        shutil.copy2(ROOT / name, DIST / name)
    shutil.copytree(ROOT / "docs", DIST / "docs", ignore=shutil.ignore_patterns("__pycache__"))  # 説明書とサンプルのエンジン
    (DIST / "assets").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "assets" / "icon.png", DIST / "assets" / "icon.png")  # README の絵
    copy_distributable_plugins(ROOT / "plugins", DIST / "plugins")
    copy_licenses(DIST / "licenses")
    copy_engine_licenses(DIST / "licenses")
    print(f"配布物: {DIST}")
    print(f"zip: {make_zip(version)}")


if __name__ == "__main__":
    main()
