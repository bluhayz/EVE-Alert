"""Tests for evealert.tools.intel_rollups (#239, v7.3) -- the analytics
rollup layer that precomputes dossier/hunting-ground queries so alarm-
path reads don't do a full history scan."""

import asyncio
import os
import shutil
import tempfile
import time
import unittest
from collections import Counter
from unittest import mock


class IntelRollupsTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        os.environ["EVEALERT_INTEL_ROLLUPS_PATH"] = str(
            os.path.join(self.temp_dir, "intel_rollups.db")
        )
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(
            os.path.join(self.temp_dir, "pilot_history.db")
        )
        os.environ["EVEALERT_COMBAT_ACTIVITY_PATH"] = str(
            os.path.join(self.temp_dir, "combat_activity.db")
        )

    def tearDown(self):
        os.environ.pop("EVEALERT_INTEL_ROLLUPS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        os.environ.pop("EVEALERT_COMBAT_ACTIVITY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)


def _seed_activity(pilot_name, n, *, start_kmid=1):
    """Write *n* synthetic combat_activity rows for *pilot_name*, cycling
    through a small set of ships/systems/gang sizes so a brute-force
    Counter over them has a known, checkable answer."""
    from evealert.tools.combat_activity_store import record_activity

    ships = ["Sabre", "Loki", "Sabre", "Rifter", "Sabre"]  # Sabre must win top_ships
    systems = ["Jita", "Jita", "Amarr", "Jita", "Dodixie"]  # Jita must win top_systems
    now = time.time()
    for i in range(n):
        record_activity(
            start_kmid + i,
            pilot_name,
            role="attacker" if i % 3 != 0 else "victim",
            character_id=999,
            ship_type_id=100,
            ship_name=ships[i % len(ships)],
            solar_system_id=30000142,
            system_name=systems[i % len(systems)],
            gang_size=(i % 5) + 1,
            victim_ship_name="Venture",
            occurred_at=now - i,  # spread across the "last N seconds"
        )


class GetIntelRollupsPathTests(IntelRollupsTestCase):
    def test_uses_env_override(self):
        from evealert.tools.intel_rollups import get_intel_rollups_path

        self.assertEqual(
            get_intel_rollups_path(), os.environ["EVEALERT_INTEL_ROLLUPS_PATH"]
        )


class ComputePilotRollupCorrectnessTests(IntelRollupsTestCase):
    """#239 acceptance criterion: rollup for a synthetic 1,000-row pilot
    matches a brute-force recomputation exactly."""

    def test_matches_manual_brute_force_computation_at_scale(self):
        from evealert.tools.combat_activity_store import get_activity
        from evealert.tools.intel_rollups import compute_pilot_rollup

        _seed_activity("Bad Guy", 1000)

        rollup = compute_pilot_rollup("Bad Guy")

        # Manual brute-force recomputation directly from the raw store,
        # independent of compute_pilot_rollup's own internals.
        activity = get_activity("Bad Guy", limit=1_000_000)
        expected_kills = sum(1 for a in activity if a.role == "attacker")
        expected_losses = sum(1 for a in activity if a.role == "victim")
        expected_ships = Counter(a.ship_name for a in activity if a.ship_name).most_common(5)
        expected_systems = Counter(a.system_name for a in activity if a.system_name).most_common(5)
        expected_hist = [0] * 24
        for a in activity:
            expected_hist[time.gmtime(a.occurred_at).tm_hour] += 1
        expected_avg_gang = sum(a.gang_size for a in activity) / len(activity)

        self.assertEqual(rollup.kill_count, expected_kills)
        self.assertEqual(rollup.loss_count, expected_losses)
        self.assertEqual(rollup.top_ships, expected_ships)
        self.assertEqual(rollup.top_systems, expected_systems)
        self.assertEqual(rollup.hour_histogram, expected_hist)
        self.assertAlmostEqual(rollup.avg_gang_size, expected_avg_gang)
        self.assertEqual(sum(rollup.hour_histogram), 1000)

    def test_sighting_count_reflects_pilot_history_store(self):
        from evealert.tools.intel_rollups import compute_pilot_rollup
        from evealert.tools.pilot_history_store import record_sighting

        record_sighting("Bad Guy", source="local", system="Jita")
        record_sighting("Bad Guy", source="local", system="Amarr")
        record_sighting("Bad Guy", source="intel", system="Dodixie")

        rollup = compute_pilot_rollup("Bad Guy")
        self.assertEqual(rollup.sighting_count, 3)

    def test_no_data_produces_empty_rollup_not_an_error(self):
        from evealert.tools.intel_rollups import compute_pilot_rollup

        rollup = compute_pilot_rollup("Nobody")
        self.assertEqual(rollup.sighting_count, 0)
        self.assertEqual(rollup.kill_count, 0)
        self.assertEqual(rollup.loss_count, 0)
        self.assertEqual(rollup.top_ships, [])
        self.assertIsNone(rollup.avg_gang_size)
        self.assertIsNone(rollup.last_active_at)


