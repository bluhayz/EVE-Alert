"""Tests for evealert.tools.threat_heatmap (#148)."""

import unittest

from evealert.tools.threat_heatmap import _build_entry, HeatmapEntry


class BuildEntryTests(unittest.TestCase):
    def _kill(self, hour: int) -> dict:
        return {"killmail_time": f"2024-05-01T{hour:02d}:00:00Z"}

    def test_empty_kills(self):
        entry = _build_entry("D7-ZAC", [], 7)
        self.assertEqual(entry.kills_7d, 0)
        self.assertEqual(entry.kills_24h, 0)
        self.assertEqual(sum(entry.kill_histogram), 0)

    def test_kill_count(self):
        kills = [self._kill(h) for h in [10, 10, 15, 22]]
        entry = _build_entry("D7-ZAC", kills, 7)
        self.assertEqual(entry.kills_7d, 4)

    def test_histogram_bins(self):
        kills = [self._kill(h) for h in [10, 10, 10]]
        entry = _build_entry("D7-ZAC", kills, 7)
        self.assertEqual(entry.kill_histogram[10], 3)
        self.assertEqual(sum(entry.kill_histogram), 3)

    def test_peak_hour(self):
        kills = [self._kill(h) for h in [10, 10, 15]]
        entry = _build_entry("D7-ZAC", kills, 7)
        self.assertEqual(entry.peak_hour_utc, 10)

    def test_system_name_preserved(self):
        entry = _build_entry("1DQ1-A", [], 7)
        self.assertEqual(entry.system, "1DQ1-A")

    def test_heatmap_entry_fields(self):
        entry = HeatmapEntry(
            system="D7-ZAC", kills_24h=2, kills_7d=10,
            peak_hour_utc=15, kill_histogram=[0]*24,
        )
        self.assertEqual(len(entry.kill_histogram), 24)
        self.assertEqual(entry.peak_hour_utc, 15)


if __name__ == "__main__":
    unittest.main()
