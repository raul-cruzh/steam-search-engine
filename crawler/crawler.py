"""BFS crawler for Steam: URL rules, HTTP downloading, and HTML storage."""

from collections import deque
from datetime import datetime, timezone
import os
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from uuid import uuid4

from bs4 import BeautifulSoup
import requests

from .config import (
    MAX_GAME_PAGES, MAX_NAVIGATION_PAGES, OUTPUT_DIR,
    REQUEST_DELAY_SECONDS, REQUEST_TIMEOUT_SECONDS,
    ROBOTS_URL, SEED_URLS, USER_AGENT,
)

STORE_HOST = "store.steampowered.com"
APP_PATH_RE = re.compile(r"^/app/(\d+)(?:/|$)")
NAVIGATION_PATH_RE = re.compile(r"^/(?:category|genre|tags)/[a-zA-Z0-9_+%./-]+/?$")
DEMO_TITLE_RE = re.compile(r"\b(?:demo|playtest)\s*$", re.IGNORECASE)
SOUNDTRACK_TITLE_RE = re.compile(r"\b(?:soundtrack|ost)\s*$", re.IGNORECASE)
# These rules are intentionally conservative: uncertain products are checked
# again using the downloaded HTML, rather than rejected based on a vague link.
UNWANTED_LINK_RE = re.compile(
    r"\b(?:demo|playtest|soundtrack|ost|dlc)\s*$", re.IGNORECASE
)


def app_id_from_url(url: str) -> str | None:
    """Return a Steam AppID, or None if this is not a Steam app page."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != STORE_HOST:
        return None
    match = APP_PATH_RE.match(parts.path)
    return match.group(1) if match else None


def normalize_url(href: str, source_url: str) -> str | None:
    """Normalize supported Steam URLs, dropping fragments and tracking queries.

    App URLs become /app/ID/?l=english. Homepage and category/genre/tag URLs
    can also be visited to discover games, but are not stored as game pages.
    """
    try:
        url = urljoin(source_url, href)
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None  # Ignore malformed links instead of stopping the crawl.
    if parts.scheme not in ("http", "https") or parts.hostname != STORE_HOST:
        return None
    if parts.username or parts.password or port not in (None, 80, 443):
        return None

    app_id = app_id_from_url(f"https://{STORE_HOST}{parts.path}")
    if app_id is not None:
        return f"https://{STORE_HOST}/app/{int(app_id)}/?l=english"

    if parts.path in ("", "/"):
        return f"https://{STORE_HOST}/?l=english"

    if NAVIGATION_PATH_RE.fullmatch(parts.path):
        path = parts.path.rstrip("/") + "/"
        return f"https://{STORE_HOST}{path}?l=english"

    return None


def normalize_app_url(href: str, source_url: str) -> str | None:
    """Backward-compatible helper for canonical Steam app links only."""
    url = normalize_url(href, source_url)
    return url if url and app_id_from_url(url) is not None else None


def should_visit_url(url: str, link_text: str = "", href: str = "") -> bool:
    """URL-level rules: reject obvious demos/DLC before adding to BFS.

    Unclear app URLs are kept: the HTML classifier makes the final decision.
    """
    if app_id_from_url(url) is None:
        return True  # Supported navigation pages may lead to new games.

    # If a link has a product name, check its ending, not arbitrary page text.
    label = link_text.strip()
    if label and UNWANTED_LINK_RE.search(label):
        return False

    # Steam often places product names in the URL slug after /app/ID/.
    # Check the final word only; e.g. "Demolition" must not match "Demo".
    slug_parts = urlsplit(href).path.split("/")
    if len(slug_parts) >= 4 and slug_parts[1:2] == ["app"]:
        slug = slug_parts[3].replace("_", "-").replace("-", " ")
        if UNWANTED_LINK_RE.search(slug):
            return False
    return True


def extract_links(html: bytes, base_url: str) -> list[tuple[str, str, str]]:
    """Discover supported links, keeping their labels for pre-queue filtering."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    unique = set()
    for tag in soup.select("a[href]"):
        href = tag.get("href", "")
        url = normalize_url(href, base_url)
        if url is None:
            continue
        title = tag.get("title", "") or tag.get_text(" ", strip=True)
        key = (url, title)
        if key in unique:
            continue
        unique.add(key)
        links.append((url, title, href))
    return links


