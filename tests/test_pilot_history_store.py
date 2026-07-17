"""Tests for evealert.tools.pilot_history_store (#214, v7.0) — the
persistent SQLite pilot-sighting store."""

import os
import shutil
import tempfile
import time
import unittest


class PilotHistoryStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(
            os.path.join(self.temp_dir, "pilot_history.db")
        )

    def tearDown(self):
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class GetPilotHistoryPathTests(PilotHistoryStoreTestCase):
    def test_uses_env_override(self):
        from evealert.tools.pilot_history_store import get_pilot_history_path

        path = get_pilot_history_path()
        self.assertEqual(path, os.environ["EVEALERT_PILOT_HISTORY_PATH"])


class RecordAndGetSightingsTests(PilotHistoryStoreTestCase):
    def test_round_trip(self):
        from evealert.tools.pilot_history_store import get_sightings, record_sighting

        record_sighting(
            "Bad Guy", source="local", system="Jita", ship="Loki",
            corp="Evil Corp", alliance="Evil Alliance",
        )
        results = get_sightings("Bad Guy")
        self.assertEqual(len(results), 1)
        s = results[0]
        self.assertEqual(s.pilot_name, "Bad Guy")
        self.assertEqual(s.system, "Jita")
        self.assertEqual(s.ship, "Loki")
        self.assertEqual(s.source, "local")
        self.assertEqual(s.corp, "Evil Corp")
        self.assertEqual(s.alliance, "Evil Alliance")
        self.assertIsInstance(s.seen_at, float)

    def test_seen_at_defaults_to_now(self):
        from evealert.tools.pilot_history_store import get_sightings, record_sighting

        before = time.time()
        record_sighting("Bad Guy", source="intel")
        after = time.time()
        s = get_sightings("Bad Guy")[0]
        self.assertGreaterEqual(s.seen_at, before)
        self.assertLessEqual(s.seen_at, after)

    def test_returns_newest_first(self):
        from evealert.tools.pilot_history_store import get_sightings, record_sighting

        record_sighting("Bad Guy", source="local", system="Jita", seen_at=1000.0)
        record_sighting("Bad Guy", source="local", system="Amarr", seen_at=3000.0)
        record_sighting("Bad Guy", source="local", system="Dodixie", seen_at=2000.0)
        results = get_sightings("Bad Guy")
        self.assertEqual([s.system for s in results], ["Amarr", "Dodixie", "Jita"])

    def test_since_filter(self):
        from evealert.tools.pilot_history_store import get_sightings, record_sighting

        record_sighting("Bad Guy", source="local", system="Old", seen_at=1000.0)
        record_sighting("Bad Guy", source="local", system="New", seen_at=5000.0)
        results = get_sightings("Bad Guy", since=3000.0)
        self.assertEqual([s.system for s in results], ["New"])

    def test_limit_respected(self):
        from evealert.tools.pilot_history_store import get_sightings, record_sighting

        for i in range(10):
            record_sighting("Bad Guy", source="local", seen_at=float(i))
        results = get_sightings("Bad Guy", limit=3)
        self.assertEqual(len(results), 3)

    def test_only_returns_matching_pilot(self):
        from evealert.tools.pilot_history_store import get_sightings, record_sighting

        record_sighting("Bad Guy", source="local")
        record_sighting("Other Guy", source="local")
        results = get_sightings("Bad Guy")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].pilot_name, "Bad Guy")

    def test_no_sightings_returns_empty_list(self):
        from evealert.tools.pilot_history_store import get_sightings

        self.assertEqual(get_sightings("Nobody"), [])

    def test_rejects_invalid_source(self):
        from evealert.tools.pilot_history_store import record_sighting

        with self.assertRaises(ValueError):
            record_sighting("Bad Guy", source="not_a_real_source")


class PruneOlderThanTests(PilotHistoryStoreTestCase):
    def test_removes_only_rows_older_than_window(self):
        from evealert.tools.pilot_history_store import (
            get_sightings,
            prune_older_than,
            record_sighting,
        )

        now = time.time()
        record_sighting("Bad Guy", source="local", system="Old", seen_at=now - 200 * 86400)
        record_sighting("Bad Guy", source="local", system="Recent", seen_at=now - 5 * 86400)

        deleted = prune_older_than(180)

        self.assertEqual(deleted, 1)
        remaining = get_sightings("Bad Guy")
        self.assertEqual([s.system for s in remaining], ["Recent"])

    def test_zero_or_negative_days_keeps_everything(self):
        from evealert.tools.pilot_history_store import (
            get_sightings,
            prune_older_than,
            record_sighting,
        )

        record_sighting("Bad Guy", source="local", seen_at=time.time() - 10000 * 86400)

        self.assertEqual(prune_older_than(0), 0)
        self.assertEqual(prune_older_than(-5), 0)
        self.assertEqual(len(get_sightings("Bad Guy")), 1)


if __name__ == "__main__":
    unittest.main()
