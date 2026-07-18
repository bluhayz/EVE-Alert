"""Tests for evealert.tools.gatecamp (#170, v7.1) -- gate-camp detection
from R2Z2 live-kill clustering."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import Request, Response

from evealert.tools import gatecamp
from evealert.tools.gatecamp import CampInfo, detect_camps, get_active_camps
from evealert.tools.r2z2 import LiveKillmail


def _km(killmail_id, system_id=30000142, location_id=50000342,
        victim_corp=None, attacker_char_ids=None):
    return LiveKillmail(
        killmail_id=killmail_id,
        solar_system_id=system_id,
        victim_ship_type_id=587,
        attacker_count=len(attacker_char_ids or []),
        location_id=location_id,
        victim_corporation_id=victim_corp,
        attacker_character_ids=set(attacker_char_ids or []),
    )


class DetectCampsTests(unittest.TestCase):
    """Synthetic kill sequences: camp / not-camp / possible-camp / decayed."""

    def test_three_kills_two_corps_full_overlap_is_a_camp(self):
        now = 1_000_000.0
        kills = [
            (now - 100, _km(1, victim_corp=100, attacker_char_ids=[1, 2, 3])),
            (now - 200, _km(2, victim_corp=200, attacker_char_ids=[1, 2, 4])),
            (now - 300, _km(3, victim_corp=200, attacker_char_ids=[1, 3, 5])),
        ]
        camps = detect_camps(kills, now=now)
        self.assertEqual(len(camps), 1)
        camp = camps[0]
        self.assertEqual(camp.confidence, "camp")
        self.assertEqual(camp.kill_count, 3)
        self.assertEqual(camp.system_id, 30000142)
        self.assertEqual(camp.location_id, 50000342)
        self.assertAlmostEqual(camp.last_kill_age_seconds, 100.0)

    def test_gank_squads_without_attacker_overlap_is_not_a_camp(self):
        """Three kills, three distinct attacker gangs -- no repeating
        characters means this is unrelated one-off ganks, not a camp."""
        now = 1_000_000.0
        kills = [
            (now - 100, _km(1, victim_corp=100, attacker_char_ids=[1, 2])),
            (now - 200, _km(2, victim_corp=200, attacker_char_ids=[3, 4])),
            (now - 300, _km(3, victim_corp=300, attacker_char_ids=[5, 6])),
        ]
        camps = detect_camps(kills, now=now)
        self.assertEqual(camps, [])

    def test_two_kills_with_overlap_is_a_possible_camp(self):
        now = 1_000_000.0
        kills = [
            (now - 60, _km(1, victim_corp=100, attacker_char_ids=[1, 2])),
            (now - 120, _km(2, victim_corp=100, attacker_char_ids=[1, 3])),
        ]
        camps = detect_camps(kills, now=now)
        self.assertEqual(len(camps), 1)
        self.assertEqual(camps[0].confidence, "possible_camp")

    def test_three_kills_single_victim_corp_is_only_possible_camp(self):
        """Kill count and overlap qualify for a full camp, but all victims
        are from the same corp -- one wing wipe, not necessarily a camp
        catching random traffic."""
        now = 1_000_000.0
        kills = [
            (now - 100, _km(1, victim_corp=100, attacker_char_ids=[1, 2])),
            (now - 200, _km(2, victim_corp=100, attacker_char_ids=[1, 3])),
            (now - 300, _km(3, victim_corp=100, attacker_char_ids=[1, 4])),
        ]
        camps = detect_camps(kills, now=now)
        self.assertEqual(len(camps), 1)
        self.assertEqual(camps[0].confidence, "possible_camp")

    def test_single_kill_is_never_a_camp(self):
        now = 1_000_000.0
        kills = [(now - 60, _km(1, victim_corp=100, attacker_char_ids=[1, 2]))]
        self.assertEqual(detect_camps(kills, now=now), [])

    def test_kills_with_no_location_id_are_excluded(self):
        now = 1_000_000.0
        kills = [
            (now - 60, _km(1, location_id=None, victim_corp=100, attacker_char_ids=[1, 2])),
            (now - 120, _km(2, location_id=None, victim_corp=200, attacker_char_ids=[1, 3])),
            (now - 180, _km(3, location_id=None, victim_corp=200, attacker_char_ids=[1, 4])),
        ]
        self.assertEqual(detect_camps(kills, now=now), [])

    def test_different_locations_do_not_cluster_together(self):
        now = 1_000_000.0
        kills = [
            (now - 60, _km(1, location_id=111, victim_corp=100, attacker_char_ids=[1, 2])),
            (now - 120, _km(2, location_id=222, victim_corp=200, attacker_char_ids=[1, 3])),
        ]
        # Same overlapping attacker (char 1) but split across two gates --
        # neither location alone has 2 kills.
        self.assertEqual(detect_camps(kills, now=now), [])

    def test_decayed_camp_kills_outside_the_window_are_not_passed_in(self):
        """detect_camps() is windowless by design -- callers (get_active_
        camps -> R2Z2Consumer.get_recent_kills_with_times) are responsible
        for excluding aged-out kills. Simulate that by only passing the
        still-fresh kill and confirm a lone fresh kill doesn't camp."""
        now = 1_000_000.0
        fresh_only = [(now - 60, _km(1, victim_corp=100, attacker_char_ids=[1, 2]))]
        self.assertEqual(detect_camps(fresh_only, now=now), [])

    def test_last_kill_age_never_negative(self):
        now = 1_000_000.0
        kills = [
            (now + 50, _km(1, victim_corp=100, attacker_char_ids=[1, 2])),  # clock skew
            (now - 10, _km(2, victim_corp=200, attacker_char_ids=[1, 3])),
        ]
        camps = detect_camps(kills, now=now)
        self.assertEqual(camps[0].last_kill_age_seconds, 0.0)