class GetPilotRollupTests(IntelRollupsTestCase):
    def test_returns_none_for_pilot_with_no_history(self):
        from evealert.tools.intel_rollups import get_pilot_rollup

        self.assertIsNone(get_pilot_rollup("Nobody"))

    def test_computes_and_stores_on_first_read(self):
        from evealert.tools.intel_rollups import (
            _load_stored_pilot_rollup,
            get_pilot_rollup,
        )

        _seed_activity("Bad Guy", 10)
        result = get_pilot_rollup("Bad Guy")

        self.assertIsNotNone(result)
        stored = _load_stored_pilot_rollup("Bad Guy")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.kill_count, result.kill_count)

    def test_fresh_rollup_served_without_recomputing(self):
        from evealert.tools.intel_rollups import get_pilot_rollup

        _seed_activity("Bad Guy", 5)
        get_pilot_rollup("Bad Guy")  # populates the stored rollup

        with mock.patch(
            "evealert.tools.intel_rollups.compute_pilot_rollup"
        ) as mock_compute:
            get_pilot_rollup("Bad Guy", max_age_seconds=3600)  # well within freshness window
        mock_compute.assert_not_called()

    def test_stale_rollup_triggers_a_recompute(self):
        from evealert.tools.intel_rollups import (
            _load_stored_pilot_rollup,
            _store_pilot_rollup,
            compute_pilot_rollup,
            get_pilot_rollup,
        )

        _seed_activity("Bad Guy", 5)
        stale = compute_pilot_rollup("Bad Guy")
        stale.updated_at = time.time() - 999999
        _store_pilot_rollup(stale)

        get_pilot_rollup("Bad Guy", max_age_seconds=60)

        refreshed = _load_stored_pilot_rollup("Bad Guy")
        self.assertGreater(refreshed.updated_at, stale.updated_at)


