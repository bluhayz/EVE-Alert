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


class PurgeExpiredCacheTests(unittest.TestCase):
    """#177: purge_expired_cache() must evict entries the TTL check would
    already treat as stale on read, not just skip them -- otherwise a
    constellation checked once and never revisited sits in this
    module-level cache for the life of the process."""

    def setUp(self):
        from evealert.tools import threat_heatmap  # noqa: PLC0415
        self._module = threat_heatmap
        self._module._CACHE.clear()  # isolate from other tests' pollution

    def tearDown(self):
        self._module._CACHE.clear()

    def test_purge_removes_expired_entries(self):
        import time  # noqa: PLC0415
        self._module._CACHE[("STALE", 7)] = (
            time.time() - self._module._CACHE_TTL - 1, {}
        )
        removed = self._module.purge_expired_cache()
        self.assertEqual(removed, 1)
        self.assertNotIn(("STALE", 7), self._module._CACHE)

    def test_purge_keeps_fresh_entries(self):
        import time  # noqa: PLC0415
        self._module._CACHE[("FRESH", 7)] = (time.time(), {})
        removed = self._module.purge_expired_cache()
        self.assertEqual(removed, 0)
        self.assertIn(("FRESH", 7), self._module._CACHE)

    def test_purge_returns_zero_on_empty_cache(self):
        self.assertEqual(self._module.purge_expired_cache(), 0)


if __name__ == "__main__":
    unittest.main()
