"""Unit tests for query processing, TF-IDF/cosine and Okapi BM25."""

import json
import math
from pathlib import Path
import tempfile
import unittest

from indexer.indexer import SteamIndexer
from searcher.searcher import SteamSearcher


class SearcherTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        path = Path(self.temporary_directory.name)
        indexer = SteamIndexer()
        indexer.add_document({"app_id": "101", "title": "Magic magic witch", "url": "https://example.org/101"})
        indexer.add_document({"app_id": "102", "title": "Hockey hockey game", "url": "https://example.org/102"})
        indexer.add_document({"app_id": "103", "title": "Magic game sports", "url": "https://example.org/103"})
        indexer.add_document({"app_id": "104", "title": "Fishing adventure", "url": "https://example.org/104"})
        for key, data in indexer.to_data().items():
            (path / (key + ".json")).write_text(json.dumps(data), encoding="utf-8")
        self.searcher = SteamSearcher(path)

    def test_loads_index_and_statistics(self):
        self.assertEqual(self.searcher.document_count, 4)
        self.assertAlmostEqual(self.searcher.average_document_length, 2.75)

    def test_or_query_returns_union_of_postings(self):
        for method in ("tfidf", "bm25"):
            results = self.searcher.search("witch hockey", method=method)
            self.assertEqual({r["app_id"] for r in results}, {"101", "102"})

    def test_known_single_word(self):
        for method in ("tfidf", "bm25"):
            results = self.searcher.search("fishing", method=method)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["title"], "Fishing adventure")
            self.assertGreater(results[0]["score"], 0)

    def test_query_uses_indexer_tokenization(self):
        results = self.searcher.search("MAGIC, magic!", method="tfidf")
        self.assertEqual({r["app_id"] for r in results}, {"101", "103"})

    def test_cosine_prefers_a_focused_match(self):
        # For 'witch', the only matching game is 101.
        result = self.searcher.search("witch", method="tfidf")[0]
        self.assertLessEqual(result["score"], 1.0 + 1e-12)
        self.assertGreater(result["score"], 0)

    def test_bm25_formula(self):
        result = self.searcher.search("fishing", method="bm25")[0]
        n, df, tf = 4, 1, 1
        k1, b = 1.5, 0.75
        doc_length, avg_length = 2, 2.75
        expected = math.log(1 + (n-df+0.5)/(df+0.5)) * (tf*(k1+1)) / (tf+k1*(1-b+b*doc_length/avg_length))
        self.assertAlmostEqual(result["score"], expected, places=12)

    def test_unknown_and_empty_queries(self):
        for method in ("tfidf", "bm25"):
            self.assertEqual(self.searcher.search("unfindableterm", method), [])
            self.assertEqual(self.searcher.search("!!!", method), [])

    def test_limit_and_sort_order(self):
        for method in ("tfidf", "bm25"):
            results = self.searcher.search("magic hockey game adventure", method, limit=2)
            self.assertEqual(len(results), 2)
            self.assertGreaterEqual(results[0]["score"], results[1]["score"])

    def test_both_methods_are_computed_independently(self):
        a = self.searcher.search("magic game", method="tfidf")
        b = self.searcher.search("magic game", method="bm25")
        self.assertEqual({x["app_id"] for x in a}, {x["app_id"] for x in b})
        self.assertTrue(any(abs(x["score"] - y["score"]) > 0.001 for x, y in zip(a, b)))

    def test_bad_arguments_and_missing_index(self):
        with self.assertRaises(ValueError):
            self.searcher.search("magic", method="unknown")
        with self.assertRaises(ValueError):
            self.searcher.search("magic", limit=0)
        with self.assertRaises(ValueError):
            SteamSearcher(Path(self.temporary_directory.name) / "missing")


if __name__ == "__main__":
    unittest.main()
