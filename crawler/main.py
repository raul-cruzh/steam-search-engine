"""Command-line entry point for a new or resumed Steam crawl."""
import argparse

from .crawler import SteamCrawler


def main():
    parser = argparse.ArgumentParser(description="Crawl public Steam game pages.")
    parser.add_argument(
        "--resume", metavar="RUN_DIRECTORY",
        help="Resume an existing crawl from its data/pages/run_... directory.",
    )
    args = parser.parse_args()
    SteamCrawler(resume_dir=args.resume).run()


if __name__ == "__main__":
    main()
