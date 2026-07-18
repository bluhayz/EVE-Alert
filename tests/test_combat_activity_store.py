"""Tests for evealert.tools.combat_activity_store (#237, v7.3) -- the
persistent SQLite combat-activity store fed by the R2Z2 live-kill stream
and the zKillboard backfill."""

import os
import shutil
import tempfile
import time
import unittest

import respx
from httpx import Response


class CombatActivityStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        os.environ["EVEALERT_COMBAT_ACTIVITY_PATH"] = str(
            os.path.join(self.temp_dir, "combat_activity.db")
        )

    def tearDown(self):
        os.environ.pop("EVEALERT_COMBAT_ACTIVITY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class GetCombatActivityPathTests(CombatActivityStoreTestCase):
    def test_uses_env_override(self):
        from evealert.tools.combat_activity_store import get_combat_activity_path

        path = get_combat_activity_path()
        self.assertEqual(path, os.environ["EVEALERT_COMBAT_ACTIVITY_PATH"])


class RecordAndGetActivityTests(CombatActivityStoreTestCase):
    def test_round_trip(self):
        from evealert.tools.combat_activity_store import get_activity, record_activity

        record_activity(
            12345, "Bad Guy", role="attacker", character_id=999,
            ship_type_id=17738, ship_name="Sabre", solar_system_id=30000142,
            system_name="Jita", gang_size=3, victim_ship_name="Venture",
        )
        results = get_activity("Bad Guy")
        self.assertEqual(len(results), 1)
        row = results[0]
        self.assertEqual(row.killmail_id, 12345)
        self.assertEqual(row.pilot_name, "Bad Guy")
        self.assertEqual(row.character_id, 999)
        self.assertEqual(row.role, "attacker")
        self.assertEqual(row.ship_name, "Sabre")
        self.assertEqual(row.system_name, "Jita")
        self.assertEqual(row.gang_size, 3)
        self.assertEqual(row.victim_ship_name, "Venture")
        self.assertIsInstance(row.occurred_at, float)

    def test_occurred_at_defaults_to_now(self):
        from evealert.tools.combat_activity_store import get_activity, record_activity

        before = time.time()
        record_activity(1, "Bad Guy", role="victim")
        after = time.time()
        row = get_activity("Bad Guy")[0]
        self.assertGreaterEqual(row.occurred_at, before)
        self.assertLessEqual(row.occurred_at, after)

    def test_returns_newest_first(self):
        from evealert.tools.combat_activity_store import get_activity, record_activity

        record_activity(1, "Bad Guy", role="attacker", occurred_at=1000.0)
        record_activity(2, "Bad Guy", role="attacker", occurred_at=3000.0)
        record_activity(3, "Bad Guy", role="attacker", occurred_at=2000.0)
        results = get_activity("Bad Guy")
        self.assertEqual([r.killmail_id for r in results], [2, 3, 1])

    def test_since_filter(self):
        from evealert.tools.combat_activity_store import get_activity, record_activity

        record_activity(1, "Bad Guy", role="attacker", occurred_at=1000.0)
        record_activity(2, "Bad Guy", role="attacker", occurred_at=5000.0)
        results = get_activity("Bad Guy", since=3000.0)
        self.assertEqual([r.killmail_id for r in results], [2])

    def test_only_returns_matching_pilot(self):
        from evealert.tools.combat_activity_store import get_activity, record_activity

        record_activity(1, "Bad Guy", role="attacker")
        record_activity(2, "Someone Else", role="attacker")
        results = get_activity("Bad Guy")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].pilot_name, "Bad Guy")

    def test_no_activity_returns_empty_list(self):
        from evealert.tools.combat_activity_store import get_activity

        self.assertEqual(get_activity("Nobody"), [])

    def test_rejects_invalid_role(self):
        from evealert.tools.combat_activity_store import record_activity

        with self.assertRaises(ValueError):
            record_activity(1, "Bad Guy", role="bystander")

    def test_duplicate_killmail_pilot_role_is_idempotent(self):
        """Same killmail/pilot/role recorded twice (e.g. live-stream +
        backfill overlap) must not create a duplicate row."""
        from evealert.tools.combat_activity_store import get_activity, record_activity

        record_activity(1, "Bad Guy", role="attacker", ship_name="Sabre")
        record_activity(1, "Bad Guy", role="attacker", ship_name="Sabre")
        results = get_activity("Bad Guy")
        self.assertEqual(len(results), 1)

    def test_same_killmail_different_role_is_two_rows(self):
        """One pilot can legitimately have two rows for different
        killmails, and two different pilots can each have their own row
        for the SAME killmail (attacker vs victim)."""
        from evealert.tools.combat_activity_store import get_activity, record_activity

        record_activity(1, "Attacker Guy", role="attacker")
        record_activity(1, "Victim Guy", role="victim")
        self.assertEqual(len(get_activity("Attacker Guy")), 1)
        self.assertEqual(len(get_activity("Victim Guy")), 1)


