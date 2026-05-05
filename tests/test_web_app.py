from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from library_system.web_app import INDEX_HTML, WebLibraryApp


class WebLibraryAppTest(unittest.TestCase):
    def test_initial_data_and_page_are_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = WebLibraryApp(Path(tmp) / "library.db")
            data = app.initial_data()

        self.assertIn("图书借阅管理系统", INDEX_HTML)
        self.assertIn("shelves", data)
        self.assertGreaterEqual(len(data["shelves"]), 1)
        self.assertIn("stock_adjust", data["reasons"])

    def test_inbound_form_uses_single_code_field(self) -> None:
        inbound_section = INDEX_HTML.split('<section id="inbound" class="page">', 1)[1].split(
            '<section id="inventory" class="page">',
            1,
        )[0]

        self.assertIn('id="inBarcode"', inbound_section)
        self.assertNotIn('id="inIsbn"', inbound_section)
        self.assertNotIn('id="inBookNo"', inbound_section)
        self.assertNotIn('id="inCategory"', inbound_section)

    def test_inventory_editor_omits_removed_book_fields(self) -> None:
        inventory_section = INDEX_HTML.split('<section id="inventory" class="page">', 1)[1].split(
            '<section id="readers" class="page">',
            1,
        )[0]

        self.assertNotIn('id="editIsbn"', inventory_section)
        self.assertNotIn('id="editBookNo"', inventory_section)
        self.assertNotIn('id="editCategory"', inventory_section)

    def test_settings_include_edit_and_delete_controls(self) -> None:
        settings_section = INDEX_HTML.split('<section id="settings" class="page">', 1)[1].split(
            "</section>",
            1,
        )[0]

        self.assertIn("updateShelf()", settings_section)
        self.assertIn("deleteShelf()", settings_section)
        self.assertIn("updateReason()", settings_section)
        self.assertIn("deleteReason()", settings_section)


if __name__ == "__main__":
    unittest.main()
