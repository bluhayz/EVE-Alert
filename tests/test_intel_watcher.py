"""Tests for evealert.tools.intel_watcher — EVE chat log file tailer."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestGetEveChatlogDir(unittest.TestCase):
    def test_returns_none_or_path(self):
        """get_eve_chatlog_dir returns None (no EVE install) or a valid Path."""
        from evealert.tools.intel_watcher import get_eve_chatlog_dir

        result = get_eve_chatlog_dir()
        # Both None and a Path are valid depending on whether EVE is installed
        self.assertTrue(result is None or isinstance(result, Path))


class TestFindIntelLog(unittest.TestCase):
    def test_returns_none_for_empty_directory(self):
        from evealert.tools.intel_watcher import find_intel_log

        with tempfile.TemporaryDirectory() as tmpdir:
            result = find_intel_log(Path(tmpdir), "Intel")
        self.assertIsNone(result)

    def test_returns_none_for_nonexistent_directory(self):
        from evealert.tools.intel_watcher import find_intel_log

        result = find_intel_log(Path("/nonexistent/path"), "Intel")
        self.assertIsNone(result)

    def test_finds_matching_log_file(self):
        from evealert.tools.intel_watcher import find_intel_log

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "Intel_20240501_153022.txt").write_text("hello")
            (d / "Local_20240501_153022.txt").write_text("other")

            result = find_intel_log(d, "Intel")
        self.assertIsNotNone(result)
        self.assertIn("Intel", result.name)

    def test_returns_most_recent_when_multiple_match(self):
        from evealert.tools.intel_watcher import find_intel_log

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            old = d / "Intel_20240101_000000.txt"
            new = d / "Intel_20240501_000000.txt"
            old.write_text("old")
            new.write_text("new")
            # Touch new to make it definitively newer
            import time

            time.sleep(0.05)
            new.touch()

            result = find_intel_log(d, "Intel")
        self.assertEqual(result.name, new.name)

    def test_case_insensitive_pattern(self):
        from evealert.tools.intel_watcher import find_intel_log

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "INTEL_20240501_153022.txt").write_text("x")
            result = find_intel_log(d, "intel")
        self.assertIsNotNone(result)


class TestIntelWatcherTailOnce(unittest.TestCase):
    def test_callback_called_for_new_lines(self):
        from evealert.tools.intel_watcher import IntelWatcher

        received = []
        watcher = IntelWatcher(channel_pattern="Intel", callback=received.append)

        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "Intel_test.txt"
            log.write_text("line one\nline two\n")
            watcher._log_path = log
            watcher._file_pos = 0

            watcher._tail_once()

        self.assertIn("line one", received)
        self.assertIn("line two", received)

    def test_empty_lines_not_forwarded(self):
        from evealert.tools.intel_watcher import IntelWatcher

        received = []
        watcher = IntelWatcher(channel_pattern="Intel", callback=received.append)

        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "Intel_test.txt"
            log.write_text("\n\n\n")
            watcher._log_path = log
            watcher._file_pos = 0
            watcher._tail_once()

        self.assertEqual(received, [])

    def test_does_not_reread_already_seen_content(self):
        from evealert.tools.intel_watcher import IntelWatcher

        received = []
        watcher = IntelWatcher(channel_pattern="Intel", callback=received.append)

        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "Intel_test.txt"
            log.write_text("first line\n")
            watcher._log_path = log
            watcher._file_pos = 0

            watcher._tail_once()  # reads "first line"
            watcher._tail_once()  # nothing new

        self.assertEqual(len(received), 1)

    def test_handles_missing_file_gracefully(self):
        from evealert.tools.intel_watcher import IntelWatcher

        watcher = IntelWatcher(channel_pattern="Intel", callback=lambda _: None)
        watcher._log_path = Path("/nonexistent/file.txt")
        watcher._file_pos = 0
        # Should not raise
        watcher._tail_once()


class TestIntelWatcherStop(unittest.TestCase):
    async def _run_watcher(self, watcher):
        """Run the watcher and stop it quickly."""
        task = asyncio.create_task(watcher.run())
        await asyncio.sleep(0.05)
        watcher.stop()
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def test_stop_sets_running_false(self):
        from evealert.tools.intel_watcher import IntelWatcher

        watcher = IntelWatcher(channel_pattern="Intel", callback=lambda _: None)
        watcher._running = True
        watcher.stop()
        self.assertFalse(watcher._running)


if __name__ == "__main__":
    unittest.main()