class PruneOlderThanTests(CombatActivityStoreTestCase):
    def test_removes_only_rows_older_than_window(self):
        from evealert.tools.combat_activity_store import (
            get_activity,
            prune_older_than,
            record_activity,
        )

        now = time.time()
        record_activity(1, "Bad Guy", role="attacker", occurred_at=now - 200 * 86400)
        record_activity(2, "Bad Guy", role="attacker", occurred_at=now - 5 * 86400)

        removed = prune_older_than(180)

        self.assertEqual(removed, 1)
        results = get_activity("Bad Guy")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].killmail_id, 2)

    def test_zero_or_negative_days_keeps_everything(self):
        from evealert.tools.combat_activity_store import (
            get_activity,
            prune_older_than,
            record_activity,
        )

        record_activity(1, "Bad Guy", role="attacker", occurred_at=1.0)
        self.assertEqual(prune_older_than(0), 0)
        self.assertEqual(prune_older_than(-5), 0)
        self.assertEqual(len(get_activity("Bad Guy")), 1)


class GetCoAttackersTests(CombatActivityStoreTestCase):
    def test_returns_other_attackers_on_shared_killmails(self):
        from evealert.tools.combat_activity_store import get_co_attackers, record_activity

        record_activity(1, "Bad Guy", role="attacker", ship_name="Sabre")
        record_activity(1, "Wingman", role="attacker", ship_name="Loki")
        record_activity(2, "Bad Guy", role="attacker", ship_name="Sabre")

        rows = get_co_attackers([1, 2])
        self.assertEqual(
            sorted(rows), [(1, "Bad Guy"), (1, "Wingman"), (2, "Bad Guy")]
        )

    def test_victim_role_excluded(self):
        from evealert.tools.combat_activity_store import get_co_attackers, record_activity

        record_activity(1, "Bad Guy", role="attacker", ship_name="Sabre")
        record_activity(1, "Victim Guy", role="victim", ship_name="Venture")

        rows = get_co_attackers([1])
        self.assertEqual(rows, [(1, "Bad Guy")])

    def test_empty_input_returns_empty_list(self):
        from evealert.tools.combat_activity_store import get_co_attackers

        self.assertEqual(get_co_attackers([]), [])

    def test_unmatched_killmail_ids_ignored(self):
        from evealert.tools.combat_activity_store import get_co_attackers, record_activity

        record_activity(1, "Bad Guy", role="attacker", ship_name="Sabre")
        self.assertEqual(get_co_attackers([999]), [])


class SearchPilotNamesTests(CombatActivityStoreTestCase):
    def test_empty_query_returns_empty_list(self):
        from evealert.tools.combat_activity_store import search_pilot_names

        self.assertEqual(search_pilot_names(""), [])
        self.assertEqual(search_pilot_names("   "), [])

    def test_case_insensitive_partial_match(self):
        from evealert.tools.combat_activity_store import record_activity, search_pilot_names

        record_activity(1, "Bad Guy Jones", role="attacker")
        record_activity(2, "Someone Else", role="attacker")

        self.assertEqual(search_pilot_names("bad guy"), ["Bad Guy Jones"])

    def test_no_match_returns_empty_list(self):
        from evealert.tools.combat_activity_store import search_pilot_names

        self.assertEqual(search_pilot_names("Nobody"), [])


