"""ぷよの定跡ファイルの登録と選択

登録の内容は config/puyo_books.json に保存する(利用者は直接書かない)。
定跡ファイルは今の YamaNEKO の書き出し(opening_tree.exe --export)と同じ形式。組み込みの定跡は常に「標準の定跡」として選べる。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ....paths import config_dir

REGISTRY_PATH = config_dir() / "puyo_books.json"
BUILTIN_BOOK = "標準の定跡"


@dataclass
class BookEntry:
    name: str
    path: str


@dataclass
class Registry:
    books: list[BookEntry] = field(default_factory=list)
    selected_book: str = BUILTIN_BOOK  # シミュレーターで使う定跡
    # 支援モードで側ごとに使う定跡("1p"・"2p"・"solo" → 定跡の名前。無い・空なら使わない)
    side_books: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = REGISTRY_PATH) -> "Registry":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        books = [BookEntry(str(b["name"]), str(b["path"])) for b in data.get("books", [])
                 if isinstance(b, dict) and b.get("name") and b.get("path")]
        side_books = data.get("side_books", {})
        side_books = {str(k): str(v) for k, v in side_books.items()} if isinstance(side_books, dict) else {}
        return cls(books, str(data.get("selected_book", BUILTIN_BOOK)), side_books)

    def save(self, path: Path = REGISTRY_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"books": [asdict(b) for b in self.books],
                "selected_book": self.selected_book, "side_books": self.side_books}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def book_path(self, name: str | None = None) -> Path | None:
        """定跡ファイルの場所(組み込みなら None = 組み込みの既定)。"""
        name = self.selected_book if name is None else name
        entry = next((b for b in self.books if b.name == name), None)
        return Path(entry.path) if entry is not None else None
