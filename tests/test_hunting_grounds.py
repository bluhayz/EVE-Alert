"""Tests for evealert.tools.hunting_grounds (#242, v7.4) -- group and
system hunting-ground analytics built entirely from local
combat_activity/pilot_history data (zero network I/O in this module)."""

import os
import shutil
import tempfile
import time
import unittest


class HuntingGroundsTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(
            os.path.join(self.temp_dir, "pilot_history.db")
        )
        os.environ["EVEALERT_COMBAT_ACTIVITY_PATH"] = str(
            os.path.join(self.temp_dir, "combat_activity.db")
        )

    def tearDown(self):
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        os.environ.pop("EVEALERT_COMBAT_ACTIVITY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)


def _hour_anchor(hour_utc: int) -> float:
    """Return a unix timestamp landing exactly at HH:00:00 UTC of
    *hour_utc*, computed relative to the real current time so tests don't
    depend on wall-clock time at run time."""
    now = time.time()
    tm = time.gmtime(now)
    return now - (tm.tm_hour - hour_utc) * 3600 - tm.tm_min * 60 - tm.tm_sec


def _seed_hour(system_name, hour_utc, count, *, pilot="Bad Guy", start_kmid=1, role="attacker", gang_size=None):
    from evealert.tools.combat_activity_store import record_activity

    base = _hour_anchor(hour_utc)
    for i in range(count):
        record_activity(
            start_kmid + i, pilot, role=role, ship_name="Sabre",
            system_name=system_name, gang_size=gang_size, occurred_at=base + i,
        )


class _FakeCache:
    """Async stand-in for UniverseCache -- system name doubles as ID."""

    def __init__(self, neighbors: dict | None = None, fail: bool = False):
        self.neighbors = neighbors or {}
        self.fail = fail

    async def get_system_id(self, name):
        if self.fail:
            raise RuntimeError("boom")
        return name

    async def get_systems_within_jumps(self, origin_id, max_jumps):
        return self.neighbors.get(origin_id, {})

    async def get_system_name(self, system_id):
        return system_id


class GroupActivityTests(HuntingGroundsTestCase):
    def test_unknown_group_returns_none(self):
        from evealert.tools.hunting_grounds import group_activity

        self.assertIsNone(group_activity("Snuffed Out"))

    def test_group_with_no_kills_returns_none(self):
        from evealert.tools.pilot_history_store import record_sighting
        from evealert.tools.hunting_grounds import group_activity

        record_sighting("Some Guy", source="local", corp="Snuffed Out")
        self.assertIsNone(group_activity("Snuffed Out"))

    def test_correct_top_systems_pilots_gang(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.pilot_history_store import record_sighting
        from evealert.tools.hunting_grounds import group_activity

        record_sighting("Alice", source="local", corp="Snuffed Out")
        record_sighting("Bob", source="local", alliance="Snuffed Out")

        now = time.time()
        # Alice: 3 kills in Jita, gang 4
        for i in range(3):
            record_activity(100 + i, "Alice", role="attacker", system_name="Jita",
                             gang_size=4, occurred_at=now - i)
        # Bob: 1 kill in Amarr, gang 4
        record_activity(200, "Bob", role="attacker", system_name="Amarr",
                         gang_size=4, occurred_at=now)
        # A loss shouldn't count toward top_pilots/top_systems (attacker-only)
        record_activity(300, "Alice", role="victim", system_name="Dodixie", occurred_at=now)

        result = group_activity("Snuffed Out")
        self.assertIsNotNone(result)
        self.assertEqual(result.top_systems[0], ("Jita", 3))
        self.assertEqual(dict(result.top_pilots), {"Alice": 3, "Bob": 1})
        self.assertEqual(result.avg_gang_size, 4.0)
        self.assertEqual(result.kills_30d, 4)

    def test_case_insensitive_corp_match(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.pilot_history_store import record_sighting
        from evealert.tools.hunting_grounds import group_activity

        record_sighting("Alice", source="local", corp="Snuffed Out")
        record_activity(1, "Alice", role="attacker", system_name="Jita")

        result = group_activity("snuffed out")
        self.assertIsNotNone(result)


class ClassifyTrendTests(unittest.TestCase):
    def test_no_kills_is_insufficient_data(self):
        from evealert.tools.hunting_grounds import _classify_trend

        self.assertEqual(_classify_trend(0, 0), "insufficient data")

    def test_rising(self):
        from evealert.tools.hunting_grounds import _classify_trend

        # 7d rate way above the 30d baseline rate
        self.assertEqual(_classify_trend(kills_7d=14, kills_30d=15), "rising")

    def test_falling(self):
        from evealert.tools.hunting_grounds import _classify_trend

        self.assertEqual(_classify_trend(kills_7d=1, kills_30d=30), "falling")

    def test_steady(self):
        from evealert.tools.hunting_grounds import _classify_trend

        # ~same daily rate over both windows
        self.assertEqual(_classify_trend(kills_7d=7, kills_30d=30), "steady")


class SystemDangerWindowsTests(HuntingGroundsTestCase):
    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_insufficient_data_never_flags_danger(self):
        from evealert.tools.hunting_grounds import system_danger_windows

        _seed_hour("Jita", hour_utc=time.gmtime().tm_hour, count=3)
        result = self._run(system_danger_windows("Jita", cache=_FakeCache()))
        self.assertFalse(result.danger_now)

    def test_current_hour_dominant_flags_danger(self):
        from evealert.tools.hunting_grounds import system_danger_windows

        current_hour = time.gmtime().tm_hour
        _seed_hour("Jita", hour_utc=current_hour, count=15, start_kmid=1)
        # Spread a handful of kills across other hours so the histogram
        # isn't perfectly flat/all-in-one-bucket.
        _seed_hour("Jita", hour_utc=(current_hour + 6) % 24, count=1, start_kmid=1000)
        result = self._run(system_danger_windows("Jita", cache=_FakeCache()))
        self.assertTrue(result.danger_now)
        self.assertEqual(result.current_hour_percentile, 100.0)

    def test_off_peak_hour_not_flagged(self):
        from evealert.tools.hunting_grounds import system_danger_windows

        current_hour = time.gmtime().tm_hour
        busy_hour = (current_hour + 12) % 24
        _seed_hour("Jita", hour_utc=busy_hour, count=20, start_kmid=1)
        result = self._run(system_danger_windows("Jita", cache=_FakeCache()))
        self.assertFalse(result.danger_now)

    def test_neighbor_activity_included(self):
        from evealert.tools.hunting_grounds import system_danger_windows

        current_hour = time.gmtime().tm_hour
        _seed_hour("Jita", hour_utc=current_hour, count=5, start_kmid=1)
        _seed_hour("Perimeter", hour_utc=current_hour, count=5, start_kmid=1000)
        cache = _FakeCache(neighbors={"Jita": {"Perimeter": 1}})
        result = self._run(system_danger_windows("Jita", cache=cache))
        self.assertEqual(sum(result.hour_histogram), 10)

    def test_cache_failure_falls_back_to_origin_only(self):
        from evealert.tools.hunting_grounds import system_danger_windows

        current_hour = time.gmtime().tm_hour
        _seed_hour("Jita", hour_utc=current_hour, count=15, start_kmid=1)
        result = self._run(system_danger_windows("Jita", cache=_FakeCache(fail=True)))
        self.assertEqual(sum(result.hour_histogram), 15)

    def test_hot_window_and_pct_computed(self):
        from evealert.tools.hunting_grounds import system_danger_windows

        current_hour = time.gmtime().tm_hour
        _seed_hour("Jita", hour_utc=current_hour, count=20, start_kmid=1)
        result = self._run(system_danger_windows("Jita", cache=_FakeCache()))
        self.assertIsNotNone(result.hot_window)
        self.assertEqual(result.hot_window_pct, 100.0)

    def test_zero_data_returns_empty_shape_not_none(self):
        from evealert.tools.hunting_grounds import system_danger_windows

        result = self._run(system_danger_windows("Nowhere", cache=_FakeCache()))
        self.assertEqual(result.system_name, "Nowhere")
        self.assertFalse(result.danger_now)
        self.assertIsNone(result.hot_window)


if __name__ == "__main__":
    unittest.main()