class BackfillFromZkillboardTests(CombatActivityStoreTestCase):
    """#237 ingest path 2: on-demand backfill from zKillboard."""

    def _zkb_entry(self, killmail_id, hash_val="abc123"):
        return {"killmail_id": killmail_id, "zkb": {"hash": hash_val}}

    def test_backfill_records_attacker_role(self):
        import asyncio

        from evealert.tools.combat_activity_store import (
            backfill_from_zkillboard,
            get_activity,
        )

        with respx.mock:
            respx.get("https://zkillboard.com/api/characterID/999/").mock(
                return_value=Response(200, json=[self._zkb_entry(1)])
            )
            respx.get("https://esi.evetech.net/latest/killmails/1/abc123/").mock(
                return_value=Response(200, json={
                    "solar_system_id": 30000142,
                    "killmail_time": "2026-07-18T12:00:00Z",
                    "victim": {"character_id": 555, "ship_type_id": 32880},
                    "attackers": [
                        {"character_id": 999, "ship_type_id": 17738},
                        {"character_id": 111},
                    ],
                })
            )
            respx.get("https://esi.evetech.net/latest/universe/types/17738/").mock(
                return_value=Response(200, json={"name": "Sabre"})
            )
            respx.get("https://esi.evetech.net/latest/universe/types/32880/").mock(
                return_value=Response(200, json={"name": "Venture"})
            )
            respx.get("https://esi.evetech.net/latest/universe/systems/30000142/").mock(
                return_value=Response(200, json={"name": "Jita"})
            )
            inserted = asyncio.run(backfill_from_zkillboard(999, "Bad Guy"))

        self.assertEqual(inserted, 1)
        row = get_activity("Bad Guy")[0]
        self.assertEqual(row.role, "attacker")
        self.assertEqual(row.ship_name, "Sabre")
        self.assertEqual(row.system_name, "Jita")
        self.assertEqual(row.victim_ship_name, "Venture")
        self.assertEqual(row.gang_size, 2)

    def test_backfill_records_victim_role(self):
        import asyncio

        from evealert.tools.combat_activity_store import (
            backfill_from_zkillboard,
            get_activity,
        )

        with respx.mock:
            respx.get("https://zkillboard.com/api/characterID/999/").mock(
                return_value=Response(200, json=[self._zkb_entry(2)])
            )
            respx.get("https://esi.evetech.net/latest/killmails/2/abc123/").mock(
                return_value=Response(200, json={
                    "solar_system_id": 30000142,
                    "victim": {"character_id": 999, "ship_type_id": 32880},
                    "attackers": [{"character_id": 111, "ship_type_id": 17738}],
                })
            )
            respx.get("https://esi.evetech.net/latest/universe/types/32880/").mock(
                return_value=Response(200, json={"name": "Venture"})
            )
            respx.get("https://esi.evetech.net/latest/universe/systems/30000142/").mock(
                return_value=Response(200, json={"name": "Jita"})
            )
            inserted = asyncio.run(backfill_from_zkillboard(999, "Bad Guy"))

        self.assertEqual(inserted, 1)
        row = get_activity("Bad Guy")[0]
        self.assertEqual(row.role, "victim")
        self.assertEqual(row.ship_name, "Venture")

    def test_backfill_caps_at_max_rows(self):
        """The zKB list is truncated to _BACKFILL_MAX_ROWS entries before
        any per-killmail resolution work starts -- verified by patching
        the (expensive, network-bound) per-killmail step with a fast stub
        and counting how many times it's actually invoked, rather than
        exercising real ESI resolution for 200 synthetic killmails."""
        import asyncio
        from unittest import mock

        from evealert.tools import combat_activity_store as store_mod
        from evealert.tools.combat_activity_store import backfill_from_zkillboard

        entries = [self._zkb_entry(i) for i in range(1, 201)]  # >> _BACKFILL_MAX_ROWS
        calls = []

        async def fake_backfill_one(entry, character_id, pilot_name):
            calls.append(entry["killmail_id"])
            return True

        with respx.mock:
            respx.get("https://zkillboard.com/api/characterID/999/").mock(
                return_value=Response(200, json=entries)
            )
            with mock.patch.object(store_mod, "_backfill_one_killmail", fake_backfill_one):
                inserted = asyncio.run(backfill_from_zkillboard(999, "Bad Guy"))

        self.assertEqual(inserted, store_mod._BACKFILL_MAX_ROWS)
        self.assertEqual(len(calls), store_mod._BACKFILL_MAX_ROWS)
        self.assertEqual(calls, list(range(1, store_mod._BACKFILL_MAX_ROWS + 1)))

    def test_backfill_returns_zero_on_zkb_failure(self):
        import asyncio

        from evealert.tools.combat_activity_store import backfill_from_zkillboard

        with respx.mock:
            respx.get("https://zkillboard.com/api/characterID/999/").mock(
                return_value=Response(500)
            )
            inserted = asyncio.run(backfill_from_zkillboard(999, "Bad Guy"))
        self.assertEqual(inserted, 0)

    def test_backfill_returns_zero_for_no_character_id(self):
        import asyncio

        from evealert.tools.combat_activity_store import backfill_from_zkillboard

        inserted = asyncio.run(backfill_from_zkillboard(0, "Bad Guy"))
        self.assertEqual(inserted, 0)

    def test_backfill_skips_malformed_killmail_without_raising(self):
        import asyncio

        from evealert.tools.combat_activity_store import backfill_from_zkillboard

        with respx.mock:
            respx.get("https://zkillboard.com/api/characterID/999/").mock(
                return_value=Response(200, json=[self._zkb_entry(1)])
            )
            respx.get("https://esi.evetech.net/latest/killmails/1/abc123/").mock(
                return_value=Response(200, json={"solar_system_id": None})  # malformed
            )
            inserted = asyncio.run(backfill_from_zkillboard(999, "Bad Guy"))
        self.assertEqual(inserted, 0)  # character not found on the malformed killmail

    def test_backfill_skips_entries_missing_hash(self):
        import asyncio

        from evealert.tools.combat_activity_store import backfill_from_zkillboard

        with respx.mock:
            respx.get("https://zkillboard.com/api/characterID/999/").mock(
                return_value=Response(200, json=[{"killmail_id": 1, "zkb": {}}])
            )
            inserted = asyncio.run(backfill_from_zkillboard(999, "Bad Guy"))
        self.assertEqual(inserted, 0)


if __name__ == "__main__":
    unittest.main()