def extract_app_urls(html: bytes, base_url: str) -> list[str]:
    """Extract canonical app links, for simple tests and external reuse."""
    return list(dict.fromkeys(
        url for url, _title, _href in extract_links(html, base_url)
        if app_id_from_url(url) is not None
    ))


def classify_app_page(html: bytes) -> tuple[str, str | None]:
    """Classify the downloaded product HTML before storing it.

    These are heuristic HTML rules, not Steam's official product-type API.
    """
    soup = BeautifulSoup(html, "html.parser")
    name_tag = soup.select_one("#appHubAppName")
    title = name_tag.get_text(" ", strip=True) if name_tag else ""

    if soup.select_one(".game_area_soundtrack_bubble"):
        return "soundtrack", title or None
    if soup.select_one(".game_area_dlc_bubble"):
        return "dlc", title or None
    if not title:
        return "unknown", None
    if DEMO_TITLE_RE.search(title):
        return "demo_or_playtest", title
    if SOUNDTRACK_TITLE_RE.search(title):
        return "soundtrack", title
    return "game", title


class SteamCrawler:
    """A single-worker BFS crawler with two independent selection steps."""

    def __init__(self, resume_dir: str | Path | None = None):
        """Create a new crawl or resume a crawl from its saved state file."""
        self.last_request_at = None
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.robots = RobotFileParser()

        if resume_dir is not None:
            self.run_dir = Path(resume_dir).expanduser().resolve()
            self.manifest_path = self.run_dir / "manifest.jsonl"
            self.state_path = self.run_dir / "state.json"
            self._load_state()
            self._reconcile_saved_files()
            print(f"[RESUMED] Loaded state from {self.state_path}")
        else:
            seeds = [normalize_url(url, url) for url in SEED_URLS]
            seeds = [url for url in seeds if url is not None]
            if not seeds:
                raise ValueError("No supported Steam seed URLs were configured.")
            self.queue = deque(dict.fromkeys(seeds))
            self.known = set(self.queue)
            self.visited = set()
            self.games_saved = 0
            self.navigation_pages_visited = 0
            self.links_rejected_before_queue = 0
            self.bytes_saved = 0
            self.errors = 0
            self.excluded_by_type = {}
            run_name = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ_") + uuid4().hex[:6]
            self.run_dir = OUTPUT_DIR / run_name
            self.run_dir.mkdir(parents=True, exist_ok=False)
            self.manifest_path = self.run_dir / "manifest.jsonl"
            self.state_path = self.run_dir / "state.json"
            self._checkpoint()

    def _checkpoint(self):
        """Atomically save the BFS frontier and counters after each completed URL."""
        data = {
            "version": 1,
            "queue": list(self.queue),
            "known": sorted(self.known),
            "visited": sorted(self.visited),
            "games_saved": self.games_saved,
            "navigation_pages_visited": self.navigation_pages_visited,
            "links_rejected_before_queue": self.links_rejected_before_queue,
            "bytes_saved": self.bytes_saved,
            "errors": self.errors,
            "excluded_by_type": self.excluded_by_type,
        }
        temporary = self.run_dir / "state.json.tmp"
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(data, output, indent=2, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        # Windows may temporarily deny replacement while another process is
        # reading or scanning state.json. Keep the previous checkpoint intact
        # and retry instead of stopping a long crawl immediately.
        max_attempts = 20
        for attempt in range(1, max_attempts + 1):
            try:
                os.replace(temporary, self.state_path)
                if attempt > 1:
                    print(f"[CHECKPOINT] Saved after {attempt} attempts.")
                return
            except PermissionError:
                if attempt == max_attempts:
                    raise
                if attempt == 1:
                    print("[WARN] Checkpoint file is temporarily locked; retrying...")
                time.sleep(0.5)

    def _load_state(self):
        """Read a previous checkpoint; never silently reset a missing state."""
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Cannot resume: missing or invalid checkpoint at {self.state_path}"
            ) from exc
        if data.get("version") != 1:
            raise RuntimeError("Cannot resume: unsupported checkpoint version.")
        try:
            self.queue = deque(data["queue"])
            self.known = set(data["known"])
            self.visited = set(data["visited"])
            self.games_saved = int(data["games_saved"])
            self.navigation_pages_visited = int(data["navigation_pages_visited"])
            self.links_rejected_before_queue = int(data["links_rejected_before_queue"])
            self.bytes_saved = int(data["bytes_saved"])
            self.errors = int(data["errors"])
            self.excluded_by_type = dict(data["excluded_by_type"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Cannot resume: incomplete checkpoint.") from exc
        if not self.known.issuperset(self.queue) or not self.known.issuperset(self.visited):
            raise RuntimeError("Cannot resume: inconsistent URL frontier in checkpoint.")

    def _reconcile_saved_files(self):
        """Count completed HTML files, including one saved just before a crash."""
        saved = list(self.run_dir.glob("app_*.html"))
        if len(saved) < self.games_saved:
            raise RuntimeError(
                "Saved HTML files are missing from this run; cannot safely continue."
            )
        self.games_saved = len(saved)
        self.bytes_saved = sum(file.stat().st_size for file in saved)

    def _record(self, event: dict):
        event["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
        with self.manifest_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _load_robots(self):
        """Stop the crawl if robots.txt cannot be checked."""
        try:
            response = self.session.get(ROBOTS_URL, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Could not verify robots.txt: {exc}") from exc
        self.robots.parse(response.text.splitlines())
        self.last_request_at = time.monotonic()

    def _fetch(self, url: str) -> bytes | None:
        if not self.robots.can_fetch(USER_AGENT, url):
            print(f"[BLOCKED by robots.txt] {url}")
            self._record({"url": url, "result": "blocked_by_robots"})
            return None

        if self.last_request_at is not None:
            remaining = REQUEST_DELAY_SECONDS - (time.monotonic() - self.last_request_at)
            if remaining > 0:
                time.sleep(remaining)

        try:
            response = self.session.get(
                url, timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=False
            )
        except requests.RequestException as exc:
            self.errors += 1
            print(f"[ERROR] {url}: {exc}")
            self._record({"url": url, "result": "request_error", "error": str(exc)})
            return None
        finally:
            self.last_request_at = time.monotonic()

        if response.status_code in (403, 429):
            self._record({"url": url, "result": "access_denied", "status": response.status_code})
            raise RuntimeError(f"Steam returned HTTP {response.status_code}; stopping the crawl.")
        if response.status_code != 200:
            self.errors += 1
            self._record({"url": url, "result": "http_error_or_redirect", "status": response.status_code})
            print(f"[SKIPPED] HTTP {response.status_code}: {url}")
            return None
        if "text/html" not in response.headers.get("Content-Type", "").lower():
            self._record({"url": url, "result": "not_html"})
            print(f"[SKIPPED] Not HTML: {url}")
            return None
        return response.content

    def _save(self, url: str, html: bytes, title: str | None = None):
        app_id = app_id_from_url(url)
        if app_id is None:
            return
        file_name = f"app_{app_id}.html"
        path = self.run_dir / file_name
        if path.exists():
            return
        # Complete the file before exposing its final name. A crash cannot
        # leave a partly written HTML document named app_<id>.html.
        temporary = self.run_dir / (file_name + ".tmp")
        with temporary.open("wb") as output:
            output.write(html)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        self._record({
            "url": url, "result": "saved", "file": file_name,
            "app_id": app_id, "title": title, "size_bytes": len(html),
        })
        self.bytes_saved += len(html)
        self.games_saved += 1
        print(f"[SAVED] {file_name} ({len(html)} bytes)")

    def _discover(self, html: bytes, url: str):
        """Extract, normalize and filter links BEFORE adding them to the queue."""
        for new_url, title, href in extract_links(html, url):
            if new_url in self.known:
                continue
            if not should_visit_url(new_url, title, href):
                self.links_rejected_before_queue += 1
                continue
            self.known.add(new_url)
            self.queue.append(new_url)

    def run(self):
        print(f"Output directory: {self.run_dir}")
        deferred_navigation = 0
        try:
            self._load_robots()
            while self.queue and self.games_saved < MAX_GAME_PAGES:
                url = self.queue[0]  # Keep the URL pending until fully processed.
                if url in self.visited:
                    self.queue.popleft()
                    self._checkpoint()
                    continue

                # Keep navigation URLs for a future resumed run if the limit
                # has been reached. Rotate past them to process known apps.
                if app_id_from_url(url) is None and self.navigation_pages_visited >= MAX_NAVIGATION_PAGES:
                    self.queue.rotate(-1)
                    deferred_navigation += 1
                    if deferred_navigation >= len(self.queue):
                        print("[PAUSED] Navigation limit reached. Increase MAX_NAVIGATION_PAGES to continue.")
                        break
                    continue
                deferred_navigation = 0

                app_id = app_id_from_url(url)
                cached_file = self.run_dir / f"app_{app_id}.html" if app_id else None
                if cached_file is not None and cached_file.is_file():
                    # A page can be saved just before an abrupt interruption.
                    # Reprocess its stored HTML to recover outgoing links.
                    html = cached_file.read_bytes()
                    print(f"[CACHED] {cached_file.name}")
                else:
                    html = self._fetch(url)

                if html is not None:
                    # The page may contain useful links even if not stored.
                    self._discover(html, url)
                    if app_id is None:
                        self.navigation_pages_visited += 1
                        self._record({"url": url, "result": "navigation_page"})
                        print(f"[NAVIGATION] {url}")
                    else:
                        product_type, title = classify_app_page(html)
                        if product_type == "game":
                            self._save(url, html, title)
                        else:
                            self.excluded_by_type[product_type] = (
                                self.excluded_by_type.get(product_type, 0) + 1
                            )
                            self._record({
                                "url": url, "result": "excluded_product_type",
                                "product_type": product_type, "title": title,
                            })
                            print(f"[EXCLUDED] {product_type}: {title or url}")

                # A single checkpoint commits the processed URL together with
                # all newly discovered URLs and statistics. If interrupted
                # earlier, the URL remains pending for the next run.
                self.queue.popleft()
                self.visited.add(url)
                self._checkpoint()
        except KeyboardInterrupt:
            print("\n[STOPPED] Interrupted by user. Progress can be resumed.")
            # Discard in-memory work that was not committed to the checkpoint.
            self._load_state()
        except RuntimeError as exc:
            print(f"[STOPPED] {exc}")
            self._load_state()
            self.errors += 1
        finally:
            self._reconcile_saved_files()
            stats = {
                "game_pages_saved": self.games_saved,
                "navigation_pages_visited": self.navigation_pages_visited,
                "urls_seen": len(self.known),
                "urls_visited": len(self.visited),
                "remaining_queue": len(self.queue),
                "links_rejected_before_queue": self.links_rejected_before_queue,
                "bytes_saved": self.bytes_saved,
                "errors": self.errors,
                "excluded_by_type": self.excluded_by_type,
                "excluded_total": sum(self.excluded_by_type.values()),
                "run_directory": str(self.run_dir),
                "checkpoint_file": str(self.state_path),
            }
            (self.run_dir / "stats.json").write_text(
                json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            self.session.close()
            print("\nStatistics:")
            print(json.dumps(stats, indent=2, ensure_ascii=False))
