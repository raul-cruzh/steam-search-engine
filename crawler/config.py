"""Configuration for the Steam crawler."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "data" / "pages"

# Start at the Steam homepage, without relying on search result filters.
SEED_URLS = ["https://store.steampowered.com/"]

# Start with a small live test. Increase only after checking the results.
MAX_GAME_PAGES = 10000
MAX_NAVIGATION_PAGES = 500
REQUEST_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "IRStudentCrawler/0.1 (university educational project)"
ROBOTS_URL = "https://store.steampowered.com/robots.txt"
