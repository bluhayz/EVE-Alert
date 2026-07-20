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


class CharacterIdAndMigrationTests(PilotHistoryStoreTestCase):
    """#238: schema v1 -> v2 -- nullable character_id column plus the
    composite indexes analytics queries need."""

    def test_character_id_round_trips(self):
        from evealert.tools.pilot_history_store import get_sightings, record_sighting

        record_sighting("Bad Guy", source="local", character_id=987654)
        result = get_sightings("Bad Guy")[0]
        self.assertEqual(result.character_id, 987654)

    def test_character_id_defaults_to_none(self):
        from evealert.tools.pilot_history_store import get_sightings, record_sighting

        record_sighting("Bad Guy", source="intel")  # intel mentions never resolve one
        result = get_sightings("Bad Guy")[0]
        self.assertIsNone(result.character_id)

    def test_existing_v1_database_migrates_without_data_loss(self):
        """A pre-#238 database has no character_id column and no
        composite indexes. Connecting to it must add both in place
        without touching existing rows."""
        import sqlite3

        from evealert.tools.pilot_history_store import (
            get_pilot_history_path,
            get_sightings,
        )

        # Build a v1-shaped database by hand -- no character_id column,
        # PRAGMA user_version left at its SQLite default of 0.
        v1_conn = sqlite3.connect(get_pilot_history_path())
        v1_conn.executescript("""
            CREATE TABLE sightings (
                id INTEGER PRIMARY KEY,
                pilot_name TEXT NOT NULL,
                system TEXT,
                ship TEXT,
                source TEXT NOT NULL CHECK(source IN ('local', 'intel')),
                corp TEXT,
                alliance TEXT,
                seen_at REAL NOT NULL
            );
        """)
        v1_conn.execute(
            "INSERT INTO sightings (pilot_name, system, ship, source, corp, alliance, seen_at) "
            "VALUES ('Bad Guy', 'Jita', 'Loki', 'local', 'Evil Corp', 'Evil Alliance', 1000.0)"
        )
        v1_conn.commit()
        v1_conn.close()

        # A normal read through the module must migrate in place.
        results = get_sightings("Bad Guy")

        self.assertEqual(len(results), 1)
        row = results[0]
        self.assertEqual(row.pilot_name, "Bad Guy")
        self.assertEqual(row.system, "Jita")
        self.assertEqual(row.ship, "Loki")
        self.assertEqual(row.corp, "Evil Corp")
        self.assertEqual(row.alliance, "Evil Alliance")
        self.assertEqual(row.seen_at, 1000.0)
        self.assertIsNone(row.character_id)  # pre-existing row, never had one

        # Verify the migration actually ran: user_version bumped, indexes exist.
        conn = sqlite3.connect(get_pilot_history_path())
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertGreaterEqual(version, 2)
        index_names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        self.assertIn("idx_sightings_pilot_seen", index_names)
        self.assertIn("idx_sightings_system_seen", index_names)
        conn.close()

    def test_migration_is_idempotent_across_repeated_connections(self):
        from evealert.tools.pilot_history_store import get_sightings, record_sighting

        record_sighting("Bad Guy", source="local", character_id=1)
        get_sightings("Bad Guy")  # second connection -- must not raise or duplicate columns
        get_sightings("Bad Guy")  # third connection
        result = get_sightings("Bad Guy")[0]
        self.assertEqual(result.character_id, 1)


class GetPilotsByCorpOrAllianceTests(PilotHistoryStoreTestCase):
    def test_matches_corp_case_insensitively(self):
        from evealert.tools.pilot_history_store import (
            get_pilots_by_corp_or_alliance,
            record_sighting,
        )

        record_sighting("Alice", source="local", corp="Snuffed Out")
        self.assertEqual(get_pilots_by_corp_or_alliance("snuffed out"), ["Alice"])

    def test_matches_alliance(self):
        from evealert.tools.pilot_history_store import (
            get_pilots_by_corp_or_alliance,
            record_sighting,
        )

        record_sighting("Bob", source="local", alliance="Test Alliance Please Ignore")
        self.assertEqual(
            get_pilots_by_corp_or_alliance("Test Alliance Please Ignore"), ["Bob"]
        )

    def test_no_match_returns_empty_list(self):
        from evealert.tools.pilot_history_store import get_pilots_by_corp_or_alliance

        self.assertEqual(get_pilots_by_corp_or_alliance("Nobody Corp"), [])

    def test_distinct_pilots_only(self):
        from evealert.tools.pilot_history_store import (
            get_pilots_by_corp_or_alliance,
            record_sighting,
        )

        record_sighting("Alice", source="local", corp="Snuffed Out")
        record_sighting("Alice", source="intel", corp="Snuffed Out")
        self.assertEqual(get_pilots_by_corp_or_alliance("Snuffed Out"), ["Alice"])


class SearchPilotNamesTests(PilotHistoryStoreTestCase):
    def test_empty_query_returns_empty_list(self):
        from evealert.tools.pilot_history_store import search_pilot_names

        self.assertEqual(search_pilot_names(""), [])
        self.assertEqual(search_pilot_names("   "), [])

    def test_case_insensitive_partial_match(self):
        from evealert.tools.pilot_history_store import record_sighting, search_pilot_names

        record_sighting("Bad Guy Jones", source="local")
        record_sighting("Someone Else", source="local")

        self.assertEqual(search_pilot_names("bad guy"), ["Bad Guy Jones"])

    def test_results_sorted_alphabetically(self):
        from evealert.tools.pilot_history_store import record_sighting, search_pilot_names

        record_sighting("Zed Guy", source="local")
        record_sighting("Alpha Guy", source="local")
        self.assertEqual(search_pilot_names("guy"), ["Alpha Guy", "Zed Guy"])

    def test_no_match_returns_empty_list(self):
        from evealert.tools.pilot_history_store import search_pilot_names

        self.assertEqual(search_pilot_names("Nobody"), [])

    def test_underscore_is_treated_as_literal_not_wildcard(self):
        """#249 regression: an unescaped "_" in the query used to match
        any single character (SQL LIKE semantics), so searching "_"
        matched every pilot of the right length instead of pilots whose
        name literally contains an underscore."""
        from evealert.tools.pilot_history_store import record_sighting, search_pilot_names

        record_sighting("Bad_Guy", source="local")
        record_sighting("BadXGuy", source="local")  # same length, no underscore

        self.assertEqual(search_pilot_names("Bad_Guy"), ["Bad_Guy"])

    def test_percent_is_treated_as_literal_not_wildcard(self):
        from evealert.tools.pilot_history_store import record_sighting, search_pilot_names

        record_sighting("100% Hostile", source="local")
        record_sighting("Someone Else", source="local")

        self.assertEqual(search_pilot_names("100%"), ["100% Hostile"])


if __name__ == "__main__":
    unittest.main()
