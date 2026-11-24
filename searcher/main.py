"""Command-line interface for Steam search."""

import argparse
from pathlib import Path

from .searcher import SteamSearcher


def main() -> None:
    parser = argparse.ArgumentParser(description="Search Steam games.")
    parser.add_argument("index_directory", type=Path)
    parser.add_argument("--query", required=True)
    parser.add_argument(
        "--method",
        choices=("tfidf", "bm25", "both", "lucene"),
        default="both"
    )
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    try:
        if args.method == "lucene":
            from lucene_index.searcher import search

            results = search(
                args.index_directory, args.query, args.limit
            )
            print_results("PyLucene BM25", args.query, results)

        else:
            searcher = SteamSearcher(args.index_directory)
            methods = (
                ("tfidf", "bm25")
                if args.method == "both"
                else (args.method,)
            )

            for method in methods:
                results = searcher.search(
                    args.query, method=method, limit=args.limit
                )

                title = (
                    "TF-IDF + cosine similarity"
                    if method == "tfidf"
                    else "Okapi BM25"
                )

                print_results(title, args.query, results)

    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


def print_results(title, query, results):
    print(f"\n[{title}] Query: {query!r} | {len(results)} result(s) shown")

    if not results:
        print("No matching games found.")

    for rank, result in enumerate(results, start=1):
        print(
            f"{rank:2d}. {result['title']}  "
            f"(AppID: {result['app_id']}, score: {result['score']:.4f})"
        )
        print(f"    {result['url']}")


if __name__ == "__main__":
    main()