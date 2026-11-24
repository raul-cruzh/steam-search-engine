"""Join extracted Steam games with Wikipedia articles using Spark."""

import json
import os
import sys
from pathlib import Path
import re
import unicodedata

from pyspark.sql import SparkSession, functions as F


PROJECT = Path(__file__).resolve().parents[1]
RUN = "run_20261027T235528Z_676afa"

STEAM_DIR = PROJECT / "data" / "spark_extracted" / RUN
WIKI_FILE = PROJECT / "data" / "wiki" / "wiki_candidate_articles.json"
OUTPUT_FILE = PROJECT / "data" / "wiki" / "steam_wikipedia_join.json"


def normalize_title(value):
    """Normalize a game title for exact matching."""
    return (value or "").strip().casefold()


def load_steam_file(path):
    """Read one extracted Steam JSON inside a Spark worker."""
    game = json.loads(Path(path).read_text(encoding="utf-8"))

    return (
        str(game["app_id"]),
        game["title"],
        normalize_title(game["title"]),
        game.get("developer") or [],
        game.get("publisher") or [],
        game.get("release_date"),
        game.get("genres") or [],
    )


def load_wiki_record(page):
    """Prepare one Wikipedia article for the Spark join."""
    return (
        str(page["wiki_id"]),
        page["wiki_title"],
        normalize_title(page["wiki_title"]),
        page["attributes"],
        [str(app_id) for app_id in page["steam_app_ids"]],
    )


def remove_references(value):
    value = re.sub(
        r"<!--.*?-->", " ", value or "",
        flags=re.DOTALL
    )

    value = re.sub(
        r"<ref\b[^>]*>.*?</ref\s*>",
        " ", value,
        flags=re.IGNORECASE | re.DOTALL
    )

    return re.sub(
        r"<ref\b[^>]*/\s*>",
        " ", value,
        flags=re.IGNORECASE
    )


def normalise(value):
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.casefold()
    return " ".join(re.findall(r"[a-z0-9]+", value))


def wikipedia_developer_text(raw):
    raw = remove_references(raw)

    # Notes can mention developers of other editions.
    raw = re.split(
        r"\{\{\s*(?:efn|note)\s*\|",
        raw,
        maxsplit=1,
        flags=re.IGNORECASE
    )[0]

    raw = re.sub(
        r"\[\[[^\]|]+\|([^\]]+)\]\]",
        r"\1", raw
    )

    raw = re.sub(
        r"\[\[([^\]]+)\]\]",
        r"\1", raw
    )

    return normalise(raw)


def developer_agrees(steam_developers, wiki_developer):
    wiki = wikipedia_developer_text(wiki_developer)

    for developer in steam_developers:
        name = normalise(developer)

        if len(name) < 4:
            continue

        # Steam name appears in Wikipedia.
        if re.search(
            r"(?<![a-z0-9])" + re.escape(name) +
            r"(?![a-z0-9])",
            wiki
        ):
            return True

        # Wikipedia name appears within a longer Steam name.
        if 5 <= len(wiki) <= 100 and re.search(
            r"(?<![a-z0-9])" + re.escape(wiki) +
            r"(?![a-z0-9])",
            name
        ):
            return True

    return False


def extract_years(value):
    value = remove_references(value)

    return sorted({
        int(year)
        for year in re.findall(
            r"(?<!\d)(?:19\d{2}|20\d{2})(?!\d)",
            value
        )
    })


def audit(row):
    attributes = row["wiki_attributes_raw"]

    steam_years = extract_years(
        row.get("steam_release_date", "")
    )

    steam_year = steam_years[0] if steam_years else None

    wiki_years = extract_years(
        attributes.get("released", "")
    )

    wiki_dev = attributes.get("developer", "")

    dev_match = developer_agrees(
        row.get("steam_developers", []),
        wiki_dev
    )

    year_match = (
        steam_year in wiki_years
        if steam_year is not None
        else False
    )

    reasons = []

    if not wiki_dev or not wikipedia_developer_text(wiki_dev):
        reasons.append("wikipedia_developer_missing")
    elif not dev_match:
        reasons.append("developer_not_matched_automatically")

    if not wiki_years:
        reasons.append("wikipedia_release_year_missing")
    elif steam_year is None:
        reasons.append("steam_release_year_missing")
    elif not year_match:
        reasons.append("steam_year_not_listed_in_wikipedia")

        if min(
            abs(steam_year - year)
            for year in wiki_years
        ) > 5:
            reasons.append("year_difference_over_5")

    result = dict(row)

    result["audit"] = {
        "developer_match": dev_match,
        "steam_release_year": steam_year,
        "wikipedia_release_years": wiki_years,
        "release_year_match": year_match,
        "assessment": (
            "consistent_evidence"
            if dev_match and year_match
            else "manual_review"
        ),
        "review_reasons": reasons
    }

    return result




