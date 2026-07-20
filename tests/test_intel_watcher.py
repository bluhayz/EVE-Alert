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


class TestDiscoverChannels(unittest.TestCase):
    """#191: discover_channels() -- channel names derived from log filenames."""

    def test_matches_the_acceptance_criterion_example(self):
        from evealert.tools.intel_watcher import discover_channels

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "Intel_20240501_153022.txt").write_text("x")
            (d / "Local_D7-ZAC_20240501_090000.txt").write_text("x")
            (d / "Alliance_20240501_090000.txt").write_text("x")

            result = discover_channels(d)

        self.assertEqual(result, ["Alliance", "Intel", "Local_D7-ZAC"])

    def test_multi_underscore_channel_name_kept_intact(self):
        from evealert.tools.intel_watcher import discover_channels

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "Local_D7-ZAC_20240501_090000.txt").write_text("x")
            result = discover_channels(d)
        self.assertEqual(result, ["Local_D7-ZAC"])

    def test_mixed_case_variants_deduplicate_to_one_entry(self):
        from evealert.tools.intel_watcher import discover_channels

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "Intel_20240501_100000.txt").write_text("x")
            (d / "Intel_20240502_090000.txt").write_text("x")
            (d / "intel_20240503_080000.txt").write_text("x")

            result = discover_channels(d)

        self.assertEqual(len(result), 1)
        # "Intel" appears twice, "intel" once -- most-common casing wins.
        self.assertEqual(result[0], "Intel")

    def test_empty_directory_returns_empty_list(self):
        from evealert.tools.intel_watcher import discover_channels

        with tempfile.TemporaryDirectory() as tmpdir:
            result = discover_channels(Path(tmpdir))
        self.assertEqual(result, [])

    def test_nonexistent_directory_returns_empty_list_not_error(self):
        from evealert.tools.intel_watcher import discover_channels

        result = discover_channels(Path("/nonexistent/path/xyz"))
        self.assertEqual(result, [])

    def test_non_log_files_ignored(self):
        from evealert.tools.intel_watcher import discover_channels

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "Intel_20240501_090000.txt").write_text("x")
            (d / "readme.md").write_text("not a log")
            (d / "screenshot.jpg").write_text("not a log")

            result = discover_channels(d)

        self.assertEqual(result, ["Intel"])

    def test_files_without_date_time_suffix_ignored(self):
        from evealert.tools.intel_watcher import discover_channels

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "Intel_20240501_090000.txt").write_text("x")
            (d / "readme.txt").write_text("no date suffix")
            (d / "Intel.txt").write_text("no date suffix either")

            result = discover_channels(d)

        self.assertEqual(result, ["Intel"])

    def test_results_are_sorted_alphabetically(self):
        from evealert.tools.intel_watcher import discover_channels

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "Zeta_20240501_090000.txt").write_text("x")
            (d / "Alpha_20240501_090000.txt").write_text("x")
            (d / "Mid_20240501_090000.txt").write_text("x")

            result = discover_channels(d)

        self.assertEqual(result, ["Alpha", "Mid", "Zeta"])

    def test_real_eve_filenames_with_owner_id_suffix_are_discovered(self):
        """#226: real EVE clients append a trailing '_<ownerID>' segment
        before '.txt' -- '<Channel>_<YYYYMMDD>_<HHMMSS>_<ownerID>.txt'.
        The old regex, anchored on '_YYYYMMDD_HHMMSS.txt$', never matched
        these real filenames, so discover_channels() silently returned []."""
        from evealert.tools.intel_watcher import discover_channels

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "I. Ftn Intel_20260718_120036_620084186.txt").write_text("x")
            (d / "I. C Ring Intel_20260718_120036_620084186.txt").write_text("x")

            result = discover_channels(d)

        self.assertEqual(result, ["I. C Ring Intel", "I. Ftn Intel"])


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


