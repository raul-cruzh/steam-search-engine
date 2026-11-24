"""Tests for TF, DF, inverted postings, and file-based index builds."""

import json
from pathlib import Path
import tempfile
import unittest

from indexer.indexer import SteamIndexer, document_text, tokenize
from indexer.main import build_index


class IndexerTests(unittest.TestCase):
    def test_tokenizer_case_and_punctuation(self):
        self.assertEqual(tokenize("Magic, MAGIC! RPG_2026"), ["magic", "magic", "rpg", "2026"])

    def test_selected_fields_only(self):
        game = {"app_id": "42", "title": "Magic", "genres": ["RPG"], "url": "hiddenword"}
        self.assertEqual(document_text(game), "Magic RPG")

    def test_tf_df_and_inverted_index(self):
        indexer = SteamIndexer()
        indexer.add_document({"app_id": "101", "title": "Magic magic RPG", "url": "https://example.org/1"})
        indexer.add_document({"app_id": "102", "title": "Magic adventure"})
        indexer.add_document({"app_id": "103", "title": "Adventure only"})
        data = indexer.to_data()
        self.assertEqual(data["inverted_index"]["magic"], {"101": 2, "102": 1})
        self.assertEqual(data["vocabulary"]["magic"], {"df": 2, "collection_tf": 3})
        self.assertEqual(data["vocabulary"]["rpg"]["df"], 1)
        self.assertEqual(data["documents"]["101"]["length"], 3)
        self.assertEqual(indexer.statistics()["document_count"], 3)

    def test_validation_and_duplicates(self):
        idx = SteamIndexer()
        with self.assertRaises(ValueError):
            idx.add_document({"title": "No id"})
        idx.add_document({"app_id": "7", "title": "First"})
        with self.assertRaises(ValueError):
            idx.add_document({"app_id": "7", "title": "Duplicate"})

    def test_build_from_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "extracted"
            destination = Path(tmp) / "index"
            source.mkdir()
            (source / "app_1.json").write_text(json.dumps({"app_id": "1", "title": "Magic Magic"}), encoding="utf-8")
            (source / "app_2.json").write_text(json.dumps({"app_id": "2", "title": "Magic World"}), encoding="utf-8")
            (source / "stats.json").write_text("{}", encoding="utf-8")
            stats = build_index(source, destination)
            self.assertEqual(stats["document_count"], 2)
            vocab = json.loads((destination / "vocabulary.json").read_text(encoding="utf-8"))
            self.assertEqual(vocab["magic"]["df"], 2)
            self.assertEqual(json.loads((destination / "inverted_index.json").read_text())["magic"], {"1": 2, "2": 1})
            self.assertEqual(build_index(source, destination)["document_count"], 2)

    def test_works_with_real_extractor_schema(self):
        fixture = Path(__file__).resolve().parent.parent / "examples" / "app_1737870.json"
        data = json.loads(fixture.read_text(encoding="utf-8"))
        idx = SteamIndexer()
        idx.add_document(data)
        self.assertIn("reka", idx.postings)
        self.assertIn("witch", idx.postings)
        self.assertEqual(idx.documents[data["app_id"]]["title"], "REKA")


if __name__ == "__main__":
    unittest.main()