def main():
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

    steam_paths = [
        str(path) for path in sorted(STEAM_DIR.glob("app_*.json"))
    ]

    if not steam_paths:
        raise SystemExit(f"No Steam JSON files found: {STEAM_DIR}")

    wiki_records = json.loads(
        WIKI_FILE.read_text(encoding="utf-8")
    )

    # Keep articles containing extracted video game attributes.
    wiki_candidates = [
        page for page in wiki_records
        if page["article_found"] and page["attributes"]
    ]

    spark = (
        SparkSession.builder
        .appName("Steam Wikipedia Join")
        .master("local[2]")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )

    try:
        sc = spark.sparkContext
        sc.setLogLevel("ERROR")

        # Read Steam data in parallel using Spark workers.
        steam_rdd = sc.parallelize(steam_paths, 4).map(load_steam_file)

        steam_df = spark.createDataFrame(
            steam_rdd,
            schema=(
                "app_id string, "
                "steam_title string, "
                "title_key string, "
                "steam_developers array<string>, "
                "steam_publishers array<string>, "
                "steam_release_date string, "
                "steam_genres array<string>"
            ),
        )

        # Distribute extracted Wikipedia records.
        wiki_rdd = sc.parallelize(
            wiki_candidates, 2
        ).map(load_wiki_record)

        wiki_df = spark.createDataFrame(
            wiki_rdd,
            schema=(
                "wiki_id string, "
                "wiki_title string, "
                "title_key string, "
                "wiki_attributes map<string,string>, "
                "candidate_app_ids array<string>"
            ),
        )

        # Execute the actual join using Apache Spark.
        joined = (
            steam_df
            .join(F.broadcast(wiki_df), on="title_key", how="inner")
            .filter(
                F.array_contains(
                    F.col("candidate_app_ids"),
                    F.col("app_id")
                )
            )
            .cache()
        )

        # Calculate statistics in Spark.
        statistics = joined.agg(
            F.count("*").alias("join_rows"),
            F.countDistinct("wiki_id").alias("unique_wiki_pages"),
            F.countDistinct("app_id").alias("unique_steam_games"),
        ).first()

        # Collect the compact result for local JSON output.
        rows = joined.collect()

        output = []

        for row in rows:
            output.append({
                "steam_app_id": row.app_id,
                "steam_title": row.steam_title,
                "steam_developers": row.steam_developers,
                "steam_publishers": row.steam_publishers,
                "steam_release_date": row.steam_release_date,
                "steam_genres": row.steam_genres,
                "wiki_id": row.wiki_id,
                "wiki_title": row.wiki_title,
                "wiki_attributes_raw": row.wiki_attributes,
                "match_method": "exact_normalized_title",
                "match_status": "candidate_requires_validation",
            })

        output.sort(
            key=lambda item: (
                item["steam_title"].casefold(),
                item["wiki_id"]
            )
        )

        OUTPUT_FILE.write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print("\n=== SPARK JOIN RESULTS ===")
        print("Steam JSON files:", len(steam_paths))
        print("Wikipedia articles with attributes:", len(wiki_candidates))
        print("Join rows:", statistics["join_rows"])
        print("Unique Wikipedia pages:", statistics["unique_wiki_pages"])
        print("Unique Steam games:", statistics["unique_steam_games"])
        print("Output:", OUTPUT_FILE)

        print("\n=== FIRST 10 MATCHES ===")
        for item in output[:10]:
            print(
                f"{item['steam_title']} "
                f"| AppID: {item['steam_app_id']} "
                f"| Wiki ID: {item['wiki_id']}"
            )

        audited = [audit(item) for item in output]
        review = [row for row in audited if row["audit"]["assessment"] == "manual_review"]
        review.sort(key=lambda row: (
            "year_difference_over_5" not in row["audit"]["review_reasons"],
            row["steam_title"].casefold(),
        ))
        audited_path = PROJECT / "data" / "wiki" / "steam_wikipedia_audited.json"
        review_path = PROJECT / "data" / "wiki" / "steam_wikipedia_review.json"
        audited_path.write_text(json.dumps(audited, ensure_ascii=False, indent=2), encoding="utf-8")
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n=== JOIN VALIDATION ===")
        print("Consistent evidence:", len(audited) - len(review))
        print("Needs manual review:", len(review))
        print("Large year differences (>5 years):", sum(
            "year_difference_over_5" in row["audit"]["review_reasons"] for row in review
        ))
        print("Audited data:", audited_path)
        print("Review candidates:", review_path)
        print("\nSUCCESS: Spark join and audit completed!")

        joined.unpersist()

    finally:
        spark.stop()


if __name__ == "__main__":
    main()