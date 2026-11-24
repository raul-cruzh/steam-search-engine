"""Offline tests for the Steam crawler's URL, page, and BFS rules."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crawler import crawler as crawler_module
from crawler.crawler import (
    app_id_from_url, classify_app_page, extract_app_urls, extract_links,
    normalize_app_url, normalize_url, should_visit_url,
)

ROOT = "https://store.steampowered.com/?l=english"


def game(title, extra=b""):
    return f'<div id="appHubAppName">{title}</div>'.encode() + extra


class CrawlerTests(unittest.TestCase):
    def test_app_ids(self):
        self.assertEqual(app_id_from_url("https://store.steampowered.com/app/620/Portal_2/"), "620")
        self.assertIsNone(app_id_from_url("https://example.com/app/620/"))
        self.assertIsNone(app_id_from_url("https://store.steampowered.com/sub/620/"))

    def test_app_url_normalization(self):
        source = "https://store.steampowered.com/category/action/"
        variants = [
            "/app/620/Portal_2/?snr=1#description",
            "https://store.steampowered.com/app/620/?curator_clanid=123",
            "/app/620/#reviews",
        ]
        for url in variants:
            self.assertEqual(normalize_app_url(url, source),
                             "https://store.steampowered.com/app/620/?l=english")

    def test_navigation_normalization(self):
        self.assertEqual(normalize_url("https://store.steampowered.com/", ROOT), ROOT)
        self.assertEqual(normalize_url("/category/action/#header", ROOT),
                         "https://store.steampowered.com/category/action/?l=english")
        self.assertEqual(normalize_url("/genre/RPG/?snr=tracking", ROOT),
                         "https://store.steampowered.com/genre/RPG/?l=english")
        self.assertEqual(normalize_url("/tags/en/Action/", ROOT),
                         "https://store.steampowered.com/tags/en/Action/?l=english")

    def test_scope_rejects_other_sites_and_paths(self):
        self.assertIsNone(normalize_url("https://example.com/app/7/", ROOT))
        self.assertIsNone(normalize_url("/account/", ROOT))
        self.assertIsNone(normalize_url("/search/?category1=998", ROOT))
        self.assertIsNone(normalize_url("javascript:alert(1)", ROOT))

    def test_discovery_without_clicking_or_duplicate_links(self):
        html = (b'<a href="/app/620/Portal_2/">Portal 2</a>'
                b'<a href="/app/620/?snr=2">same game</a>'
                b'<a href="/category/action/">Action</a>'
                b'<a href="https://other.example/app/700/">external</a>')
        links = extract_links(html, ROOT)
        self.assertEqual([link[0] for link in links], [
            "https://store.steampowered.com/app/620/?l=english",
            "https://store.steampowered.com/app/620/?l=english",
            "https://store.steampowered.com/category/action/?l=english",
        ])
        self.assertEqual(extract_app_urls(html, ROOT),
                         ["https://store.steampowered.com/app/620/?l=english"])

    def test_prequeue_product_filter(self):
        app = "https://store.steampowered.com/app/10/?l=english"
        self.assertFalse(should_visit_url(app, "First Demo", "/app/10/First_Demo/"))
        self.assertFalse(should_visit_url(app, "", "/app/10/First_Soundtrack/"))
        self.assertFalse(should_visit_url(app, "Bonus DLC", "/app/10/"))
        self.assertTrue(should_visit_url(app, "Demolition Days", "/app/10/Demolition_Days/"))
        self.assertTrue(should_visit_url(app, "Game with demo included", "/app/10/"))

    def test_html_product_type_filter(self):
        self.assertEqual(classify_app_page(game("First Game"))[0], "game")
        self.assertEqual(classify_app_page(game("First Game",
                         b'<div class="demo_above_purchase">Demo available</div>'))[0], "game")
        self.assertEqual(classify_app_page(game("First Demo"))[0], "demo_or_playtest")
        self.assertEqual(classify_app_page(game("Test Playtest"))[0], "demo_or_playtest")
        self.assertEqual(classify_app_page(game("Original OST"))[0], "soundtrack")
        self.assertEqual(classify_app_page(game("Bonus", b'<div class="game_area_dlc_bubble"></div>'))[0], "dlc")
        self.assertEqual(classify_app_page(b"<html>No product name</html>")[0], "unknown")

    def _run_offline(self, pages, max_games=2, max_navigation=10):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.object(crawler_module, "OUTPUT_DIR", Path(tmp.name)), \
             patch.object(crawler_module, "SEED_URLS", [ROOT]), \
             patch.object(crawler_module, "MAX_GAME_PAGES", max_games), \
             patch.object(crawler_module, "MAX_NAVIGATION_PAGES", max_navigation):
            crawler = crawler_module.SteamCrawler()
            crawler._load_robots = lambda: None
            calls = []

            def mock_fetch(url):
                calls.append(url)
                return pages[url]

            crawler._fetch = mock_fetch
            crawler.run()
        return crawler, calls

    def test_bfs_from_home_through_category_pages(self):
        app1 = "https://store.steampowered.com/app/101/?l=english"
        app2 = "https://store.steampowered.com/app/102/?l=english"
        app3 = "https://store.steampowered.com/app/103/?l=english"
        category = "https://store.steampowered.com/category/action/?l=english"
        crawler, requests = self._run_offline({
            ROOT: b'<a href="/category/action/">Action</a><a href="/app/101/">A</a>',
            category: b'<a href="/app/102/">B</a>',
            app1: game("A", b'<a href="/app/103/">C</a>'),
            app2: game("B"),
            app3: game("C"),
        })
        self.assertEqual(requests, [ROOT, category, app1, app2])
        self.assertEqual(crawler.games_saved, 2)
        self.assertEqual(crawler.navigation_pages_visited, 2)
        self.assertEqual({p.name for p in crawler.run_dir.glob("*.html")},
                         {"app_101.html", "app_102.html"})
        self.assertEqual(json.loads((crawler.run_dir / "stats.json").read_text())["errors"], 0)

    def test_url_filter_before_queue_and_page_filter_after_fetch(self):
        demo = "https://store.steampowered.com/app/1/?l=english"
        dlc = "https://store.steampowered.com/app/2/?l=english"
        full_game = "https://store.steampowered.com/app/3/?l=english"
        crawler, requests = self._run_offline({
            ROOT: (b'<a href="/app/1/My_Demo/">My Demo</a>'
                   b'<a href="/app/2/">Extra Pack</a>'
                   b'<a href="/app/3/">Full Game</a>'),
            dlc: game("Extra Pack", b'<div class="game_area_dlc_bubble"></div>'),
            full_game: game("Full Game"),
        }, max_games=1)
        self.assertEqual(requests, [ROOT, dlc, full_game])
        self.assertNotIn(demo, crawler.known)
        self.assertEqual(crawler.links_rejected_before_queue, 1)
        self.assertEqual(crawler.excluded_by_type, {"dlc": 1})
        self.assertEqual({p.name for p in crawler.run_dir.glob("app_*.html")}, {"app_3.html"})

    def test_second_eligible_anchor_overrides_an_earlier_excluded_one(self):
        app = "https://store.steampowered.com/app/98/?l=english"
        crawler, requests = self._run_offline({
            ROOT: (b'<a href="/app/98/">Some Demo</a>'
                   b'<a href="/app/98/">Some Game</a>'),
            app: game("Some Game"),
        }, max_games=1)
        self.assertEqual(requests, [ROOT, app])
        self.assertEqual(crawler.games_saved, 1)

    def test_navigation_page_not_saved_even_with_product_links(self):
        app = "https://store.steampowered.com/app/60/?l=english"
        crawler, requests = self._run_offline({
            ROOT: b'<a href="/app/60/">Full Game</a>',
            app: game("Full Game"),
        }, max_games=1)
        self.assertEqual(requests, [ROOT, app])
        self.assertEqual({p.name for p in crawler.run_dir.glob("*.html")}, {"app_60.html"})
        events = [json.loads(row) for row in (crawler.run_dir / "manifest.jsonl").read_text().splitlines()]
        self.assertEqual(events[0]["result"], "navigation_page")

    def test_rejected_product_can_still_yield_new_game_links(self):
        dlc = "https://store.steampowered.com/app/51/?l=english"
        game2 = "https://store.steampowered.com/app/52/?l=english"
        crawler, requests = self._run_offline({
            ROOT: b'<a href="/app/51/">Bonus</a>',
            dlc: game("Bonus", b'<div class="game_area_dlc_bubble"></div>'
                                 b'<a href="/app/52/">New Game</a>'),
            game2: game("New Game"),
        }, max_games=1)
        self.assertEqual(requests, [ROOT, dlc, game2])
        self.assertEqual(crawler.games_saved, 1)
        self.assertEqual(crawler.excluded_by_type, {"dlc": 1})

    def test_navigation_limit_is_applied(self):
        category = "https://store.steampowered.com/category/action/?l=english"
        app = "https://store.steampowered.com/app/80/?l=english"
        crawler, requests = self._run_offline({
            ROOT: b'<a href="/category/action/">Action</a><a href="/app/80/">A</a>',
            app: game("A"),
        }, max_games=1, max_navigation=1)
        self.assertEqual(requests, [ROOT, app])
        self.assertNotIn(category, crawler.visited)
        self.assertEqual(crawler.games_saved, 1)

    def test_resume_after_keyboard_interrupt(self):
        """Resuming should use the existing frontier rather than crawling the seed again."""
        app1 = "https://store.steampowered.com/app/301/?l=english"
        app2 = "https://store.steampowered.com/app/302/?l=english"
        root_html = b'<a href="/app/301/">A</a><a href="/app/302/">B</a>'
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.object(crawler_module, "OUTPUT_DIR", Path(tmp.name)), \
             patch.object(crawler_module, "SEED_URLS", [ROOT]), \
             patch.object(crawler_module, "MAX_GAME_PAGES", 2):
            first = crawler_module.SteamCrawler()
            first._load_robots = lambda: None
            fetched_first = []

            def interrupt_on_app(url):
                fetched_first.append(url)
                if url != ROOT:
                    raise KeyboardInterrupt()
                return root_html

            first._fetch = interrupt_on_app
            first.run()
            self.assertEqual(fetched_first, [ROOT, app1])
            checkpoint = json.loads((first.run_dir / "state.json").read_text())
            self.assertEqual(checkpoint["queue"], [app1, app2])
            self.assertIn(ROOT, checkpoint["visited"])

            resumed = crawler_module.SteamCrawler(resume_dir=first.run_dir)
            resumed._load_robots = lambda: None
            fetched_resumed = []

            def finish_crawl(url):
                fetched_resumed.append(url)
                return {app1: game("A"), app2: game("B")}[url]

            resumed._fetch = finish_crawl
            resumed.run()

            self.assertEqual(fetched_resumed, [app1, app2])
            self.assertEqual(resumed.run_dir, first.run_dir.resolve())
            self.assertEqual(resumed.games_saved, 2)
            self.assertEqual(resumed.bytes_saved, len(game("A")) + len(game("B")))
            self.assertEqual(len(list(resumed.run_dir.glob("app_*.html"))), 2)
            checkpoint = json.loads((resumed.run_dir / "state.json").read_text())
            self.assertEqual(checkpoint["queue"], [])
            self.assertEqual(len(checkpoint["visited"]), 3)

    def test_resume_after_html_saved_but_before_checkpoint(self):
        """Recover a previously written HTML without fetching it from Steam again."""
        app1 = "https://store.steampowered.com/app/401/?l=english"
        app2 = "https://store.steampowered.com/app/402/?l=english"
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.object(crawler_module, "OUTPUT_DIR", Path(tmp.name)), \
             patch.object(crawler_module, "SEED_URLS", [ROOT]), \
             patch.object(crawler_module, "MAX_GAME_PAGES", 2):
            first = crawler_module.SteamCrawler()
            first._load_robots = lambda: None
            first._fetch = lambda url: {
                ROOT: b'<a href="/app/401/">A</a><a href="/app/402/">B</a>',
                app1: game("A", b'<a href="/app/402/">B</a>'),
            }[url]
            checkpoint = first._checkpoint
            interrupted = [False]

            def stop_after_file_save():
                if first.games_saved == 1 and not interrupted[0]:
                    interrupted[0] = True
                    raise KeyboardInterrupt()
                return checkpoint()

            first._checkpoint = stop_after_file_save
            first.run()
            self.assertTrue((first.run_dir / "app_401.html").exists())
            saved_before_resume = (first.run_dir / "app_401.html").read_bytes()
            on_disk_checkpoint = json.loads((first.run_dir / "state.json").read_text())
            self.assertEqual(on_disk_checkpoint["queue"][0], app1)

            resumed = crawler_module.SteamCrawler(resume_dir=first.run_dir)
            self.assertEqual(resumed.games_saved, 1)
            resumed._load_robots = lambda: None
            requests_after_resume = []

            def get_second_app(url):
                requests_after_resume.append(url)
                return {app2: game("B")}[url]

            resumed._fetch = get_second_app
            resumed.run()
            self.assertEqual(requests_after_resume, [app2])
            self.assertEqual((resumed.run_dir / "app_401.html").read_bytes(), saved_before_resume)
            self.assertEqual(resumed.games_saved, 2)
            self.assertEqual(resumed.visited, {ROOT, app1, app2})

    def test_increase_limit_when_resuming(self):
        """A crawl that reached the old page limit can later be extended."""
        app1 = "https://store.steampowered.com/app/501/?l=english"
        app2 = "https://store.steampowered.com/app/502/?l=english"
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.object(crawler_module, "OUTPUT_DIR", Path(tmp.name)), \
             patch.object(crawler_module, "SEED_URLS", [ROOT]), \
             patch.object(crawler_module, "MAX_GAME_PAGES", 1):
            first = crawler_module.SteamCrawler()
            first._load_robots = lambda: None
            first._fetch = lambda url: {
                ROOT: b'<a href="/app/501/">A</a><a href="/app/502/">B</a>',
                app1: game("A"),
            }[url]
            first.run()
            self.assertEqual(first.games_saved, 1)
        with patch.object(crawler_module, "MAX_GAME_PAGES", 2):
            resumed = crawler_module.SteamCrawler(resume_dir=first.run_dir)
            resumed._load_robots = lambda: None
            called = []

            def fetch_next(url):
                called.append(url)
                self.assertEqual(url, app2)
                return game("B")

            resumed._fetch = fetch_next
            resumed.run()
            self.assertEqual(called, [app2])
            self.assertEqual(resumed.games_saved, 2)

    def test_old_run_without_checkpoint_cannot_be_resumed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with self.assertRaisesRegex(RuntimeError, "missing or invalid checkpoint"):
            crawler_module.SteamCrawler(resume_dir=tmp.name)


if __name__ == "__main__":
    unittest.main()
