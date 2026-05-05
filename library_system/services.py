from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from .db import Database


BOOK_FIELDS = [
    "barcode",
    "title",
    "author",
    "publisher",
    "price",
    "publish_date",
    "description",
]


class LibraryError(Exception):
    """Raised for user-correctable business rule failures."""


def now_text() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def today_text() -> str:
    return date.today().isoformat()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def row_dict(row: Optional[Any]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


def require_positive_int(value: Any, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise LibraryError(f"{field_name}必须是整数")
    if number <= 0:
        raise LibraryError(f"{field_name}必须大于 0")
    return number


def require_non_negative_int(value: Any, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise LibraryError(f"{field_name}必须是整数")
    if number < 0:
        raise LibraryError(f"{field_name}不能小于 0")
    return number


def excel_text(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    if text.isdigit():
        escaped = text.replace('"', '""')
        return f'="{escaped}"'
    return text


class LibraryService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.initialize()

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.db.connect().execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, clean_text(value)),
            )

    def default_borrow_days(self) -> int:
        return int(self.get_setting("default_borrow_days", "30") or 30)

    def default_renewal_days(self) -> int:
        return int(self.get_setting("renewal_days", "15") or 15)

    def add_shelf(self, name: str, note: str = "") -> int:
        name = clean_text(name)
        if not name:
            raise LibraryError("书架名称不能为空")
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM shelves WHERE name = ?",
                (name,),
            ).fetchone()
            if existing:
                if existing["active"]:
                    raise LibraryError("书架名称已存在")
                conn.execute(
                    "UPDATE shelves SET note = ?, active = 1 WHERE id = ?",
                    (clean_text(note), existing["id"]),
                )
                return int(existing["id"])
            cur = conn.execute(
                """
                INSERT INTO shelves(name, note, active, created_at)
                VALUES(?, ?, 1, ?)
                """,
                (name, clean_text(note), now_text()),
            )
            return int(cur.lastrowid)

    def update_shelf(self, shelf_id: int, name: str, note: str = "") -> None:
        name = clean_text(name)
        if not name:
            raise LibraryError("书架名称不能为空")
        with self.db.transaction() as conn:
            duplicate = conn.execute(
                "SELECT id FROM shelves WHERE name = ? AND id != ?",
                (name, shelf_id),
            ).fetchone()
            if duplicate:
                raise LibraryError("书架名称已存在")
            cur = conn.execute(
                """
                UPDATE shelves
                SET name = ?, note = ?
                WHERE id = ?
                """,
                (name, clean_text(note), shelf_id),
            )
            if cur.rowcount == 0:
                raise LibraryError("书架不存在")

    def set_shelf_active(self, shelf_id: int, active: bool) -> None:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "UPDATE shelves SET active = ? WHERE id = ?",
                (1 if active else 0, shelf_id),
            )
            if cur.rowcount == 0:
                raise LibraryError("书架不存在")

    def list_shelves(self, active_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM shelves"
        params: list[Any] = []
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY name"
        return [dict(row) for row in self.db.connect().execute(sql, params)]

    def add_reason(self, category: str, name: str) -> int:
        category = clean_text(category)
        name = clean_text(name)
        if not category or not name:
            raise LibraryError("原因分类和名称不能为空")
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM reason_codes WHERE category = ? AND name = ?",
                (category, name),
            ).fetchone()
            if existing:
                if existing["active"]:
                    raise LibraryError("原因已存在")
                conn.execute(
                    "UPDATE reason_codes SET active = 1 WHERE id = ?",
                    (existing["id"],),
                )
                return int(existing["id"])
            cur = conn.execute(
                """
                INSERT INTO reason_codes(category, name, active, created_at)
                VALUES(?, ?, 1, ?)
                """,
                (category, name, now_text()),
            )
            return int(cur.lastrowid)

    def update_reason(self, reason_id: int, category: str, name: str) -> None:
        category = clean_text(category)
        name = clean_text(name)
        if not category or not name:
            raise LibraryError("原因分类和名称不能为空")
        with self.db.transaction() as conn:
            duplicate = conn.execute(
                "SELECT id FROM reason_codes WHERE category = ? AND name = ? AND id != ?",
                (category, name, reason_id),
            ).fetchone()
            if duplicate:
                raise LibraryError("原因已存在")
            cur = conn.execute(
                """
                UPDATE reason_codes
                SET category = ?, name = ?
                WHERE id = ?
                """,
                (category, name, reason_id),
            )
            if cur.rowcount == 0:
                raise LibraryError("原因不存在")

    def set_reason_active(self, reason_id: int, active: bool) -> None:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "UPDATE reason_codes SET active = ? WHERE id = ?",
                (1 if active else 0, reason_id),
            )
            if cur.rowcount == 0:
                raise LibraryError("原因不存在")

    def list_reasons(
        self,
        category: Optional[str] = None,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM reason_codes WHERE 1 = 1"
        params: list[Any] = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        if active_only:
            sql += " AND active = 1"
        sql += " ORDER BY category, name"
        return [dict(row) for row in self.db.connect().execute(sql, params)]

    def _ensure_reason(self, reason_id: int, category: Optional[str] = None) -> dict[str, Any]:
        row = self.db.connect().execute(
            "SELECT * FROM reason_codes WHERE id = ? AND active = 1",
            (reason_id,),
        ).fetchone()
        if row is None:
            raise LibraryError("请选择有效的预设原因")
        data = dict(row)
        if category and data["category"] != category:
            raise LibraryError("原因分类不正确")
        return data

    def find_book_by_barcode(self, barcode: str) -> Optional[dict[str, Any]]:
        barcode = clean_text(barcode)
        if not barcode:
            return None
        return row_dict(
            self.db.connect().execute(
                "SELECT * FROM books WHERE barcode = ?",
                (barcode,),
            ).fetchone()
        )

    def create_inbound_batch(self, note: str = "") -> int:
        code = "RK" + datetime.now().strftime("%Y%m%d%H%M%S%f")
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO inbound_batches(code, status, note, created_at)
                VALUES(?, 'pending', ?, ?)
                """,
                (code, clean_text(note), now_text()),
            )
            return int(cur.lastrowid)

    def get_batch(self, batch_id: int) -> Optional[dict[str, Any]]:
        return row_dict(
            self.db.connect().execute(
                "SELECT * FROM inbound_batches WHERE id = ?",
                (batch_id,),
            ).fetchone()
        )

    def _ensure_pending_batch(self, conn: Any, batch_id: int) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM inbound_batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
        if row is None:
            raise LibraryError("入库批次不存在")
        batch = dict(row)
        if batch["status"] != "pending":
            raise LibraryError("该入库批次已确认，不能继续修改")
        return batch

    def add_inbound_item(
        self,
        batch_id: int,
        book_data: dict[str, Any],
        shelf_id: int,
        quantity: Any,
        note: str = "",
    ) -> int:
        data = {field: clean_text(book_data.get(field, "")) for field in BOOK_FIELDS}
        data["barcode"] = clean_text(book_data.get("barcode", ""))
        if not data["barcode"]:
            raise LibraryError("条码不能为空")
        qty = require_positive_int(quantity, "入库数量")
        existing = self.find_book_by_barcode(data["barcode"])
        if existing:
            for field in BOOK_FIELDS:
                if not data.get(field):
                    data[field] = clean_text(existing.get(field, ""))
            book_id = existing["id"]
        else:
            if not data["title"]:
                raise LibraryError("新书必须录入书名")
            book_id = None
        with self.db.transaction() as conn:
            self._ensure_pending_batch(conn, batch_id)
            shelf = conn.execute("SELECT id FROM shelves WHERE id = ?", (shelf_id,)).fetchone()
            if shelf is None:
                raise LibraryError("请选择有效书架")
            cur = conn.execute(
                """
                INSERT INTO inbound_items(
                    batch_id, book_id, barcode, title, author, publisher,
                    shelf_id, quantity, note, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    book_id,
                    data["barcode"],
                    data["title"],
                    data["author"],
                    data["publisher"],
                    shelf_id,
                    qty,
                    clean_text(note),
                    now_text(),
                    now_text(),
                ),
            )
            return int(cur.lastrowid)

    def update_inbound_item(
        self,
        item_id: int,
        book_data: dict[str, Any],
        shelf_id: int,
        quantity: Any,
        note: str = "",
    ) -> None:
        data = {field: clean_text(book_data.get(field, "")) for field in BOOK_FIELDS}
        if not data["barcode"]:
            raise LibraryError("条码不能为空")
        qty = require_positive_int(quantity, "入库数量")
        existing = self.find_book_by_barcode(data["barcode"])
        if existing:
            for field in BOOK_FIELDS:
                if not data.get(field):
                    data[field] = clean_text(existing.get(field, ""))
            book_id = existing["id"]
        else:
            if not data["title"]:
                raise LibraryError("新书必须录入书名")
            book_id = None
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT batch_id FROM inbound_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise LibraryError("入库流水不存在")
            self._ensure_pending_batch(conn, row["batch_id"])
            conn.execute(
                """
                UPDATE inbound_items
                SET book_id = ?, barcode = ?, title = ?, author = ?, publisher = ?, shelf_id = ?,
                    quantity = ?, note = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    book_id,
                    data["barcode"],
                    data["title"],
                    data["author"],
                    data["publisher"],
                    shelf_id,
                    qty,
                    clean_text(note),
                    now_text(),
                    item_id,
                ),
            )

    def delete_inbound_item(self, item_id: int) -> None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT batch_id FROM inbound_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                return
            self._ensure_pending_batch(conn, row["batch_id"])
            conn.execute("DELETE FROM inbound_items WHERE id = ?", (item_id,))

    def list_inbound_items(self, batch_id: int) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT ii.*, s.name AS shelf_name
            FROM inbound_items ii
            JOIN shelves s ON s.id = ii.shelf_id
            WHERE ii.batch_id = ?
            ORDER BY ii.id
            """,
            (batch_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def confirm_inbound_batch(self, batch_id: int) -> None:
        with self.db.transaction() as conn:
            self._ensure_pending_batch(conn, batch_id)
            items = conn.execute(
                "SELECT * FROM inbound_items WHERE batch_id = ? ORDER BY id",
                (batch_id,),
            ).fetchall()
            if not items:
                raise LibraryError("当前批次没有入库流水")
            for item_row in items:
                item = dict(item_row)
                book = conn.execute(
                    "SELECT * FROM books WHERE barcode = ?",
                    (item["barcode"],),
                ).fetchone()
                if book is None:
                    if not clean_text(item["title"]):
                        raise LibraryError(f"条码 {item['barcode']} 缺少书名，不能入库")
                    now = now_text()
                    cur = conn.execute(
                        """
                        INSERT INTO books(
                            barcode, title, author, publisher, price, publish_date,
                            description, created_at, updated_at
                        )
                        VALUES(?, ?, ?, ?, '', '', '', ?, ?)
                        """,
                        (
                            item["barcode"],
                            item["title"],
                            item["author"],
                            item["publisher"],
                            now,
                            now,
                        ),
                    )
                    book_id = int(cur.lastrowid)
                else:
                    book_id = int(book["id"])
                conn.execute(
                    """
                    INSERT INTO inventory(book_id, shelf_id, quantity, updated_at)
                    VALUES(?, ?, ?, ?)
                    ON CONFLICT(book_id, shelf_id)
                    DO UPDATE SET
                        quantity = inventory.quantity + excluded.quantity,
                        updated_at = excluded.updated_at
                    """,
                    (book_id, item["shelf_id"], item["quantity"], now_text()),
                )
                conn.execute(
                    "UPDATE inbound_items SET book_id = ?, updated_at = ? WHERE id = ?",
                    (book_id, now_text(), item["id"]),
                )
            conn.execute(
                """
                UPDATE inbound_batches
                SET status = 'confirmed', confirmed_at = ?
                WHERE id = ?
                """,
                (now_text(), batch_id),
            )

    def list_inventory(
        self,
        keyword: str = "",
        shelf_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT
                inv.id AS inventory_id,
                inv.quantity,
                b.id AS book_id,
                b.barcode,
                b.title,
                b.author,
                b.publisher,
                b.price,
                b.publish_date,
                b.description,
                s.id AS shelf_id,
                s.name AS shelf_name
            FROM inventory inv
            JOIN books b ON b.id = inv.book_id
            JOIN shelves s ON s.id = inv.shelf_id
            WHERE 1 = 1
        """
        params: list[Any] = []
        keyword = clean_text(keyword)
        if keyword:
            like = f"%{keyword}%"
            sql += """
                AND (
                    b.title LIKE ? OR b.barcode LIKE ? OR b.author LIKE ? OR
                    b.publisher LIKE ?
                )
            """
            params.extend([like] * 4)
        if shelf_id:
            sql += " AND s.id = ?"
            params.append(shelf_id)
        sql += " ORDER BY s.name, b.title, b.barcode"
        return [dict(row) for row in self.db.connect().execute(sql, params)]

    def adjust_book_inventory(
        self,
        book_id: int,
        shelf_id: int,
        new_quantity: Any,
        book_updates: dict[str, Any],
        reason_id: int,
        note: str = "",
        operator: str = "管理员",
    ) -> None:
        self._ensure_reason(reason_id, "stock_adjust")
        qty = require_non_negative_int(new_quantity, "库存数量")
        updates = {field: clean_text(book_updates.get(field, "")) for field in BOOK_FIELDS if field != "barcode"}
        if not updates.get("title"):
            raise LibraryError("书名不能为空")
        with self.db.transaction() as conn:
            book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
            if book is None:
                raise LibraryError("图书不存在")
            inv = conn.execute(
                "SELECT * FROM inventory WHERE book_id = ? AND shelf_id = ?",
                (book_id, shelf_id),
            ).fetchone()
            previous_qty = int(inv["quantity"]) if inv else 0
            previous = {field: book[field] for field in BOOK_FIELDS if field != "barcode"}
            previous["quantity"] = previous_qty
            new_value = dict(updates)
            new_value["quantity"] = qty
            conn.execute(
                """
                UPDATE books
                SET title = ?, author = ?, publisher = ?, price = ?,
                    publish_date = ?, description = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    updates["title"],
                    updates["author"],
                    updates["publisher"],
                    updates["price"],
                    updates["publish_date"],
                    updates["description"],
                    now_text(),
                    book_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO inventory(book_id, shelf_id, quantity, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(book_id, shelf_id)
                DO UPDATE SET quantity = excluded.quantity, updated_at = excluded.updated_at
                """,
                (book_id, shelf_id, qty, now_text()),
            )
            conn.execute(
                """
                INSERT INTO adjustment_logs(
                    book_id, shelf_id, reason_id, previous_value, new_value,
                    note, operator, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    book_id,
                    shelf_id,
                    reason_id,
                    json.dumps(previous, ensure_ascii=False),
                    json.dumps(new_value, ensure_ascii=False),
                    clean_text(note),
                    clean_text(operator) or "管理员",
                    now_text(),
                ),
            )

    def save_reader(
        self,
        name: str,
        phone: str,
        department: str = "",
        contact: str = "",
        status: str = "正常",
        reader_id: Optional[int] = None,
    ) -> int:
        name = clean_text(name)
        phone = clean_text(phone)
        if not name:
            raise LibraryError("读者姓名不能为空")
        if not phone:
            raise LibraryError("手机号不能为空")
        with self.db.transaction() as conn:
            if reader_id:
                conn.execute(
                    """
                    UPDATE readers
                    SET name = ?, phone = ?, department = ?, contact = ?,
                        status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        name,
                        phone,
                        clean_text(department),
                        clean_text(contact),
                        clean_text(status) or "正常",
                        now_text(),
                        reader_id,
                    ),
                )
                return reader_id
            cur = conn.execute(
                """
                INSERT INTO readers(name, phone, department, contact, status, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    phone,
                    clean_text(department),
                    clean_text(contact),
                    clean_text(status) or "正常",
                    now_text(),
                    now_text(),
                ),
            )
            return int(cur.lastrowid)

    def search_readers(self, query: str = "") -> list[dict[str, Any]]:
        query = clean_text(query)
        params: list[Any] = []
        sql = "SELECT * FROM readers"
        if query:
            like = f"%{query}%"
            sql += " WHERE name LIKE ? OR phone LIKE ? OR department LIKE ?"
            params.extend([like, like, like])
        sql += " ORDER BY name, phone LIMIT 200"
        return [dict(row) for row in self.db.connect().execute(sql, params)]

    def reader_current_loans(self, reader_id: int) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT
                li.id AS loan_item_id,
                l.id AS loan_id,
                l.borrowed_at,
                l.due_date,
                b.barcode,
                b.title,
                b.author,
                s.name AS shelf_name,
                li.status
            FROM loan_items li
            JOIN loans l ON l.id = li.loan_id
            JOIN books b ON b.id = li.book_id
            JOIN shelves s ON s.id = li.shelf_id
            WHERE l.reader_id = ? AND li.status = 'borrowed'
            ORDER BY l.due_date, b.title
            """,
            (reader_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def reader_history(self, reader_id: int) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT
                li.id AS loan_item_id,
                l.id AS loan_id,
                l.borrowed_at,
                l.due_date,
                li.returned_at,
                li.lost_at,
                li.status,
                b.barcode,
                b.title,
                s.name AS shelf_name
            FROM loan_items li
            JOIN loans l ON l.id = li.loan_id
            JOIN books b ON b.id = li.book_id
            JOIN shelves s ON s.id = li.shelf_id
            WHERE l.reader_id = ?
            ORDER BY l.borrowed_at DESC, li.id DESC
            LIMIT 200
            """,
            (reader_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def borrow_books(
        self,
        reader_id: int,
        barcodes: Iterable[str],
        due_days: Optional[int] = None,
        note: str = "",
    ) -> int:
        clean_barcodes = [clean_text(barcode) for barcode in barcodes if clean_text(barcode)]
        if not clean_barcodes:
            raise LibraryError("请先录入要借出的图书")
        days = due_days if due_days is not None else self.default_borrow_days()
        with self.db.transaction() as conn:
            reader = conn.execute(
                "SELECT * FROM readers WHERE id = ? AND status = '正常'",
                (reader_id,),
            ).fetchone()
            if reader is None:
                raise LibraryError("读者不存在或状态不可借阅")
            borrowed_at = now_text()
            due_date = (date.today() + timedelta(days=int(days))).isoformat()
            cur = conn.execute(
                """
                INSERT INTO loans(reader_id, borrowed_at, due_date, status, note)
                VALUES(?, ?, ?, 'active', ?)
                """,
                (reader_id, borrowed_at, due_date, clean_text(note)),
            )
            loan_id = int(cur.lastrowid)
            for barcode in clean_barcodes:
                book = conn.execute(
                    "SELECT * FROM books WHERE barcode = ?",
                    (barcode,),
                ).fetchone()
                if book is None:
                    raise LibraryError(f"条码 {barcode} 未入库")
                inv = conn.execute(
                    """
                    SELECT inv.*, s.name AS shelf_name
                    FROM inventory inv
                    JOIN shelves s ON s.id = inv.shelf_id
                    WHERE inv.book_id = ? AND inv.quantity > 0
                    ORDER BY s.name, inv.id
                    LIMIT 1
                    """,
                    (book["id"],),
                ).fetchone()
                if inv is None:
                    raise LibraryError(f"《{book['title']}》没有可借库存")
                conn.execute(
                    "UPDATE inventory SET quantity = quantity - 1, updated_at = ? WHERE id = ?",
                    (now_text(), inv["id"]),
                )
                conn.execute(
                    """
                    INSERT INTO loan_items(loan_id, book_id, shelf_id, borrowed_at, status)
                    VALUES(?, ?, ?, ?, 'borrowed')
                    """,
                    (loan_id, book["id"], inv["shelf_id"], borrowed_at),
                )
            return loan_id

    def return_books(self, reader_id: int, barcodes: Iterable[str]) -> None:
        clean_barcodes = [clean_text(barcode) for barcode in barcodes if clean_text(barcode)]
        if not clean_barcodes:
            raise LibraryError("请先录入要归还的图书")
        with self.db.transaction() as conn:
            for barcode in clean_barcodes:
                item = conn.execute(
                    """
                    SELECT li.*, l.reader_id, b.barcode, b.title
                    FROM loan_items li
                    JOIN loans l ON l.id = li.loan_id
                    JOIN books b ON b.id = li.book_id
                    WHERE l.reader_id = ? AND b.barcode = ? AND li.status = 'borrowed'
                    ORDER BY li.borrowed_at, li.id
                    LIMIT 1
                    """,
                    (reader_id, barcode),
                ).fetchone()
                if item is None:
                    raise LibraryError(f"当前读者没有在借条码 {barcode} 的图书")
                conn.execute(
                    """
                    UPDATE loan_items
                    SET status = 'returned', returned_at = ?
                    WHERE id = ?
                    """,
                    (now_text(), item["id"]),
                )
                conn.execute(
                    """
                    UPDATE inventory
                    SET quantity = quantity + 1, updated_at = ?
                    WHERE book_id = ? AND shelf_id = ?
                    """,
                    (now_text(), item["book_id"], item["shelf_id"]),
                )
                self._close_loan_if_complete(conn, item["loan_id"])

    def _close_loan_if_complete(self, conn: Any, loan_id: int) -> None:
        remaining = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM loan_items
            WHERE loan_id = ? AND status = 'borrowed'
            """,
            (loan_id,),
        ).fetchone()["total"]
        if remaining == 0:
            conn.execute(
                "UPDATE loans SET status = 'closed', returned_at = ? WHERE id = ?",
                (now_text(), loan_id),
            )

    def list_overdue(self) -> list[dict[str, Any]]:
        today = today_text()
        rows = self.db.connect().execute(
            """
            SELECT
                li.id AS loan_item_id,
                l.id AS loan_id,
                r.id AS reader_id,
                r.name AS reader_name,
                r.phone,
                r.department,
                b.barcode,
                b.title,
                s.name AS shelf_name,
                l.borrowed_at,
                l.due_date,
                CAST(julianday(?) - julianday(l.due_date) AS INTEGER) AS days_overdue
            FROM loan_items li
            JOIN loans l ON l.id = li.loan_id
            JOIN readers r ON r.id = l.reader_id
            JOIN books b ON b.id = li.book_id
            JOIN shelves s ON s.id = li.shelf_id
            WHERE li.status = 'borrowed' AND l.due_date < ?
            ORDER BY l.due_date, r.name, b.title
            """,
            (today, today),
        ).fetchall()
        return [dict(row) for row in rows]

    def renew_loan(
        self,
        loan_id: int,
        days: Any,
        reason_id: int,
        note: str = "",
        operator: str = "管理员",
    ) -> None:
        self._ensure_reason(reason_id, "renewal")
        renewal_days = require_positive_int(days, "续借天数")
        with self.db.transaction() as conn:
            loan = conn.execute(
                "SELECT * FROM loans WHERE id = ? AND status = 'active'",
                (loan_id,),
            ).fetchone()
            if loan is None:
                raise LibraryError("借阅记录不存在或已结束")
            old_due = date.fromisoformat(loan["due_date"])
            base = old_due if old_due >= date.today() else date.today()
            new_due = (base + timedelta(days=renewal_days)).isoformat()
            conn.execute(
                "UPDATE loans SET due_date = ? WHERE id = ?",
                (new_due, loan_id),
            )
            conn.execute(
                """
                INSERT INTO loan_actions(
                    loan_id, action, reason_id, previous_value, new_value,
                    note, operator, created_at
                )
                VALUES(?, 'renew', ?, ?, ?, ?, ?, ?)
                """,
                (
                    loan_id,
                    reason_id,
                    loan["due_date"],
                    new_due,
                    clean_text(note),
                    clean_text(operator) or "管理员",
                    now_text(),
                ),
            )

    def mark_lost(
        self,
        loan_item_id: int,
        reason_id: int,
        note: str = "",
        operator: str = "管理员",
    ) -> None:
        self._ensure_reason(reason_id, "lost_damage")
        with self.db.transaction() as conn:
            item = conn.execute(
                """
                SELECT li.*, b.title, b.barcode
                FROM loan_items li
                JOIN books b ON b.id = li.book_id
                WHERE li.id = ? AND li.status = 'borrowed'
                """,
                (loan_item_id,),
            ).fetchone()
            if item is None:
                raise LibraryError("借阅明细不存在或已处理")
            conn.execute(
                "UPDATE loan_items SET status = 'lost', lost_at = ? WHERE id = ?",
                (now_text(), loan_item_id),
            )
            conn.execute(
                """
                INSERT INTO loan_actions(
                    loan_id, loan_item_id, action, reason_id, previous_value, new_value,
                    note, operator, created_at
                )
                VALUES(?, ?, 'lost', ?, 'borrowed', 'lost', ?, ?, ?)
                """,
                (
                    item["loan_id"],
                    loan_item_id,
                    reason_id,
                    clean_text(note),
                    clean_text(operator) or "管理员",
                    now_text(),
                ),
            )
            self._close_loan_if_complete(conn, item["loan_id"])

    def dashboard_stats(self) -> dict[str, int]:
        conn = self.db.connect()
        today = today_text()
        borrowed_today = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM loan_items
            WHERE borrowed_at >= ? AND borrowed_at < ?
            """,
            (today + " 00:00:00", today + " 23:59:59"),
        ).fetchone()["total"]
        returned_today = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM loan_items
            WHERE returned_at >= ? AND returned_at < ?
            """,
            (today + " 00:00:00", today + " 23:59:59"),
        ).fetchone()["total"]
        zero_stock = conn.execute(
            "SELECT COUNT(*) AS total FROM inventory WHERE quantity = 0",
        ).fetchone()["total"]
        overdue = len(self.list_overdue())
        return {
            "borrowed_today": int(borrowed_today),
            "returned_today": int(returned_today),
            "zero_stock": int(zero_stock),
            "overdue": int(overdue),
        }

    def export_inventory_csv(self, path: Union[str, Path], shelf_id: Optional[int] = None) -> None:
        rows = self.list_inventory(shelf_id=shelf_id)
        headers = [
            "书架",
            "条码",
            "书名",
            "作者",
            "出版社",
            "库存数量",
        ]
        with open(path, "w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(headers)
            for row in rows:
                writer.writerow(
                    [
                        row["shelf_name"],
                        excel_text(row["barcode"]),
                        row["title"],
                        row["author"],
                        row["publisher"],
                        row["quantity"],
                    ]
                )
