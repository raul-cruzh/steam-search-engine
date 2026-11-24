"""Offline regression tests for transient Windows checkpoint file locks."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crawler import crawler as crawler_module

class CheckpointRetryTests(unittest.TestCase):
    def test_transient_permission_error_retries_and_saves(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(crawler_module, "OUTPUT_DIR", Path(tmp)):
            crawler = crawler_module.SteamCrawler()
            crawler.games_saved = 7
            original_replace = os.replace
            calls = []

            def temporarily_locked(src, dst):
                calls.append((src, dst))
                if len(calls) < 3:
                    raise PermissionError(13, "File temporarily locked")
                original_replace(src, dst)

            with patch.object(crawler_module.os, "replace", side_effect=temporarily_locked), \
                 patch.object(crawler_module.time, "sleep") as sleep:
                crawler._checkpoint()

            self.assertEqual(len(calls), 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(json.loads(crawler.state_path.read_text())['games_saved'], 7)
            self.assertFalse((crawler.run_dir / 'state.json.tmp').exists())

    def test_permanent_permission_error_preserves_last_valid_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(crawler_module, "OUTPUT_DIR", Path(tmp)):
            crawler = crawler_module.SteamCrawler()
            previous_state = crawler.state_path.read_bytes()
            crawler.games_saved = 7

            with patch.object(crawler_module.os, "replace", side_effect=PermissionError(13, 'Locked')) as replace, \
                 patch.object(crawler_module.time, "sleep") as sleep:
                with self.assertRaises(PermissionError):
                    crawler._checkpoint()

            self.assertEqual(replace.call_count, 20)
            self.assertEqual(sleep.call_count, 19)
            self.assertEqual(crawler.state_path.read_bytes(), previous_state)
            self.assertTrue((crawler.run_dir / 'state.json.tmp').is_file())


if __name__ == '__main__':
    unittest.main()
