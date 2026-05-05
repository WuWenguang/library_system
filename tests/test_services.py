from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from library_system.db import Database
from library_system.services import LibraryService


class LibraryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.service = LibraryService(Database(Path(self.tmp.name) / "library.db"))
        self.shelf_id = self.service.list_shelves()[0]["id"]
        self.stock_reason_id = self.service.list_reasons("stock_adjust")[0]["id"]
        self.renew_reason_id = self.service.list_reasons("renewal")[0]["id"]
        self.lost_reason_id = self.service.list_reasons("lost_damage")[0]["id"]

    def tearDown(self) -> None:
        self.service.db.close()
        self.tmp.cleanup()

    def inbound_book(self, barcode: str = "001234567890", quantity: int = 3) -> int:
        batch_id = self.service.create_inbound_batch("test")
        self.service.add_inbound_item(
            batch_id,
            {
                "barcode": barcode,
                "title": "测试图书",
                "author": "作者",
                "publisher": "出版社",
            },
            self.shelf_id,
            quantity,
        )
        self.service.confirm_inbound_batch(batch_id)
        return batch_id

    def test_new_and_old_inbound_keep_barcode_as_text(self) -> None:
        self.inbound_book(quantity=3)
        second_batch = self.service.create_inbound_batch("old")
        self.service.add_inbound_item(
            second_batch,
            {"barcode": "001234567890"},
            self.shelf_id,
            2,
        )
        self.service.confirm_inbound_batch(second_batch)

        inventory = self.service.list_inventory("001234567890")
        self.assertEqual(inventory[0]["quantity"], 5)

        reader_id = self.service.save_reader("张三", "13800000000", "行政部")
        row = self.service.db.connect().execute(
            """
            SELECT typeof(barcode) AS barcode_type
            FROM books
            WHERE barcode = ?
            """,
            ("001234567890",),
        ).fetchone()
        phone_row = self.service.db.connect().execute(
            "SELECT typeof(phone) AS phone_type FROM readers WHERE id = ?",
            (reader_id,),
        ).fetchone()
        self.assertEqual(row["barcode_type"], "text")
        self.assertEqual(phone_row["phone_type"], "text")
        book_columns = {
            column["name"]
            for column in self.service.db.connect().execute("PRAGMA table_info(books)")
        }
        self.assertFalse({"isbn", "book_no", "category"} & book_columns)

    def test_inbound_item_can_be_modified_before_confirm(self) -> None:
        batch_id = self.service.create_inbound_batch()
        item_id = self.service.add_inbound_item(
            batch_id,
            {"barcode": "0009", "title": "待修改图书"},
            self.shelf_id,
            1,
        )
        self.service.update_inbound_item(
            item_id,
            {"barcode": "0009", "title": "已修改图书"},
            self.shelf_id,
            4,
        )
        self.service.confirm_inbound_batch(batch_id)

        inventory = self.service.list_inventory("0009")
        self.assertEqual(inventory[0]["title"], "已修改图书")
        self.assertEqual(inventory[0]["quantity"], 4)

    def test_adjust_inventory_writes_log(self) -> None:
        self.inbound_book(quantity=3)
        row = self.service.list_inventory("001234567890")[0]
        self.service.adjust_book_inventory(
            row["book_id"],
            row["shelf_id"],
            1,
            {
                "title": "测试图书修正",
                "author": row["author"],
                "publisher": row["publisher"],
                "price": "",
                "publish_date": "",
                "description": "",
            },
            self.stock_reason_id,
            "盘点少两本",
        )

        adjusted = self.service.list_inventory("测试图书修正")[0]
        self.assertEqual(adjusted["quantity"], 1)
        log_count = self.service.db.connect().execute(
            "SELECT COUNT(*) AS total FROM adjustment_logs"
        ).fetchone()["total"]
        self.assertEqual(log_count, 1)

    def test_borrow_and_return_restores_original_shelf(self) -> None:
        self.inbound_book(quantity=2)
        reader_id = self.service.save_reader("李四", "13900000000", "财务部")
        self.service.borrow_books(reader_id, ["001234567890"], due_days=7)

        after_borrow = self.service.list_inventory("001234567890")[0]
        self.assertEqual(after_borrow["quantity"], 1)
        self.assertEqual(len(self.service.reader_current_loans(reader_id)), 1)

        self.service.return_books(reader_id, ["001234567890"])
        after_return = self.service.list_inventory("001234567890")[0]
        self.assertEqual(after_return["quantity"], 2)
        self.assertEqual(len(self.service.reader_current_loans(reader_id)), 0)
        self.assertEqual(self.service.reader_history(reader_id)[0]["status"], "returned")

    def test_overdue_renew_and_lost_damage(self) -> None:
        self.inbound_book(quantity=2)
        reader_id = self.service.save_reader("王五", "13700000000", "技术部")
        loan_id = self.service.borrow_books(reader_id, ["001234567890"], due_days=-1)
        self.assertEqual(len(self.service.list_overdue()), 1)

        self.service.renew_loan(loan_id, 3, self.renew_reason_id, "续借测试")
        self.assertEqual(len(self.service.list_overdue()), 0)

        self.service.borrow_books(reader_id, ["001234567890"], due_days=-1)
        overdue_item = self.service.list_overdue()[0]
        self.service.mark_lost(overdue_item["loan_item_id"], self.lost_reason_id, "遗失")
        history = self.service.reader_history(reader_id)
        self.assertIn("lost", {item["status"] for item in history})

    def test_inventory_export_protects_numeric_text(self) -> None:
        self.inbound_book(quantity=1)
        path = Path(self.tmp.name) / "inventory.csv"
        self.service.export_inventory_csv(path)
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            rows = list(csv.reader(file))
        self.assertEqual(rows[1][1], '="001234567890"')
        self.assertEqual(rows[0], ["书架", "条码", "书名", "作者", "出版社", "库存数量"])

    def test_shelf_can_be_updated_deleted_and_recreated(self) -> None:
        shelf_id = self.service.add_shelf("临时书架", "旧备注")
        self.service.update_shelf(shelf_id, "临时书架2", "新备注")

        shelf = next(row for row in self.service.list_shelves() if row["id"] == shelf_id)
        self.assertEqual(shelf["name"], "临时书架2")
        self.assertEqual(shelf["note"], "新备注")

        self.service.set_shelf_active(shelf_id, False)
        self.assertNotIn(shelf_id, {row["id"] for row in self.service.list_shelves()})

        recreated_id = self.service.add_shelf("临时书架2", "恢复")
        self.assertEqual(recreated_id, shelf_id)
        shelf = next(row for row in self.service.list_shelves() if row["id"] == shelf_id)
        self.assertEqual(shelf["note"], "恢复")

    def test_reason_can_be_updated_deleted_and_recreated(self) -> None:
        reason_id = self.service.add_reason("stock_adjust", "临时原因")
        self.service.update_reason(reason_id, "stock_adjust", "临时原因2")

        reason = next(row for row in self.service.list_reasons("stock_adjust") if row["id"] == reason_id)
        self.assertEqual(reason["name"], "临时原因2")

        self.service.set_reason_active(reason_id, False)
        self.assertNotIn(reason_id, {row["id"] for row in self.service.list_reasons("stock_adjust")})

        recreated_id = self.service.add_reason("stock_adjust", "临时原因2")
        self.assertEqual(recreated_id, reason_id)

    def test_database_migration_removes_removed_book_columns(self) -> None:
        path = Path(self.tmp.name) / "legacy.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barcode TEXT NOT NULL UNIQUE,
                isbn TEXT NOT NULL DEFAULT '',
                book_no TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                author TEXT NOT NULL DEFAULT '',
                publisher TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                price TEXT NOT NULL DEFAULT '',
                publish_date TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE inbound_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                book_id INTEGER,
                barcode TEXT NOT NULL,
                isbn TEXT NOT NULL DEFAULT '',
                book_no TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                publisher TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                shelf_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO books(barcode, isbn, book_no, title, category, created_at, updated_at)
            VALUES('0001', '9781', 'B001', '旧书', '旧分类', '2026-01-01 00:00:00', '2026-01-01 00:00:00');
            """
        )
        conn.close()

        service = LibraryService(Database(path))
        try:
            for table in ("books", "inbound_items"):
                columns = {
                    column["name"]
                    for column in service.db.connect().execute(f"PRAGMA table_info({table})")
                }
                self.assertFalse({"isbn", "book_no", "category"} & columns)
            self.assertEqual(service.find_book_by_barcode("0001")["title"], "旧书")
        finally:
            service.db.close()


if __name__ == "__main__":
    unittest.main()
