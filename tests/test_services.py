from __future__ import annotations

import csv
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
                "isbn": "9780000000001",
                "book_no": "000001",
                "title": "测试图书",
                "author": "作者",
                "publisher": "出版社",
                "category": "管理",
            },
            self.shelf_id,
            quantity,
        )
        self.service.confirm_inbound_batch(batch_id)
        return batch_id

    def test_new_and_old_inbound_keep_numeric_fields_as_text(self) -> None:
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
        self.assertEqual(inventory[0]["book_no"], "000001")

        reader_id = self.service.save_reader("张三", "13800000000", "行政部")
        row = self.service.db.connect().execute(
            """
            SELECT typeof(book_no) AS book_no_type,
                   typeof(barcode) AS barcode_type
            FROM books
            WHERE barcode = ?
            """,
            ("001234567890",),
        ).fetchone()
        phone_row = self.service.db.connect().execute(
            "SELECT typeof(phone) AS phone_type FROM readers WHERE id = ?",
            (reader_id,),
        ).fetchone()
        self.assertEqual(row["book_no_type"], "text")
        self.assertEqual(row["barcode_type"], "text")
        self.assertEqual(phone_row["phone_type"], "text")

    def test_inbound_item_can_be_modified_before_confirm(self) -> None:
        batch_id = self.service.create_inbound_batch()
        item_id = self.service.add_inbound_item(
            batch_id,
            {"barcode": "0009", "book_no": "09", "title": "待修改图书"},
            self.shelf_id,
            1,
        )
        self.service.update_inbound_item(
            item_id,
            {"barcode": "0009", "book_no": "09", "title": "已修改图书"},
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
                "isbn": row["isbn"],
                "book_no": row["book_no"],
                "title": "测试图书修正",
                "author": row["author"],
                "publisher": row["publisher"],
                "category": row["category"],
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
        self.assertEqual(rows[1][2], '="000001"')


if __name__ == "__main__":
    unittest.main()
