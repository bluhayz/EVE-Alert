"""Tests for evealert.settings.stats_store — persistent lifetime stats and session reports."""

import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch


class TestGetPaths(unittest.TestCase):
    def test_get_stats_path_ends_with_statistics_json(self):
        from evealert.settings.stats_store import get_stats_path

        path = get_stats_path()
        self.assertTrue(path.endswith("statistics.json"))

    def test_get_sessions_dir_exists_after_call(self):
        from evealert.settings.stats_store import get_sessions_dir

        d = get_sessions_dir()
        self.assertIsInstance(d, Path)
        self.assertTrue(d.is_dir())

    def test_get_sessions_dir_honors_stats_path_override(self):
        """#236: without this, every test exercising AlertAgent.stop()
        (which calls save_session_report() unconditionally) wrote a real
        session_YYYYMMDD_HHMMSS.json into the user's actual config
        directory instead of the test's temp dir."""
        import os
        import tempfile

        from evealert.settings.stats_store import get_sessions_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            override = str(Path(tmpdir) / "statistics.json")
            with patch.dict(os.environ, {"EVEALERT_STATS_PATH": override}):
                d = get_sessions_dir()
            self.assertEqual(d, Path(tmpdir) / "sessions")
            self.assertTrue(d.is_dir())


class TestLoadLifetimeStats(unittest.TestCase):
    def test_returns_empty_dict_when_file_missing(self):
        from evealert.settings.stats_store import load_lifetime_stats

        with patch(
            "evealert.settings.stats_store.get_stats_path",
            return_value="/nonexistent/path/stats.json",
        ):
            result = load_lifetime_stats()
        self.assertEqual(result, {})

    def test_returns_empty_dict_on_invalid_json(self, tmp_path=None):
        import tempfile

        from evealert.settings.stats_store import load_lifetime_stats

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("NOT JSON {{{{")
            tmp = f.name

        with patch("evealert.settings.stats_store.get_stats_path", return_value=tmp):
            result = load_lifetime_stats()
        self.assertEqual(result, {})


class TestRoundTrip(unittest.TestCase):
    """save_lifetime_stats then load_lifetime_stats must produce the same values."""

    def _make_stats(self):
        from evealert.statistics import AlarmStatistics

        s = AlarmStatistics()
        s.add_alarm("Enemy")
        s.add_alarm("Enemy")
        s.add_alarm("Faction")
        return s

    def test_save_and_load_round_trip(self):
        import tempfile

        from evealert.settings.stats_store import (
            load_lifetime_stats,
            save_lifetime_stats,
        )

        stats = self._make_stats()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = str(Path(tmpdir) / "statistics.json")
            with patch(
                "evealert.settings.stats_store.get_stats_path", return_value=path
            ):
                save_lifetime_stats(stats)
                data = load_lifetime_stats()

        self.assertEqual(data["total_alarms"], 3)
        self.assertEqual(data["total_by_type"]["Enemy"], 2)
        self.assertEqual(data["total_by_type"]["Faction"], 1)
        self.assertIn("last_saved", data)


class TestSaveSessionReport(unittest.TestCase):
    def _make_stats(self):
        from evealert.statistics import AlarmStatistics

        s = AlarmStatistics()
        s.add_alarm("Enemy")
        return s

    def test_creates_file_with_expected_keys(self):
        import tempfile

        from evealert.settings.stats_store import save_session_report

        stats = self._make_stats()

        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir) / "sessions"
            sessions_dir.mkdir()
            with patch(
                "evealert.settings.stats_store.get_sessions_dir",
                return_value=sessions_dir,
            ):
                dest = save_session_report(stats, time.time())

            # Check inside the temp-dir context so the path still exists
            self.assertTrue(dest.exists())
            with open(dest, encoding="utf-8") as fh:
                data = json.load(fh)

        for key in (
            "session_start",
            "session_end",
            "duration",
            "session_alarms",
            "history",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["session_alarms"], 1)

    def test_respects_stats_path_override_without_mocking_get_sessions_dir(self):
        """#236 end-to-end: this is exactly the path AlertAgent.stop() goes
        through -- EVEALERT_STATS_PATH set (as every test's env is), but
        get_sessions_dir() itself NOT explicitly mocked. Must land in the
        temp dir, not the user's real config directory."""
        import os
        import tempfile

        from evealert.settings.stats_store import save_session_report

        stats = self._make_stats()
        with tempfile.TemporaryDirectory() as tmpdir:
            override = str(Path(tmpdir) / "statistics.json")
            with patch.dict(os.environ, {"EVEALERT_STATS_PATH": override}):
                dest = save_session_report(stats, time.time())
            self.assertEqual(dest.parent, Path(tmpdir) / "sessions")
            self.assertTrue(dest.exists())


class TestListSessionReports(unittest.TestCase):
    def test_returns_sorted_newest_first(self):
        import tempfile

        from evealert.settings.stats_store import list_session_reports

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            # Write files with names that sort in predictable order
            (d / "session_20240101_120000.json").write_text("{}")
            (d / "session_20240102_120000.json").write_text("{}")
            (d / "session_20240103_120000.json").write_text("{}")

            with patch(
                "evealert.settings.stats_store.get_sessions_dir", return_value=d
            ):
                reports = list_session_reports()

        self.assertEqual(len(reports), 3)
        # Newest first (lexicographic desc = chronological desc for this format)
        self.assertIn("20240103", str(reports[0]))

    def test_returns_empty_list_for_empty_dir(self):
        import tempfile

        from evealert.settings.stats_store import list_session_reports

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "evealert.settings.stats_store.get_sessions_dir",
                return_value=Path(tmpdir),
            ):
                reports = list_session_reports()
        self.assertEqual(reports, [])


if __name__ == "__main__":
    unittest.main()
