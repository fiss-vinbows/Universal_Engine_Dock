"""ぷよのサイドごとの設定(パネルの表記・定跡・CPU の使用・配ぷよ読みなど)の保存。

設定項目の画面(side_panel.py)が変わるたびに
config/puyo_sides.json へ保存し、支援モードを始める時(session.py)に読む。画面と支援モードが直接つながらないので、
共通のメイン画面はぷよの設定の中身を知らなくてよい。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from ...paths import config_dir

# CPU の使用(YamaNEKO の探索スレッド数)の選択肢
CPU_USE_CHOICES = [("自動", "auto"), ("全コア", "all"), ("半分", "half"), ("4分の1", "quarter"), ("2スレッド", "two")]


def options_path() -> Path:
    return config_dir() / "puyo_sides.json"


@dataclass
class SideOptions:
    language: str = "ja"  # 情報パネルの表記("ja" / "en" / 言語プラグインのキー)
    book: str = ""  # 使う定跡の名前(使わないなら "")
    cpu_use: str = "auto"  # CPU_USE_CHOICES のキー
    low_priority: bool = False  # 思考エンジンの優先度を下げる
    haipuyo: bool = False  # 配ぷよ読み(YamaNEKO だけ)
    peek: bool = False  # 相手のツモを覗く(YamaNEKO だけ)
    # 相手の盤面を見る(凝視)。相手がテトリスなど別のゲームの時は切る
    gaze: bool = True

    def search_threads(self, auto: int | None) -> int | None:
        """YamaNEKO に渡す探索スレッド数(auto は「自動」の時の値。None ならエンジンの既定 = 全コア)。"""
        cores = os.cpu_count() or 2
        if self.cpu_use == "all":
            return cores
        if self.cpu_use == "half":
            return max(1, cores // 2)
        if self.cpu_use == "quarter":
            return max(1, cores // 4)
        if self.cpu_use == "two":
            return 2
        return auto


def load_all(path: Path | None = None) -> dict[str, SideOptions]:
    """保存してあるサイドごとの設定 {サイド: SideOptions}。読めない項目は既定のまま。"""
    path = options_path() if path is None else path
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, SideOptions] = {}
    if not isinstance(data, dict):
        return out
    kinds = {f.name: f.type for f in fields(SideOptions)}
    for side, item in data.items():
        if not isinstance(item, dict):
            continue
        options = SideOptions()
        for name, value in item.items():
            if name in kinds and isinstance(value, type(getattr(options, name))):
                setattr(options, name, value)
        out[str(side)] = options
    return out


def load(side: str, path: Path | None = None) -> SideOptions:
    return load_all(path).get(side, SideOptions())


def save(side: str, options: SideOptions, path: Path | None = None) -> None:
    path = Path(options_path() if path is None else path)
    everything = load_all(path)
    everything[side] = options
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {s: asdict(o) for s, o in everything.items()}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
