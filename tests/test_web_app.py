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


if __name__ == "__main__":
    unittest.main()