class GetActiveCampsTests(unittest.TestCase):
    def test_no_consumer_returns_empty_list(self):
        self.assertEqual(get_active_camps(None), [])

    def test_delegates_to_consumer_buffer_with_the_camp_window(self):
        consumer = MagicMock()
        consumer.get_recent_kills_with_times = MagicMock(return_value=[
            (1_000_000.0 - 60, _km(1, victim_corp=100, attacker_char_ids=[1, 2])),
            (1_000_000.0 - 120, _km(2, victim_corp=200, attacker_char_ids=[1, 3])),
            (1_000_000.0 - 180, _km(3, victim_corp=200, attacker_char_ids=[1, 4])),
        ])
        camps = get_active_camps(consumer, now=1_000_000.0)
        consumer.get_recent_kills_with_times.assert_called_once_with(
            gatecamp._CAMP_WINDOW_SECONDS
        )
        self.assertEqual(len(camps), 1)
        self.assertEqual(camps[0].confidence, "camp")


class ResolveCampNamesTests(unittest.IsolatedAsyncioTestCase):
    async def test_fills_in_system_and_gate_names(self):
        camp = CampInfo(
            system_id=30000142, location_id=50000342,
            kill_count=3, last_kill_age_seconds=10.0, confidence="camp",
        )
        mock_cache = MagicMock()
        mock_cache.get_system_name = AsyncMock(return_value="Jita")
        fake_request = Request(
            "GET", "https://esi.evetech.net/latest/universe/stargates/50000342/"
        )
        with patch(
            "evealert.tools.universe.get_universe_cache", return_value=mock_cache
        ), patch(
            "httpx.AsyncClient.get",
            new=AsyncMock(return_value=Response(
                200, json={"name": "Jita IV - Moon 4 - Stargate (Perimeter)"},
                request=fake_request,
            )),
        ):
            result = await gatecamp.resolve_camp_names([camp])

        self.assertEqual(result[0].system_name, "Jita")
        self.assertEqual(result[0].gate_name, "Jita IV - Moon 4 - Stargate (Perimeter)")

    async def test_gate_name_resolution_failure_leaves_it_none(self):
        camp = CampInfo(
            system_id=30000142, location_id=60003760,  # a citadel/structure ID
            kill_count=3, last_kill_age_seconds=10.0, confidence="camp",
        )
        mock_cache = MagicMock()
        mock_cache.get_system_name = AsyncMock(return_value="Jita")
        with patch(
            "evealert.tools.universe.get_universe_cache", return_value=mock_cache
        ), patch(
            "httpx.AsyncClient.get", new=AsyncMock(side_effect=Exception("404")),
        ):
            result = await gatecamp.resolve_camp_names([camp])

        self.assertEqual(result[0].system_name, "Jita")
        self.assertIsNone(result[0].gate_name)


if __name__ == "__main__":
    unittest.main()
