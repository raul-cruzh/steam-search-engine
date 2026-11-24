"""Wikipedia title matching and Spark processing (REGEX on XML / wikitext)."""

import bz2
import html
import json
import os
import sys
import re
from pathlib import Path
from collections import defaultdict

from pyspark.sql import SparkSession

PROJECT = Path(__file__).resolve().parents[1]
RUN = "run_20261027T235528Z_676afa"
WIKI_DIR = PROJECT / "data" / "wiki"
STEAM_DIR = PROJECT / "data" / "spark_extracted" / RUN
INDEX_PATH = WIKI_DIR / "enwiki-20261101-pages-articles-multistream-index.txt.bz2"
DUMP_PATH = str(WIKI_DIR / "enwiki-20261101-pages-articles-multistream.xml.bz2")
CANDIDATES_PATH = WIKI_DIR / "title_candidates.json"
OUTPUT_PATH = WIKI_DIR / "wiki_candidate_articles.json"

FIELDS = {
    "developer", "publisher", "engine",
    "platforms", "released", "genre"
}

BRACES = re.compile(r"\{\{|\}\}")
FIELD_LINE = re.compile(
    r"^[ \t]*\|[ \t]*([a-zA-Z][\w -]*)[ \t]*=[ \t]*(.*)$"
)


def extract_wiki_attributes(page_xml: str) -> dict:
    """Extract complete raw fields from a video game infobox."""

    # Extract the article's wikitext.
    text = re.search(
        r"<text\b[^>]*>(.*?)</text>",
        page_xml,
        re.DOTALL,
    )

    if not text:
        return {}

    wikitext = html.unescape(text.group(1))

    # Find the video game infobox.
    start = re.search(
        r"(?im)^[ \t]*\{\{[ \t]*Infobox[ _]video[ _]game\b",
        wikitext,
    )

    if not start:
        return {}

    # Find the infobox end, respecting nested templates.
    depth = 0
    end = None

    for token in BRACES.finditer(wikitext, start.start()):
        depth += 1 if token.group() == "{{" else -1

        if depth == 0:
            end = token.start()
            break

    if end is None:
        return {}

    infobox = wikitext[start.start():end]

    result = {}
    current_field = None
    current_lines = []

    def save_field():
        if current_field in FIELDS:
            value = "\n".join(current_lines).strip()
            if value:
                result[current_field] = value

    # Extract fields only at the top level of the infobox.
    depth = 0

    for line in infobox.splitlines():
        field = FIELD_LINE.match(line) if depth == 1 else None

        if field:
            save_field()
            current_field = field.group(1).strip().casefold()
            current_lines = [field.group(2)]

        elif current_field in FIELDS:
            current_lines.append(line)

        for token in BRACES.finditer(line):
            depth += 1 if token.group() == "{{" else -1

    save_field()
    return result

def match_titles():
    """Create Wikipedia title candidates from the full Steam Spark output."""
    steam_titles = {}
    for path in STEAM_DIR.glob("app_*.json"):
        game = json.loads(path.read_text(encoding="utf-8"))
        title = game.get("title")
        if title:
            steam_titles.setdefault(title.strip().casefold(), []).append(str(game["app_id"]))

    if not steam_titles:
        raise SystemExit(f"No Steam games found in {STEAM_DIR}")
    if not INDEX_PATH.is_file():
        raise SystemExit(f"Wikipedia index not found: {INDEX_PATH}")

    matches = []
    with bz2.open(INDEX_PATH, "rt", encoding="utf-8") as stream:
        for line in stream:
            parts = line.rstrip("\n").split(":", 2)
            if len(parts) != 3:
                continue
            offset, wiki_id, title = parts
            key = title.replace("_", " ").strip().casefold()
            if key in steam_titles:
                matches.append({
                    "wiki_title": title,
                    "wiki_id": wiki_id,
                    "steam_app_ids": steam_titles[key],
                    "offset": int(offset),
                })

    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    CANDIDATES_PATH.write_text(
        json.dumps(matches, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    matched_games = {aid for match in matches for aid in match["steam_app_ids"]}
    print("=== WIKIPEDIA TITLE MATCHING ===")
    print("Steam titles:", len(steam_titles))
    print("Wikipedia matching pages:", len({m["wiki_id"] for m in matches}))
    print("Steam games with possible matches:", len(matched_games))
    print("Candidate output:", CANDIDATES_PATH)
    return matches


def process_partition(blocks):
    """Read compressed Wikipedia blocks inside Spark workers."""
    sys.path.insert(0, str(PROJECT))
    from spark.wikipedia import extract_wiki_attributes

    with open(DUMP_PATH, "rb") as dump:
        for offset, candidates in blocks:
            dump.seek(offset)

            # Decompress exactly one BZ2 stream.
            decompressor = bz2.BZ2Decompressor()
            chunks = []

            while not decompressor.eof:
                data = dump.read(65536)

                if not data:
                    raise EOFError(f"Incomplete Wikipedia block at {offset}")

                chunks.append(decompressor.decompress(data))

            xml = b"".join(chunks).decode("utf-8", errors="replace")

            # Identify the requested pages by Wikipedia ID.
            wanted_ids = {str(c["wiki_id"]) for c in candidates}
            pages = {}

            for match in re.finditer(r"<page>.*?</page>", xml, re.DOTALL):
                page = match.group(0)
                id_match = re.search(r"<id>(\d+)</id>", page)

                if id_match and id_match.group(1) in wanted_ids:
                    pages[id_match.group(1)] = page

            # Extract the attributes of each candidate.
            for candidate in candidates:
                wiki_id = str(candidate["wiki_id"])
                page = pages.get(wiki_id)

                attributes = (
                    extract_wiki_attributes(page)
                    if page is not None
                    else {}
                )

                yield {
                    "wiki_id": wiki_id,
                    "wiki_title": candidate["wiki_title"],
                    "steam_app_ids": candidate["steam_app_ids"],
                    "offset": offset,
                    "article_found": page is not None,
                    "attributes": attributes,
                }


def extract():
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

    candidates = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))

    # Group pages sharing the same compressed block.
    grouped = defaultdict(list)

    for candidate in candidates:
        grouped[int(candidate["offset"])].append(candidate)

    blocks = sorted(grouped.items())

    print("Wikipedia candidates:", len(candidates))
    print("Compressed blocks to process:", len(blocks))

    spark = (
        SparkSession.builder
        .appName("Steam Wikipedia Extraction")
        .master("local[2]")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )

    try:
        spark.sparkContext.setLogLevel("ERROR")

        rdd = spark.sparkContext.parallelize(blocks, 2)
        results = rdd.mapPartitions(process_partition).collect()

        OUTPUT_PATH.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        found = sum(r["article_found"] for r in results)
        with_attributes = [r for r in results if r["attributes"]]

        steam_ids = {
            app_id
            for result in with_attributes
            for app_id in result["steam_app_ids"]
        }

        print("\n=== WIKIPEDIA EXTRACTION RESULTS ===")
        print("Candidates processed:", len(results))
        print("Articles found:", found)
        print("Articles with video game attributes:",
              len(with_attributes))
        print("Steam games with candidate attributes:",
              len(steam_ids))
        print("Output:", OUTPUT_PATH)

        print("\n=== FIRST RESULTS ===")
        for result in with_attributes[:5]:
            print(result["wiki_title"], "| attributes:", ", ".join(sorted(result["attributes"])))

        print("\nSUCCESS: Wikipedia extraction completed!")

    finally:
        spark.stop()


if __name__ == "__main__":
    match_titles()
    extract()