class TestIntelWatcherPartialLineSplit(unittest.TestCase):
    """#250 regression: a poll landing mid-write (before the trailing
    newline is flushed) must not forward the truncated line, and must
    not lose or duplicate it once the rest arrives."""

    def test_partial_trailing_line_not_forwarded_yet(self):
        from evealert.tools.intel_watcher import IntelWatcher

        received = []
        watcher = IntelWatcher(channel_pattern="Intel", callback=received.append)

        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "Intel_test.txt"
            # "line one" is complete; "line tw" has no trailing newline yet
            # -- simulates a poll landing between the two writes.
            log.write_text("line one\nline tw")
            watcher._log_path = log
            watcher._file_pos = 0

            watcher._tail_once()

        self.assertEqual(received, ["line one"])

    def test_partial_line_completed_next_poll_is_forwarded_once_whole(self):
        from evealert.tools.intel_watcher import IntelWatcher

        received = []
        watcher = IntelWatcher(channel_pattern="Intel", callback=received.append)

        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "Intel_test.txt"
            log.write_text("line one\nline tw")
            watcher._log_path = log
            watcher._file_pos = 0
            watcher._tail_once()  # "line one" only

            # The rest of the line (plus its newline) arrives.
            with open(log, "a", encoding="utf-8") as f:
                f.write("o\nline three\n")
            watcher._tail_once()

        self.assertEqual(received, ["line one", "line two", "line three"])

    def test_no_complete_line_at_all_does_not_advance_file_pos(self):
        from evealert.tools.intel_watcher import IntelWatcher

        received = []
        watcher = IntelWatcher(channel_pattern="Intel", callback=received.append)

        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "Intel_test.txt"
            log.write_text("no newline yet")
            watcher._log_path = log
            watcher._file_pos = 0

            watcher._tail_once()

        self.assertEqual(received, [])
        self.assertEqual(watcher._file_pos, 0)

    def test_utf16_partial_line_not_split(self):
        """The same fix must hold for EVE's real UTF-16 LE chat-log
        encoding, not just the UTF-8 fallback path."""
        from evealert.tools.intel_watcher import IntelWatcher

        received = []
        watcher = IntelWatcher(channel_pattern="Intel", callback=received.append)

        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "Intel_test.txt"
            text = "line one\nline tw"
            log.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
            watcher._log_path = log
            # Real usage never tails from byte 0 of a BOM'd file -- run()
            # seeks to the end of a newly-detected file before tailing.
            # Start past the 2-byte BOM to match.
            watcher._file_pos = 2

            watcher._tail_once()

        self.assertEqual(received, ["line one"])


class TestIntelWatcherChannelTagging(unittest.TestCase):
    """#171: IntelWatcher tags each parsed IntelReport with its channel."""

    def test_report_gets_channel_name_default_from_pattern(self):
        from evealert.tools.intel_watcher import IntelWatcher

        reports = []
        watcher = IntelWatcher(
            channel_pattern="Intel", callback=lambda _: None,
            on_intel=reports.append,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "Intel_test.txt"
            log.write_text(
                "[ 2024.05.01 15:30:22 ] bluhayz > D7-ZAC clr\n"
            )
            watcher._log_path = log
            watcher._file_pos = 0
            watcher._tail_once()

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].channel, "Intel")

    def test_report_uses_explicit_channel_name_when_given(self):
        from evealert.tools.intel_watcher import IntelWatcher

        reports = []
        watcher = IntelWatcher(
            channel_pattern="Alliance", channel_name="NC-INT",
            callback=lambda _: None, on_intel=reports.append,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "Alliance_test.txt"
            log.write_text(
                "[ 2024.05.01 15:30:22 ] bluhayz > D7-ZAC clr\n"
            )
            watcher._log_path = log
            watcher._file_pos = 0
            watcher._tail_once()

        self.assertEqual(reports[0].channel, "NC-INT")


class TestIntelWatcherDedup(unittest.TestCase):
    """#171: an injected is_duplicate check gates both callbacks."""

    def test_duplicate_line_skips_both_callbacks(self):
        from evealert.tools.intel_watcher import IntelWatcher

        received = []
        reports = []
        watcher = IntelWatcher(
            channel_pattern="Intel", callback=received.append,
            on_intel=reports.append, is_duplicate=lambda line: True,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "Intel_test.txt"
            log.write_text(
                "[ 2024.05.01 15:30:22 ] bluhayz > D7-ZAC clr\n"
            )
            watcher._log_path = log
            watcher._file_pos = 0
            watcher._tail_once()

        self.assertEqual(received, [])
        self.assertEqual(reports, [])

    def test_non_duplicate_line_still_fires_both_callbacks(self):
        from evealert.tools.intel_watcher import IntelWatcher

        received = []
        reports = []
        watcher = IntelWatcher(
            channel_pattern="Intel", callback=received.append,
            on_intel=reports.append, is_duplicate=lambda line: False,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "Intel_test.txt"
            log.write_text(
                "[ 2024.05.01 15:30:22 ] bluhayz > D7-ZAC clr\n"
            )
            watcher._log_path = log
            watcher._file_pos = 0
            watcher._tail_once()

        self.assertEqual(len(received), 1)
        self.assertEqual(len(reports), 1)

    def test_is_duplicate_exception_does_not_block_the_line(self):
        """A failing dedup check must never suppress real intel."""
        from evealert.tools.intel_watcher import IntelWatcher

        received = []

        def _raises(line):
            raise RuntimeError("boom")

        watcher = IntelWatcher(
            channel_pattern="Intel", callback=received.append,
            is_duplicate=_raises,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "Intel_test.txt"
            log.write_text("hello\n")
            watcher._log_path = log
            watcher._file_pos = 0
            watcher._tail_once()

        self.assertEqual(received, ["hello"])

    def test_default_none_is_duplicate_treats_everything_as_unique(self):
        """Backward compat: omitting is_duplicate (existing callers)
        behaves exactly like pre-#171 -- nothing is ever skipped."""
        from evealert.tools.intel_watcher import IntelWatcher

        received = []
        watcher = IntelWatcher(channel_pattern="Intel", callback=received.append)

        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / "Intel_test.txt"
            log.write_text("line one\nline one\n")  # even literal repeats
            watcher._log_path = log
            watcher._file_pos = 0
            watcher._tail_once()

        self.assertEqual(received, ["line one", "line one"])


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