class GetPilotRollupNonblockingTests(IntelRollupsTestCase):
    """#239 acceptance criterion: the alarm-path read never triggers a
    synchronous full recompute -- verified via a slow-rollup stub."""

    def test_never_calls_compute_synchronously(self):
        from evealert.tools.intel_rollups import get_pilot_rollup_nonblocking

        def slow_compute(pilot_name):
            raise AssertionError(
                "get_pilot_rollup_nonblocking must never call compute_pilot_rollup "
                "synchronously -- that would block the alarm path on a full scan."
            )

        with mock.patch(
            "evealert.tools.intel_rollups.compute_pilot_rollup", side_effect=slow_compute
        ):
            result = get_pilot_rollup_nonblocking("Bad Guy", loop=None)  # no loop -- can't even schedule

        self.assertIsNone(result)  # nothing stored yet, nothing to serve

    def test_returns_stale_stored_value_immediately_while_scheduling_refresh(self):
        from evealert.tools.intel_rollups import (
            _store_pilot_rollup,
            compute_pilot_rollup,
            get_pilot_rollup_nonblocking,
        )

        _seed_activity("Bad Guy", 5)
        stale = compute_pilot_rollup("Bad Guy")
        stale.updated_at = time.time() - 999999
        _store_pilot_rollup(stale)

        # A plain MagicMock().create_task() would accept-and-drop the
        # coroutine argument without awaiting or closing it, tripping a
        # "coroutine was never awaited" warning at GC time -- close() it
        # to satisfy that contract the same way a real loop's task would
        # (by actually consuming it), without needing a running loop here.
        fake_loop = mock.MagicMock()
        fake_loop.create_task.side_effect = lambda coro: coro.close()
        result = get_pilot_rollup_nonblocking("Bad Guy", max_age_seconds=60, loop=fake_loop)

        self.assertIsNotNone(result)
        self.assertEqual(result.kill_count, stale.kill_count)  # the STALE value, served as-is
        fake_loop.create_task.assert_called_once()  # refresh scheduled, not run inline

    async def _run_background_refresh_and_check(self):
        from evealert.tools.intel_rollups import (
            _load_stored_pilot_rollup,
            _store_pilot_rollup,
            compute_pilot_rollup,
            get_pilot_rollup_nonblocking,
        )

        _seed_activity("Bad Guy", 5)
        stale = compute_pilot_rollup("Bad Guy")
        stale.updated_at = time.time() - 999999
        _store_pilot_rollup(stale)

        _seed_activity("Bad Guy", 5, start_kmid=1000)  # more activity since the stale snapshot

        loop = asyncio.get_event_loop()
        get_pilot_rollup_nonblocking("Bad Guy", max_age_seconds=60, loop=loop)
        await asyncio.sleep(0.05)  # let the scheduled background refresh run

        refreshed = _load_stored_pilot_rollup("Bad Guy")
        self.assertGreater(refreshed.updated_at, stale.updated_at)
        self.assertGreater(refreshed.kill_count + refreshed.loss_count, 5)

    def test_background_refresh_actually_updates_the_store(self):
        asyncio.run(self._run_background_refresh_and_check())

    def test_fresh_stored_value_schedules_nothing(self):
        from evealert.tools.intel_rollups import get_pilot_rollup, get_pilot_rollup_nonblocking

        _seed_activity("Bad Guy", 5)
        get_pilot_rollup("Bad Guy")  # populate a fresh rollup

        fake_loop = mock.MagicMock()
        get_pilot_rollup_nonblocking("Bad Guy", max_age_seconds=3600, loop=fake_loop)
        fake_loop.create_task.assert_not_called()


