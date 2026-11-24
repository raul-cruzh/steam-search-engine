"""Single entry point for the Steam + Wikipedia Apache Spark pipeline.

From the project root:
    python -m spark.main                    # check existing outputs (no Spark jobs)
    python -m spark.main --step steam       # extract Steam HTML (Spark)
    python -m spark.main --step wiki        # title candidates + Wikipedia extraction (Spark)
    python -m spark.main --step join        # Spark join + validation
    python -m spark.main --step all         # run the whole pipeline from original HTML
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = "run_20261027T235528Z_676afa"
DATA = ROOT / "data"


def show_status():
    """Read existing artifacts without reprocessing the Wikipedia dump."""
    sources = [
        ("Steam HTML", DATA / "pages" / RUN, "app_*.html"),
        ("Steam Spark JSON", DATA / "spark_extracted" / RUN, "app_*.json"),
    ]
    print("=== EXISTING PIPELINE OUTPUTS ===")
    for title, folder, pattern in sources:
        print(f"{title}: {sum(1 for _ in folder.glob(pattern)) if folder.exists() else 0}")

    wiki = DATA / "wiki"
    for title, filename in [
        ("Wikipedia candidates", "title_candidates.json"),
        ("Wikipedia extracted records", "wiki_candidate_articles.json"),
        ("Join relations", "steam_wikipedia_join.json"),
        ("Join audit", "steam_wikipedia_audited.json"),
        ("Review pending", "steam_wikipedia_review.json"),
    ]:
        path = wiki / filename
        if path.exists():
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
                print(f"{title}: {len(entries)}")
            except (OSError, ValueError, TypeError) as exc:
                print(f"{title}: unreadable ({exc})")
        else:
            print(f"{title}: not generated")


def main():
    parser = argparse.ArgumentParser(description="Steam / Wikipedia Spark pipeline")
    parser.add_argument(
        "--step", choices=("status", "steam", "wiki", "join", "all"),
        default="status", help="Pipeline stage. Default: status only."
    )
    args = parser.parse_args()
    if args.step == "status":
        show_status()
        return

    # Make the original extractor and the new spark package importable by Spark workers.
    os.environ["PYTHONPATH"] = str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

    if args.step in ("steam", "all"):
        from .steam import main as run_steam
        run_steam()
    if args.step in ("wiki", "all"):
        from .wikipedia import match_titles, extract
        match_titles()
        extract()
    if args.step in ("join", "all"):
        from .join import main as run_join
        run_join()


if __name__ == "__main__":
    main()
