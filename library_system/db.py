from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union


APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = APP_ROOT / "data" / "library.db"


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shelves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    note TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reason_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(category, name)
);

CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    publisher TEXT NOT NULL DEFAULT '',
    price TEXT NOT NULL DEFAULT '',
    publish_date TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    shelf_id INTEGER NOT NULL REFERENCES shelves(id),
    quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
    updated_at TEXT NOT NULL,
    UNIQUE(book_id, shelf_id)
);

CREATE TABLE IF NOT EXISTS inbound_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS inbound_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES inbound_batches(id) ON DELETE CASCADE,
    book_id INTEGER REFERENCES books(id),
    barcode TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    publisher TEXT NOT NULL DEFAULT '',
    shelf_id INTEGER NOT NULL REFERENCES shelves(id),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS readers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    department TEXT NOT NULL DEFAULT '',
    contact TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '正常',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reader_id INTEGER NOT NULL REFERENCES readers(id),
    borrowed_at TEXT NOT NULL,
    due_date TEXT NOT NULL,
    returned_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS loan_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    book_id INTEGER NOT NULL REFERENCES books(id),
    shelf_id INTEGER NOT NULL REFERENCES shelves(id),
    borrowed_at TEXT NOT NULL,
    returned_at TEXT,
    lost_at TEXT,
    status TEXT NOT NULL DEFAULT 'borrowed'
);

CREATE TABLE IF NOT EXISTS adjustment_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id),
    shelf_id INTEGER REFERENCES shelves(id),
    reason_id INTEGER NOT NULL REFERENCES reason_codes(id),
    previous_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    operator TEXT NOT NULL DEFAULT '管理员',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS loan_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    loan_item_id INTEGER REFERENCES loan_items(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    reason_id INTEGER NOT NULL REFERENCES reason_codes(id),
    previous_value TEXT NOT NULL DEFAULT '',
    new_value TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    operator TEXT NOT NULL DEFAULT '管理员',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);
CREATE INDEX IF NOT EXISTS idx_books_barcode ON books(barcode);
CREATE INDEX IF NOT EXISTS idx_readers_name ON readers(name);
CREATE INDEX IF NOT EXISTS idx_readers_phone ON readers(phone);
CREATE INDEX IF NOT EXISTS idx_loans_due_status ON loans(due_date, status);
CREATE INDEX IF NOT EXISTS idx_loan_items_status ON loan_items(status);
"""


DEFAULT_SETTINGS = {
    "default_borrow_days": "30",
    "renewal_days": "15",
}

DEFAULT_REASONS = {
    "stock_adjust": ["盘点差异", "破损", "丢失", "信息修正", "入库纠错"],
    "renewal": ["管理员续借", "特殊情况续借"],
    "lost_damage": ["读者丢失", "破损报损", "长期逾期报损"],
}


class Database:
    def __init__(self, path: Union[str, Path] = DEFAULT_DB_PATH) -> None:
        self.path = Path(path) if path != ":memory:" else Path(":memory:")
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            if str(self.path) != ":memory:":
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    def initialize(self) -> None:
        conn = self.connect()
        conn.executescript(SCHEMA)
        self._drop_removed_book_columns()
        from .services import now_text

        now = now_text()
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, value),
            )
        conn.execute(
            "INSERT OR IGNORE INTO shelves(name, note, active, created_at) VALUES(?, ?, 1, ?)",
            ("默认书架", "系统默认书架", now),
        )
        for category, names in DEFAULT_REASONS.items():
            for name in names:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO reason_codes(category, name, active, created_at)
                    VALUES(?, ?, 1, ?)
                    """,
                    (category, name, now),
                )
        conn.commit()

    def _drop_removed_book_columns(self) -> None:
        conn = self.connect()
        for table in ("books", "inbound_items"):
            existing = {
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for column in ("isbn", "book_no", "category"):
                if column in existing:
                    conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
