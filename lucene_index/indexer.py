import json
from pathlib import Path

import lucene

lucene.initVM()

from java.nio.file import Paths
from org.apache.lucene.analysis.standard import StandardAnalyzer
from org.apache.lucene.document import (
    Document, Field, StringField, TextField, StoredField
)
from org.apache.lucene.index import IndexWriter, IndexWriterConfig
from org.apache.lucene.store import FSDirectory
from org.apache.lucene.search.similarities import BM25Similarity


PROJECT = Path(__file__).resolve().parents[1]
RUN = "run_20261027T235528Z_676afa"

STEAM_DIR = PROJECT / "data" / "spark_extracted" / RUN
WIKI_FILE = PROJECT / "data" / "wiki" / "steam_wikipedia_audited.json"
INDEX_DIR = PROJECT / "data" / "lucene_index"

STEAM_FIELDS = (
    "title",
    "developer",
    "publisher",
    "genres",
    "tags",
    "short_description",
    "full_description"
)

WIKI_FIELDS = ("engine", "platforms")


def to_text(value):
    """Convert a field value into searchable text."""
    if isinstance(value, list):
        return " ".join(value)
    return value or ""


def load_wikipedia():
    """Load Wikipedia matches with consistent evidence."""
    records = json.loads(WIKI_FILE.read_text(encoding="utf-8"))
    matches = {}

    for record in records:
        if record["audit"]["assessment"] == "consistent_evidence":
            app_id = str(record["steam_app_id"])
            matches.setdefault(app_id, []).append(
                record["wiki_attributes_raw"]
            )

    return matches


def main():
    files = sorted(STEAM_DIR.glob("app_*.json"))

    if not files:
        raise SystemExit(f"No Steam JSON files found: {STEAM_DIR}")

    wikipedia = load_wikipedia()

    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    directory = FSDirectory.open(Paths.get(str(INDEX_DIR)))
    analyzer = StandardAnalyzer()

    config = IndexWriterConfig(analyzer)
    config.setOpenMode(IndexWriterConfig.OpenMode.CREATE)
    config.setSimilarity(BM25Similarity())

    writer = IndexWriter(directory, config)

    indexed = 0
    enriched = 0

    try:
        for path in files:
            game = json.loads(path.read_text(encoding="utf-8"))
            app_id = str(game["app_id"])

            doc = Document()

            doc.add(StringField("app_id", app_id, Field.Store.YES))
            doc.add(StoredField("url", game.get("url") or ""))

            # Steam fields
            for field in STEAM_FIELDS:
                value = to_text(game.get(field))
                store = Field.Store.YES if field == "title" else Field.Store.NO
                doc.add(TextField(field, value, store))

            # Wikipedia fields
            attributes = wikipedia.get(app_id, [])
            has_wiki_data = False

            for field in WIKI_FIELDS:
                value = " ".join(
                    item.get(field) or "" for item in attributes
                ).strip()

                if value:
                    doc.add(TextField(
                        f"wiki_{field}", value, Field.Store.NO
                    ))
                    has_wiki_data = True

            if has_wiki_data:
                enriched += 1

            writer.addDocument(doc)
            indexed += 1

    finally:
        writer.close()
        analyzer.close()
        directory.close()

    print("=== PYLUCENE INDEX ===")
    print("Indexed games:", indexed)
    print("Games enriched with Wikipedia:", enriched)
    print("Index directory:", INDEX_DIR)
    print("SUCCESS: Index created!")


if __name__ == "__main__":
    main()