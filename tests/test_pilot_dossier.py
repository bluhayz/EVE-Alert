"""Tests for evealert.tools.pilot_dossier (#241, v7.4) -- the pilot
combat dossier engine built on the v7.3 data foundation."""

import os
import shutil
import tempfile
import time
import unittest


class PilotDossierTestCase(unittest.IsolatedAsyncioTestCase):
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


def _seed_activity(pilot_name, n, *, start_kmid=1, hour_utc=19, ships=None, systems=None):
    from evealert.tools.combat_activity_store import record_activity

    ships = ships or ["Sabre", "Sabre", "Sabre", "Loki", "Rifter"]
    systems = systems or ["Jita", "Jita", "Jita", "Amarr", "Dodixie"]
    # Anchor "now" at a fixed UTC hour so the prime-window assertion is stable.
    now = time.time()
    base = now - (time.gmtime(now).tm_hour - hour_utc) * 3600 - time.gmtime(now).tm_min * 60
    for i in range(n):
        record_activity(
            start_kmid + i,
            pilot_name,
            role="attacker" if i % 4 != 0 else "victim",
            character_id=999,
            ship_type_id=100,
            ship_name=ships[i % len(ships)],
            solar_system_id=30000142,
            system_name=systems[i % len(systems)],
            gang_size=3,
            victim_ship_name="Venture",
            occurred_at=base - i,
        )


