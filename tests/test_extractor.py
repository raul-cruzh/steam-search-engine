"""Unit tests for the regex-based Steam extractor and incremental processing."""

import json
import tempfile
import unittest
from pathlib import Path

from extractor.extractor import SteamExtractor, div_content_by_id, meta_content
from extractor.main import process_folder


SAMPLE = b'''<html>
<div class="apphub_AppName" id="appHubAppName">A &amp; B</div>
<meta content="A &amp; B puzzle adventure" property="og:description">
<div id="game_area_description"><h2>About This Game</h2><div><p>Explore an <b>ancient</b> temple.</p></div><p>Find secrets.</p></div>
<div id="genresAndManufacturer"><b>Genre:</b> <a>Adventure</a>, <a>Puzzle</a><br>
<div class="dev_row"><b>Developer:</b> <a>Small Studio</a></div>
<div class="dev_row"><b>Publisher:</b> <a>Test Publisher</a></div>
<b>Release Date:</b> 14 Oct, 2026<br></div>
<div class="glance_tags popular_tags"><a class="app_tag">Puzzle</a><a class="app_tag">Exploration</a></div>
<div id="category_block"><div class="game_area_features_list_ctn"><a class="game_area_details_specs_ctn"><div class="label">Single-player</div></a></div></div>
<div id="languageTable"><table class="game_language_options"><tr><th>Language</th></tr>
<tr><td class="ellipsis">English</td><td>Yes</td></tr><tr><td>French</td></tr></table></div>
</html>'''


class TestExtractor(unittest.TestCase):
    def test_extract_main_fields(self):
        data = SteamExtractor().extract(SAMPLE, "123")
        self.assertEqual(data["title"], "A & B")
        self.assertEqual(data["developer"], ["Small Studio"])
        self.assertEqual(data["publisher"], ["Test Publisher"])
        self.assertEqual(data["genres"], ["Adventure", "Puzzle"])
        self.assertEqual(data["tags"], ["Puzzle", "Exploration"])
        self.assertEqual(data["features"], ["Single-player"])
        self.assertEqual(data["supported_languages"], ["English", "French"])
        self.assertEqual(data["release_date"], "14 Oct, 2026")
        self.assertIn("Find secrets.", data["full_description"])
        self.assertEqual(data["short_description"], "A & B puzzle adventure")

    def test_nested_div_closing_tag(self):
        self.assertEqual(div_content_by_id('<div id="x"><div>inner</div>outer</div>after', "x"), '<div>inner</div>outer')

    def test_meta_attribute_order(self):
        self.assertEqual(meta_content('<meta content="A &amp; B" name="Description">', "Description"), "A & B")

    def test_missing_values_are_not_invented(self):
        data = SteamExtractor().extract(b'<div id="appHubAppName">Only Title</div>', "10")
        self.assertIsNone(data["full_description"])
        self.assertEqual(data["developer"], [])

    def test_resume_without_reprocessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            target = Path(tmp) / "target"
            source.mkdir()
            for n in range(3):
                (source / f"app_{n+1}.html").write_bytes(SAMPLE)
            first = process_folder(source, target, limit=1)
            self.assertEqual(first["total_extracted"], 1)
            self.assertEqual(first["remaining"], 2)
            second = process_folder(source, target)
            self.assertEqual(second["total_extracted"], 3)
            self.assertEqual(second["new_in_this_invocation"], 2)
            third = process_folder(source, target)
            self.assertEqual(third["new_in_this_invocation"], 0)
            self.assertEqual(json.loads((target / "app_1.json").read_text())["app_id"], "1")


if __name__ == "__main__":
    unittest.main()
