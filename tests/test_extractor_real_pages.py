"""Optional integration checks on at least 20 saved Steam pages."""

import os
import unittest
from pathlib import Path

from extractor.extractor import SteamExtractor


class TestRealSteamPages(unittest.TestCase):
    def test_twenty_real_html_pages(self):
        """Test extraction on 20 genuine HTML documents, not synthetic fixtures."""
        if os.environ.get("STEAM_HTML_TEST_DIR"):
            folder = Path(os.environ["STEAM_HTML_TEST_DIR"])
        else:
            runs = sorted(Path("data/pages").glob("run_*"))
            folder = next((p for p in reversed(runs) if len(list(p.glob("app_*.html"))) >= 20), None)
        if folder is None or not folder.is_dir():
            self.skipTest("No run with at least 20 Steam HTML files found")
        files = sorted(folder.glob("app_*.html"))
        if len(files) < 20:
            self.skipTest("At least 20 real HTML files are required")
        parser = SteamExtractor()
        for file in files[:20]:
            with self.subTest(page=file.name):
                game = parser.extract_file(file)
                self.assertEqual(game["app_id"], file.stem[4:])
                self.assertTrue(game["title"])
                self.assertTrue(game["short_description"])
                self.assertIsInstance(game["full_description"], (str, type(None)))
                self.assertTrue(game["developer"])
                self.assertTrue(game["publisher"])
                self.assertTrue(game["genres"])


if __name__ == "__main__":
    unittest.main()