class BuildDossierTests(PilotDossierTestCase):
    async def test_unknown_pilot_returns_none(self):
        from evealert.tools.pilot_dossier import build_dossier

        result = await build_dossier("Nobody Special")
        self.assertIsNone(result)

    async def test_fallback_path_computes_ships_systems_gang(self):
        from evealert.tools.pilot_dossier import build_dossier

        _seed_activity("Bad Guy", 10)
        dossier = await build_dossier("Bad Guy")
        self.assertIsNotNone(dossier)
        self.assertEqual(dossier.top_ships[0][0], "Sabre")
        self.assertEqual(dossier.top_hunt_systems[0][0], "Jita")
        self.assertEqual(dossier.avg_gang_size, 3.0)
        self.assertEqual(dossier.solo_pct, 0.0)
        self.assertIsNotNone(dossier.prime_window)

    async def test_ship_percentages_sum_correctly(self):
        from evealert.tools.pilot_dossier import build_dossier

        # 5 attacker + 5 victim rows, all "Sabre" -- 100% Sabre.
        _seed_activity("Bad Guy", 10, ships=["Sabre"] * 5, systems=["Jita"] * 5)
        dossier = await build_dossier("Bad Guy")
        self.assertEqual(dossier.top_ships[0], ("Sabre", 100.0))

    async def test_kill_loss_ratio(self):
        from evealert.tools.pilot_dossier import build_dossier

        # i % 4 != 0 -> attacker; over 10 rows (i=0..9), victims at i=0,4,8 (3),
        # attackers the remaining 7.
        _seed_activity("Bad Guy", 10)
        dossier = await build_dossier("Bad Guy")
        self.assertAlmostEqual(dossier.kill_loss_ratio, 7 / 3)

    async def test_no_losses_ratio_is_none(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.pilot_dossier import build_dossier

        for i in range(3):
            record_activity(
                100 + i, "Bad Guy", role="attacker", ship_name="Sabre",
                system_name="Jita", gang_size=1,
            )
        dossier = await build_dossier("Bad Guy")
        self.assertIsNone(dossier.kill_loss_ratio)

    async def test_sighting_only_pilot_still_builds_dossier(self):
        """A pilot seen in Local but never in a killmail (#215 sightings
        with zero combat_activity) still gets a dossier, built around
        sighting_summary rather than returning None."""
        from evealert.tools.pilot_history_store import record_sighting
        from evealert.tools.pilot_dossier import build_dossier

        for i in range(5):
            record_sighting(
                "Quiet Guy", source="local", system="Jita", ship="Rifter",
                seen_at=time.time() - i * 3600,
            )
        dossier = await build_dossier("Quiet Guy")
        self.assertIsNotNone(dossier)
        self.assertEqual(dossier.top_ships, [])
        self.assertIsNotNone(dossier.sighting_summary)

    async def test_unresolved_ship_names_excluded_from_percentage_denominator(self):
        """#254: rows whose ship type failed ESI resolution (ship_name is
        None) must not count against the percentage denominator -- 6 of
        10 rows are Sabre, the other 4 have no resolved ship name, so
        Sabre should show 100% (share of *identified* ships), not 60%."""
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.pilot_dossier import build_dossier

        for i in range(6):
            record_activity(
                200 + i, "Bad Guy", role="attacker", ship_name="Sabre",
                system_name="Jita", gang_size=1,
            )
        for i in range(4):
            record_activity(
                300 + i, "Bad Guy", role="attacker", ship_name=None,
                system_name="Jita", gang_size=1,
            )
        dossier = await build_dossier("Bad Guy")
        self.assertEqual(dossier.top_ships, [("Sabre", 100.0)])

    async def test_rollup_path_unresolved_ships_excluded_from_denominator(self):
        """Same #254 fix, rollup-path variant: PilotRollup.kill_count +
        loss_count can exceed the sum of top_ships counts when some
        killmail rows never got a resolved ship name."""
        from evealert.tools import intel_rollups
        from evealert.tools.pilot_dossier import build_dossier

        rollup = intel_rollups.PilotRollup(
            pilot_name="Bad Guy", sighting_count=0, kill_count=8, loss_count=2,
            top_ships=[("Sabre", 6)], top_systems=[("Jita", 6)],
            hour_histogram=[0] * 24, avg_gang_size=1.0,
            last_active_at=time.time(), updated_at=time.time(),
        )
        intel_rollups._store_pilot_rollup(rollup)  # noqa: SLF001
        dossier = await build_dossier("Bad Guy")
        self.assertEqual(dossier.top_ships, [("Sabre", 100.0)])

    async def test_uses_stored_rollup_when_present(self):
        """When a PilotRollup is already cached, build_dossier uses it
        instead of recomputing from raw activity -- verified by seeding a
        rollup with a deliberately different top ship than the raw
        activity would produce, and confirming the rollup's value wins."""
        from evealert.tools import intel_rollups
        from evealert.tools.pilot_dossier import build_dossier

        _seed_activity("Bad Guy", 10)  # raw data says "Sabre" is top
        rollup = intel_rollups.PilotRollup(
            pilot_name="Bad Guy", sighting_count=0, kill_count=8, loss_count=2,
            top_ships=[("Loki", 8)], top_systems=[("Amarr", 8)],
            hour_histogram=[0] * 19 + [8] + [0] * 4, avg_gang_size=5.0,
            last_active_at=time.time(), updated_at=time.time(),
        )
        intel_rollups._store_pilot_rollup(rollup)  # noqa: SLF001
        dossier = await build_dossier("Bad Guy")
        self.assertEqual(dossier.top_ships[0][0], "Loki")
        self.assertEqual(dossier.top_hunt_systems[0][0], "Amarr")


class FleetmateInferenceTests(PilotDossierTestCase):
    async def test_shared_killmails_reported_as_fleetmates(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.pilot_dossier import build_dossier

        # "Bad Guy" and "Wingman" both attack the same 3 killmails.
        for kill_id in (1, 2, 3):
            record_activity(kill_id, "Bad Guy", role="attacker", ship_name="Sabre", system_name="Jita")
            record_activity(kill_id, "Wingman", role="attacker", ship_name="Loki", system_name="Jita")
        dossier = await build_dossier("Bad Guy")
        self.assertEqual(dossier.frequent_fleetmates, [("Wingman", 3)])

    async def test_below_threshold_not_reported(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.pilot_dossier import build_dossier

        for kill_id in (1, 2):  # only 2 shared kills -- below the 3-kill floor
            record_activity(kill_id, "Bad Guy", role="attacker", ship_name="Sabre", system_name="Jita")
            record_activity(kill_id, "Casual Ally", role="attacker", ship_name="Loki", system_name="Jita")
        dossier = await build_dossier("Bad Guy")
        self.assertEqual(dossier.frequent_fleetmates, [])

    async def test_own_character_excluded(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.pilot_dossier import build_dossier

        for kill_id in (1, 2, 3):
            record_activity(kill_id, "Bad Guy", role="attacker", ship_name="Sabre", system_name="Jita")
            record_activity(kill_id, "My Main", role="attacker", ship_name="Loki", system_name="Jita")
        dossier = await build_dossier("Bad Guy", own_character_name="My Main")
        self.assertEqual(dossier.frequent_fleetmates, [])

    async def test_victim_role_kills_not_counted_as_shared(self):
        from evealert.tools.combat_activity_store import record_activity
        from evealert.tools.pilot_dossier import build_dossier

        for kill_id in (1, 2, 3):
            record_activity(kill_id, "Bad Guy", role="attacker", ship_name="Sabre", system_name="Jita")
            record_activity(kill_id, "Some Victim", role="victim", ship_name="Venture", system_name="Jita")
        dossier = await build_dossier("Bad Guy")
        self.assertEqual(dossier.frequent_fleetmates, [])


class FormatDossierLineTests(unittest.TestCase):
    def test_full_line_shape(self):
        from evealert.tools.pilot_dossier import PilotDossier, format_dossier_line

        dossier = PilotDossier(
            pilot_name="Bad Guy",
            top_ships=[("Sabre", 58.0), ("Loki", 21.0)],
            top_hunt_systems=[("D7-ZAC", 14)],
            active_hours=[0] * 24,
            prime_window="19:00-22:00",
            avg_gang_size=4.0,
            solo_pct=12.0,
            frequent_fleetmates=[],
            sighting_summary=None,
            pathing=None,
            kill_loss_ratio=None,
            last_active=None,
        )
        line = format_dossier_line(dossier)
        self.assertIn("Sabre 58%/Loki 21%", line)
        self.assertIn("hunts D7-ZAC (14 kills)", line)
        self.assertIn("prime 19:00-22:00 EVE", line)
        self.assertIn("gang ~4 (solo 12%)", line)
        self.assertLessEqual(len(line), 140)

    def test_empty_segments_omitted(self):
        from evealert.tools.pilot_dossier import PilotDossier, format_dossier_line

        dossier = PilotDossier(
            pilot_name="Bad Guy", top_ships=[], top_hunt_systems=[],
            active_hours=[0] * 24, prime_window=None, avg_gang_size=None,
            solo_pct=None, frequent_fleetmates=[], sighting_summary=None,
            pathing=None, kill_loss_ratio=None, last_active=None,
        )
        self.assertEqual(format_dossier_line(dossier), "")

    def test_capped_at_max_chars(self):
        from evealert.tools.pilot_dossier import PilotDossier, format_dossier_line

        dossier = PilotDossier(
            pilot_name="Bad Guy",
            top_ships=[("A Very Long Cruiser Class Ship Name Indeed", 58.0), ("Another Long One", 21.0)],
            top_hunt_systems=[("A-Very-Long-Wormhole-System-Designation-Right-Here", 14)],
            active_hours=[0] * 24,
            prime_window="19:00-22:00",
            avg_gang_size=4.0,
            solo_pct=12.0,
            frequent_fleetmates=[],
            sighting_summary=None,
            pathing=None,
            kill_loss_ratio=None,
            last_active=None,
        )
        line = format_dossier_line(dossier)
        self.assertLessEqual(len(line), 140)


if __name__ == "__main__":
    unittest.main()
