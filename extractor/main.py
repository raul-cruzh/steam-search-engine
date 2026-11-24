"""Run regex extraction on existing Steam HTML files."""

import argparse
import json
import os
from collections import Counter
from pathlib import Path

from .extractor import SteamExtractor


def write_json_atomic(path: Path, data: dict) -> None:
    """Write a JSON file safely, even when the process is interrupted."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary_path.replace(path)


def report_statistics(output_dir: Path, input_files: list[Path], new_count: int, errors: dict) -> dict:
    files = sorted(output_dir.glob("app_*.json"))
    missing = Counter()
    valid = 0
    for file in files:
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
            if doc.get("app_id") and doc.get("title"):
                valid += 1
            for field in (
                "title", "developer", "publisher", "release_date", "genres",
                "tags", "features", "short_description", "full_description",
                "supported_languages",
            ):
                if not doc.get(field):
                    missing[field] += 1
        except (json.JSONDecodeError, OSError):
            missing["invalid_json"] += 1
    return {
        "input_html_count": len(input_files),
        "total_extracted": len(files),
        "valid_id_and_title": valid,
        "new_in_this_invocation": new_count,
        "remaining": len([p for p in input_files if not (output_dir / (p.stem + ".json")).exists()]),
        "output_size_bytes": sum(file.stat().st_size for file in files),
        "missing_fields": dict(missing),
        "errors": errors,
    }


def make_wiki_examples(output_dir: Path, limit: int = 3) -> Path:
    """Create short examples that can be copied to the course wiki."""
    lines = ["# Steam regex extraction examples", "", "Examples from downloaded Steam HTML pages.", ""]
    for file in sorted(output_dir.glob("app_*.json"))[:limit]:
        doc = json.loads(file.read_text(encoding="utf-8"))
        lines.extend([
            f"## {doc.get('title') or doc['app_id']}",
            f"- App ID: `{doc['app_id']}`",
            f"- URL: {doc['url']}",
            f"- Developer: {', '.join(doc['developer']) or 'Not found'}",
            f"- Release date: {doc.get('release_date') or 'Not found'}",
            f"- Tags: {', '.join(doc['tags'][:8]) or 'Not found'}",
            f"- Short description: {doc.get('short_description') or 'Not found'}",
            "",
        ])
    path = output_dir / "WIKI_EXAMPLES.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def process_folder(input_dir: Path, output_dir: Path, limit: int = 0, overwrite: bool = False) -> dict:
    """Process only unfinished files. Finished JSON documents are checkpoints."""
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input folder does not exist: {input_dir}")
    files = sorted(input_dir.glob("app_*.html"))
    if not files:
        raise ValueError(f"No Steam app_*.html files found in: {input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    parser = SteamExtractor()
    errors = {}
    new_count = 0
    try:
        for file in files:
            destination = output_dir / f"{file.stem}.json"
            if not overwrite and destination.exists():
                try:
                    existing = json.loads(destination.read_text(encoding="utf-8"))
                    if existing.get("app_id") and existing.get("title"):
                        continue
                except (OSError, json.JSONDecodeError):
                    pass  # Rebuild a corrupted or incomplete result.
            try:
                document = parser.extract_file(file)
                if not document["title"]:
                    raise ValueError("The game title could not be extracted")
                write_json_atomic(destination, document)
                new_count += 1
                print(f"[EXTRACTED] {file.name}: {document['title']}")
            except (ValueError, OSError) as exc:
                errors[file.name] = str(exc)
                print(f"[ERROR] {file.name}: {exc}")
            # Every JSON document is a checkpoint. Refresh aggregate stats
            # periodically rather than rescanning thousands of JSON files on
            # every extracted document.
            if new_count and new_count % 50 == 0:
                stats = report_statistics(output_dir, files, new_count, errors)
                write_json_atomic(output_dir / "stats.json", stats)
            if limit and new_count >= limit:
                break
    except KeyboardInterrupt:
        print("\n[STOPPED] Extraction interrupted. Finished JSON files are preserved.")
    finally:
        stats = report_statistics(output_dir, files, new_count, errors)
        write_json_atomic(output_dir / "stats.json", stats)
        make_wiki_examples(output_dir)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract data from saved Steam game HTML using regular expressions.")
    parser.add_argument("run_directory", type=Path, help="Existing crawler data/pages/run_... folder")
    parser.add_argument("--output", type=Path, help="Output folder (default: data/extracted/<run name>)")
    parser.add_argument("--limit", type=int, default=0, help="Maximum new documents in this run; 0 means all")
    parser.add_argument("--overwrite", action="store_true", help="Re-extract all source documents")
    args = parser.parse_args()
    output = args.output or Path("data") / "extracted" / args.run_directory.name
    try:
        stats = process_folder(args.run_directory, output, args.limit, args.overwrite)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print("\nExtraction statistics:")
    print(json.dumps(stats, indent=2))
    print(f"\nOutput directory: {output}")


if __name__ == "__main__":
    main()
