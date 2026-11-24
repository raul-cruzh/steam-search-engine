"""Distributed Steam HTML extraction using Apache Spark."""

import json
import os
import sys
from pathlib import Path

from pyspark.sql import SparkSession


PROJECT = Path(__file__).resolve().parents[1]
RUN = "run_20261027T235528Z_676afa"

INPUT_DIR = PROJECT / "data" / "pages" / RUN
OUTPUT_DIR = PROJECT / "data" / "spark_extracted" / RUN


def process_partition(paths):
    """Process Steam HTML pages inside a Spark worker."""

    sys.path.insert(0, str(PROJECT))

    from extractor.extractor import SteamExtractor

    extractor = SteamExtractor()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for filename in paths:
        path = Path(filename)
        output = OUTPUT_DIR / f"{path.stem}.json"

        try:
            game = extractor.extract_file(path)

            if not game.get("app_id") or not game.get("title"):
                raise ValueError("Missing App ID or title")

            temporary = output.with_suffix(".json.tmp")

            with temporary.open("w", encoding="utf-8") as file:
                json.dump(
                    game,
                    file,
                    ensure_ascii=False,
                    indent=2
                )

            os.replace(temporary, output)

            yield ("ok", path.name, "")

        except Exception as exc:
            yield ("error", path.name, str(exc))


def main():
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

    html_files = sorted(INPUT_DIR.glob("app_*.html"))

    if not html_files:
        raise SystemExit(f"No HTML files found: {INPUT_DIR}")

    print("=== STEAM DISTRIBUTED EXTRACTION ===")
    print("HTML files to process:", len(html_files))

    spark = (
        SparkSession.builder
        .appName("Steam Distributed Extraction")
        .master("local[2]")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )

    try:
        sc = spark.sparkContext
        sc.setLogLevel("ERROR")

        paths = [str(path) for path in html_files]

        results = (
            sc.parallelize(paths, 4)
            .mapPartitions(process_partition)
            .collect()
        )

        successful = sum(
            status == "ok"
            for status, _, _ in results
        )

        errors = [
            (filename, message)
            for status, filename, message in results
            if status == "error"
        ]

        print("\n=== SPARK EXTRACTION RESULTS ===")
        print("HTML pages processed:", len(results))
        print("Successful extractions:", successful)
        print("Errors:", len(errors))
        print("Output directory:", OUTPUT_DIR)

        if errors:
            print("\n=== FIRST ERRORS ===")
            for filename, message in errors[:10]:
                print(f"{filename}: {message}")
        else:
            print("\nSUCCESS: Distributed extraction completed!")

    finally:
        spark.stop()


if __name__ == "__main__":
    main()