import lucene

lucene.initVM()

from java.nio.file import Paths
from org.apache.lucene.analysis.standard import StandardAnalyzer
from org.apache.lucene.index import DirectoryReader
from org.apache.lucene.store import FSDirectory
from org.apache.lucene.search import IndexSearcher
from org.apache.lucene.search.similarities import BM25Similarity
from org.apache.lucene.queryparser.classic import (
    MultiFieldQueryParser, QueryParserBase
)


SEARCH_FIELDS = [
    "title",
    "developer",
    "publisher",
    "genres",
    "tags",
    "short_description",
    "full_description",
    "wiki_engine",
    "wiki_platforms"
]


def search(index_directory, query, limit=10):
    if limit < 1:
        raise ValueError("Limit must be at least 1")

    directory = FSDirectory.open(Paths.get(str(index_directory)))
    reader = DirectoryReader.open(directory)
    analyzer = StandardAnalyzer()

    try:
        searcher = IndexSearcher(reader)
        searcher.setSimilarity(BM25Similarity())

        parser = MultiFieldQueryParser(SEARCH_FIELDS, analyzer)
        parsed_query = QueryParserBase.cast_(parser).parse(query)

        hits = searcher.search(parsed_query, limit).scoreDocs
        stored_fields = searcher.storedFields()

        results = []

        for hit in hits:
            doc = stored_fields.document(hit.doc)

            results.append({
                "app_id": doc.get("app_id"),
                "title": doc.get("title"),
                "url": doc.get("url"),
                "score": hit.score
            })

        return results

    finally:
        reader.close()
        analyzer.close()
        directory.close()