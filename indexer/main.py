"""Build the custom Steam inverted index from extracted game JSON files."""

import argparse
import json
import os
from pathlib import Path

from .indexer import SteamIndexer


def save_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    with temp_path.open("w", encoding="utf-8") as output:
        json.dump(data, output, ensure_ascii=False, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    temp_path.replace(path)


def build_index(input_dir: Path, output_dir: Path) -> dict:
    """Rebuild from scratch, as allowed by the course requirements."""
    indexer = SteamIndexer()
    indexer.build(input_dir)
    data = indexer.to_data()
    for filename, content in data.items():
        save_json_atomic(output_dir / f"{filename}.json", content)
    stats = indexer.statistics()
    save_json_atomic(output_dir / "stats.json", stats)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a custom inverted index from Steam JSON documents.")
    parser.add_argument("extracted_directory", type=Path, help="Folder containing app_*.json files")
    parser.add_argument("--output", type=Path, help="Destination directory (default: data/index/<run name>)")
    args = parser.parse_args()
    output_dir = args.output or Path("data") / "index" / args.extracted_directory.name
    try:
        stats = build_index(args.extracted_directory, output_dir)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print("[INDEXED] Custom inverted index built successfully")
    print(json.dumps(stats, indent=2))
    print(f"Output directory: {output_dir}")


if __name__ == "__main__":
    main()
