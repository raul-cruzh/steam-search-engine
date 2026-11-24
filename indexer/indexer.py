"""Build a custom inverted index using only Python's standard library.

Term frequency (TF) counts occurrences in each document.
Document frequency (DF) counts documents containing a term.
No search/indexing framework is used.
"""

from collections import Counter, defaultdict
import re
from pathlib import Path
from typing import Any

# Only fields useful for game searches are indexed. IDs and URLs remain metadata.
INDEXED_FIELDS = (
    "title", "developer", "publisher", "genres", "tags",
    "short_description", "full_description",
)
TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Lowercase and split text into Unicode letter/number tokens."""
    return TOKEN_RE.findall(text.casefold())


def document_text(document: dict[str, Any]) -> str:
    """Convert selected text fields into one searchable text string."""
    parts = []
    for field in INDEXED_FIELDS:
        value = document.get(field)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(item for item in value if isinstance(item, str))
    return " ".join(parts)


class SteamIndexer:
    """Build a vocabulary and an inverted index from extracted game JSON."""

    def __init__(self) -> None:
        self.postings: dict[str, dict[str, int]] = defaultdict(dict)
        self.documents: dict[str, dict[str, Any]] = {}
        self.total_terms = 0

    def add_document(self, document: dict[str, Any]) -> None:
        app_id = str(document.get("app_id") or "").strip()
        title = document.get("title")
        if not app_id or not isinstance(title, str) or not title.strip():
            raise ValueError("Document must have a non-empty app_id and title")
        if app_id in self.documents:
            raise ValueError(f"Duplicate app_id: {app_id}")

        counts = Counter(tokenize(document_text(document)))
        document_length = sum(counts.values())
        self.documents[app_id] = {
            "title": title,
            "url": document.get("url") or "",
            "length": document_length,
        }
        self.total_terms += document_length
        for term, count in counts.items():
            self.postings[term][app_id] = count

    def build(self, source_dir: Path) -> None:
        if not source_dir.is_dir():
            raise FileNotFoundError(f"Extracted folder does not exist: {source_dir}")
        files = sorted(source_dir.glob("app_*.json"))
        if not files:
            raise ValueError(f"No app_*.json documents in: {source_dir}")
        import json
        for path in files:
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Cannot read {path.name}: {exc}") from exc
            if not isinstance(document, dict):
                raise ValueError(f"Expected JSON object in {path.name}")
            self.add_document(document)

    def to_data(self) -> dict[str, dict]:
        """Export stable data files for a future searcher."""
        vocabulary = {}
        inverted_index = {}
        for term in sorted(self.postings):
            postings = self.postings[term]
            vocabulary[term] = {
                "df": len(postings),
                "collection_tf": sum(postings.values()),
            }
            inverted_index[term] = {doc_id: postings[doc_id] for doc_id in sorted(postings)}
        return {
            "documents": dict(sorted(self.documents.items())),
            "vocabulary": vocabulary,
            "inverted_index": inverted_index,
        }

    def statistics(self) -> dict[str, int | float]:
        n = len(self.documents)
        return {
            "document_count": n,
            "vocabulary_size": len(self.postings),
            "total_indexed_terms": self.total_terms,
            "average_document_length": round(self.total_terms / n, 3) if n else 0.0,
            "postings_count": sum(len(p) for p in self.postings.values()),
        }