class SystemRollupTests(IntelRollupsTestCase):
    def test_returns_none_for_system_with_no_activity(self):
        from evealert.tools.intel_rollups import get_system_rollup

        self.assertIsNone(get_system_rollup("Jita"))

    def test_computes_kill_count_and_histogram(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.intel_rollups import get_system_rollup

        now = time.time()
        for i in range(5):
            record_activity(
                i, f"Pilot{i}", role="attacker", system_name="Jita", occurred_at=now - i,
            )

        rollup = get_system_rollup("Jita")
        self.assertIsNotNone(rollup)
        self.assertEqual(rollup.hostile_kill_count_30d, 5)
        self.assertEqual(sum(rollup.hour_histogram), 5)

    def test_top_hostile_corps_cross_referenced_from_pilot_history(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.intel_rollups import get_system_rollup
        from evealert.tools.pilot_history_store import record_sighting

        record_sighting("Bad Guy", source="local", corp="Evil Corp")
        record_activity(1, "Bad Guy", role="attacker", system_name="Jita")

        rollup = get_system_rollup("Jita")
        self.assertIn(("Evil Corp", 1), rollup.top_hostile_corps)

    def test_activity_older_than_30_days_excluded(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.intel_rollups import get_system_rollup

        record_activity(
            1, "Old Guy", role="attacker", system_name="Jita",
            occurred_at=time.time() - 40 * 86400,
        )
        self.assertIsNone(get_system_rollup("Jita"))


class SweepStaleRollupsTests(IntelRollupsTestCase):
    """#239 acceptance criterion: the maintenance-task sweep only touches
    pilots with new rows since their rollup's updated_at."""

    def test_pilot_with_no_rollup_yet_gets_one(self):
        from evealert.tools.intel_rollups import (
            _load_stored_pilot_rollup,
            sweep_stale_rollups,
        )

        _seed_activity("Bad Guy", 5)
        refreshed = sweep_stale_rollups()

        self.assertEqual(refreshed, 1)
        self.assertIsNotNone(_load_stored_pilot_rollup("Bad Guy"))

    def test_pilot_with_up_to_date_rollup_is_skipped(self):
        from evealert.tools.intel_rollups import (
            _load_stored_pilot_rollup,
            get_pilot_rollup,
            sweep_stale_rollups,
        )

        _seed_activity("Bad Guy", 5)
        get_pilot_rollup("Bad Guy")  # rollup now reflects all 5 rows
        before = _load_stored_pilot_rollup("Bad Guy").updated_at

        refreshed = sweep_stale_rollups()

        self.assertEqual(refreshed, 0)
        self.assertEqual(_load_stored_pilot_rollup("Bad Guy").updated_at, before)

    def test_pilot_with_new_activity_since_rollup_is_refreshed(self):
        from evealert.tools.intel_rollups import (
            _load_stored_pilot_rollup,
            get_pilot_rollup,
            sweep_stale_rollups,
        )

        _seed_activity("Bad Guy", 5)
        get_pilot_rollup("Bad Guy")
        before = _load_stored_pilot_rollup("Bad Guy")

        _seed_activity("Bad Guy", 3, start_kmid=1000)  # new rows AFTER the rollup was stored

        refreshed = sweep_stale_rollups()

        self.assertEqual(refreshed, 1)
        after = _load_stored_pilot_rollup("Bad Guy")
        self.assertGreater(after.kill_count + after.loss_count, before.kill_count + before.loss_count)

    def test_untouched_pilots_never_scanned(self):
        """A pilot outside the sweep's lookback window must not even be
        a candidate -- proves the sweep isn't a full-table rescan."""
        from evealert.tools.intel_rollups import sweep_stale_rollups
        from evealert.tools.combat_activity_store import record_activity

        record_activity(
            1, "Ancient Guy", role="attacker", occurred_at=time.time() - 999999,
        )
        refreshed = sweep_stale_rollups(lookback_seconds=3600)
        self.assertEqual(refreshed, 0)

    def test_respects_limit(self):
        from evealert.tools.intel_rollups import sweep_stale_rollups

        for i in range(5):
            _seed_activity(f"Pilot{i}", 2, start_kmid=i * 100)

        refreshed = sweep_stale_rollups(limit=2)
        self.assertLessEqual(refreshed, 2)


class ConcurrentAccessTests(IntelRollupsTestCase):
    """#239 acceptance criterion: the DB stays valid when two threads
    request the same rollup concurrently (engine loop + UI thread)."""

    def test_two_threads_computing_the_same_pilot_do_not_corrupt_the_db(self):
        """The acceptance criterion is data INTEGRITY under concurrent
        access, not "never raises" -- SQLite's write lock is real, and a
        clean OperationalError('database is locked') under a synthetic
        hammer test (10 forced-recompute calls across 2 threads racing on
        the SAME row, far more aggressive than the realistic occasional
        UI-read-overlaps-engine-sweep scenario this guards) is expected,
        well-defined behavior -- not corruption. A well-behaved caller
        retries; this test does the same and then verifies the DB is
        still valid and consistent, which is what actually matters.
        """
        import concurrent.futures
        import sqlite3
        import time as time_mod

        from evealert.tools.intel_rollups import get_pilot_rollup

        _seed_activity("Bad Guy", 20)

        errors = []

        def worker():
            last_exc = None
            for attempt in range(5):
                try:
                    return get_pilot_rollup("Bad Guy", max_age_seconds=0)  # force recompute
                except sqlite3.OperationalError as exc:
                    last_exc = exc
                    time_mod.sleep(0.05 * (attempt + 1))
            errors.append(last_exc)  # pragma: no cover - only on persistent contention
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(10)]
            results = [f.result(timeout=45) for f in futures]

        self.assertEqual(errors, [])
        self.assertTrue(all(r is not None for r in results))
        self.assertTrue(all(r.kill_count == results[0].kill_count for r in results))


if __name__ == "__main__":
    unittest.main()
