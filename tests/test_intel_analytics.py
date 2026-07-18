"""Tests for evealert.tools.intel_analytics (#244, v7.4) -- the
Intel Analytics UI's search/ranking backend."""

import os
import shutil
import tempfile
import time
import unittest


class IntelAnalyticsTestCase(unittest.TestCase):
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


class SearchPilotNamesTests(IntelAnalyticsTestCase):
    def test_empty_query_returns_empty_list(self):
        from evealert.tools.intel_analytics import search_pilot_names

        self.assertEqual(search_pilot_names(""), [])
        self.assertEqual(search_pilot_names("   "), [])

    def test_case_insensitive_partial_match(self):
        from evealert.tools.pilot_history_store import record_sighting
        from evealert.tools.intel_analytics import search_pilot_names

        record_sighting("Bad Guy Jones", source="local")
        record_sighting("Someone Else", source="local")

        self.assertEqual(search_pilot_names("bad guy"), ["Bad Guy Jones"])
        self.assertEqual(search_pilot_names("JONES"), ["Bad Guy Jones"])

    def test_unknown_query_returns_empty_list(self):
        from evealert.tools.intel_analytics import search_pilot_names

        self.assertEqual(search_pilot_names("Nobody"), [])

    def test_combat_activity_only_pilot_is_found(self):
        """A pilot recorded only via the R2Z2/watchlist path (no
        pilot_history sighting at all) must still be searchable."""
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.intel_analytics import search_pilot_names

        record_activity(1, "Watchlist Only Guy", role="attacker")
        self.assertEqual(search_pilot_names("watchlist only"), ["Watchlist Only Guy"])

    def test_results_deduplicated_and_sorted(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.pilot_history_store import record_sighting
        from evealert.tools.intel_analytics import search_pilot_names

        record_sighting("Zed Guy", source="local")
        record_sighting("Alpha Guy", source="local")
        record_activity(1, "Zed Guy", role="attacker")  # same pilot, both stores

        self.assertEqual(search_pilot_names("guy"), ["Alpha Guy", "Zed Guy"])


class TopHostilesTests(IntelAnalyticsTestCase):
    def test_no_data_returns_empty_list(self):
        from evealert.tools.intel_analytics import top_hostiles

        self.assertEqual(top_hostiles(), [])

    def test_recent_encounter_outranks_older_frequent_one(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.intel_analytics import top_hostiles

        now = time.time()
        # "Frequent But Stale": 5 kills near the edge of the 30d window.
        for i in range(5):
            record_activity(
                100 + i, "Frequent But Stale", role="attacker",
                occurred_at=now - 29 * 86400 - i,
            )
        # "Fresh Guy": 1 kill just now.
        record_activity(200, "Fresh Guy", role="attacker", occurred_at=now)

        results = top_hostiles()
        names = [e.pilot_name for e in results]
        self.assertIn("Fresh Guy", names)
        self.assertIn("Frequent But Stale", names)
        fresh_score = next(e.score for e in results if e.pilot_name == "Fresh Guy")
        stale_score = next(e.score for e in results if e.pilot_name == "Frequent But Stale")
        self.assertGreater(fresh_score, stale_score)

    def test_top_ship_and_corp_and_encounters_populated(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.pilot_history_store import record_sighting
        from evealert.tools.intel_analytics import top_hostiles

        record_sighting("Bad Guy", source="local", corp="Evil Corp")
        record_activity(1, "Bad Guy", role="attacker", ship_name="Sabre")
        record_activity(2, "Bad Guy", role="attacker", ship_name="Sabre")
        record_activity(3, "Bad Guy", role="attacker", ship_name="Loki")

        entry = top_hostiles()[0]
        self.assertEqual(entry.pilot_name, "Bad Guy")
        self.assertEqual(entry.corp, "Evil Corp")
        self.assertEqual(entry.top_ship, "Sabre")
        self.assertEqual(entry.encounters, 4)  # 1 sighting + 3 activity rows

    def test_outside_window_excluded(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.intel_analytics import top_hostiles

        record_activity(1, "Ancient History", role="attacker", occurred_at=time.time() - 60 * 86400)
        self.assertEqual(top_hostiles(), [])

    def test_respects_limit(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.intel_analytics import top_hostiles

        for i in range(5):
            record_activity(i, f"Pilot {i}", role="attacker")
        self.assertEqual(len(top_hostiles(limit=2)), 2)

    def test_trend_up_for_recent_only_activity(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.intel_analytics import top_hostiles

        now = time.time()
        for i in range(3):
            record_activity(i, "Rising Guy", role="attacker", occurred_at=now - i)
        entry = top_hostiles()[0]
        self.assertEqual(entry.trend, "up")

    def test_trend_down_for_older_only_activity(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.intel_analytics import top_hostiles

        now = time.time()
        for i in range(3):
            record_activity(i, "Fading Guy", role="attacker", occurred_at=now - 29 * 86400 - i)
        entry = top_hostiles()[0]
        self.assertEqual(entry.trend, "down")


if __name__ == "__main__":
    unittest.main()
