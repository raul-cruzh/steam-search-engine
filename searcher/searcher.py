"""Search the custom Steam inverted index using two ranking formulas.

The index is read from the JSON files produced by SteamIndexer.
Only the Python standard library is used for scoring.
"""

from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from typing import Any

# Use exactly the same tokenization for documents and queries.
from indexer.indexer import tokenize


class SteamSearcher:
    """Run OR queries and rank matched games with TF-IDF/cosine or BM25."""

    def __init__(self, index_directory: Path):
        self.index_directory = Path(index_directory)
        self.documents = self._read_json("documents.json")
        self.vocabulary = self._read_json("vocabulary.json")
        self.inverted_index = self._read_json("inverted_index.json")
        self.document_count = len(self.documents)
        if not self.document_count:
            raise ValueError("The index contains no documents")

        self.average_document_length = (
            sum(doc["length"] for doc in self.documents.values()) / self.document_count
        )
        # Computed only when cosine similarity is requested.
        self._document_norms: dict[str, float] | None = None

    def _read_json(self, filename: str) -> dict:
        path = self.index_directory / filename
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Expected a JSON object in {path}")
        return data

    def _tfidf_idf(self, term: str) -> float:
        """Smoothed, always-positive inverse document frequency."""
        df = self.vocabulary[term]["df"]
        return math.log((self.document_count + 1) / (df + 1)) + 1.0

    @staticmethod
    def _log_tf(tf: int) -> float:
        return 1.0 + math.log(tf) if tf > 0 else 0.0

    def _cosine_document_norms(self) -> dict[str, float]:
        if self._document_norms is not None:
            return self._document_norms
        norm_squares: dict[str, float] = defaultdict(float)
        for term, postings in self.inverted_index.items():
            idf = self._tfidf_idf(term)
            for doc_id, tf in postings.items():
                weight = self._log_tf(tf) * idf
                norm_squares[doc_id] += weight * weight
        self._document_norms = {
            doc_id: math.sqrt(norm_squares.get(doc_id, 0.0))
            for doc_id in self.documents
        }
        return self._document_norms

    def _tfidf_cosine(self, query_counts: Counter) -> dict[str, float]:
        """Cosine similarity of log-TF-IDF weighted query/document vectors."""
        query_weights = {
            term: self._log_tf(count) * self._tfidf_idf(term)
            for term, count in query_counts.items()
            if term in self.inverted_index
        }
        if not query_weights:
            return {}
        query_norm = math.sqrt(sum(weight * weight for weight in query_weights.values()))
        dot_products: dict[str, float] = defaultdict(float)
        for term, query_weight in query_weights.items():
            idf = self._tfidf_idf(term)
            for doc_id, tf in self.inverted_index[term].items():
                dot_products[doc_id] += query_weight * (self._log_tf(tf) * idf)

        document_norms = self._cosine_document_norms()
        return {
            doc_id: dot / (query_norm * document_norms[doc_id])
            for doc_id, dot in dot_products.items()
            if document_norms[doc_id] > 0 and dot > 0
        }

    def _bm25(self, query_counts: Counter, k1: float = 1.5, b: float = 0.75) -> dict[str, float]:
        """Standard Okapi BM25 with a positive Robertson-style IDF."""
        scores: dict[str, float] = defaultdict(float)
        avg_length = self.average_document_length
        for term in query_counts:
            if term not in self.inverted_index:
                continue
            df = self.vocabulary[term]["df"]
            idf = math.log(1.0 + (self.document_count - df + 0.5) / (df + 0.5))
            for doc_id, tf in self.inverted_index[term].items():
                doc_length = self.documents[doc_id]["length"]
                denominator = tf + k1 * (1.0 - b + b * doc_length / avg_length)
                scores[doc_id] += idf * tf * (k1 + 1.0) / denominator
        return dict(scores)

    def search(self, query: str, method: str = "tfidf", limit: int = 10) -> list[dict[str, Any]]:
        """Return top matches. OR means any query term can match a document."""
        if method not in ("tfidf", "bm25"):
            raise ValueError("method must be 'tfidf' or 'bm25'")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        query_counts = Counter(tokenize(query))
        if not query_counts:
            return []

        scores = (
            self._tfidf_cosine(query_counts)
            if method == "tfidf" else self._bm25(query_counts)
        )
        ranked = sorted(scores, key=lambda doc_id: (-scores[doc_id], doc_id))[:limit]
        return [
            {
                "app_id": doc_id,
                "title": self.documents[doc_id]["title"],
                "url": self.documents[doc_id]["url"],
                "score": scores[doc_id],
            }
            for doc_id in ranked
        ]
