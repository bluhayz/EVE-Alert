"""Tests for ESI standings + zKillboard profile parsing (#134)."""

import unittest

from evealert.tools.esi_standings import KillProfile


class KillProfileFieldTests(unittest.TestCase):
    """Verify field names are kills_total/losses_total (all-time, not 30d)."""

    def test_field_names(self):
        kp = KillProfile(kills_total=10, losses_total=2, top_ship="Vexor", danger_ratio=0.83)
        self.assertEqual(kp.kills_total, 10)
        self.assertEqual(kp.losses_total, 2)
        self.assertEqual(kp.top_ship, "Vexor")


class TopShipParsingTests(unittest.TestCase):
    def _make_data(self, top_lists, danger_ratio=0, ships_destroyed=10, ships_lost=2):
        return {
            "shipsDestroyed": ships_destroyed,
            "shipsLost": ships_lost,
            "dangerRatio": danger_ratio,
            "topLists": top_lists,
        }

    def setUp(self):
        from evealert.tools.esi_standings import EsiLookup
        self._lookup = EsiLookup()

    def _parse(self, data):
        """Exercise _fetch_zkb_profile logic without network by calling the pure parser."""
        import asyncio, respx
        from httpx import Response
        from evealert.tools.esi_standings import EsiLookup

        lookup = EsiLookup()
        with respx.mock:
            respx.get("https://zkillboard.com/api/stats/characterID/12345/").mock(
                return_value=Response(200, json=data)
            )
            return asyncio.run(lookup._fetch_zkb_profile(12345))

    def test_shiptype_resolves_top_ship(self):
        data = self._make_data([
            {"type": "shipType", "values": [{"shipName": "Vexor", "id": 1}]}
        ], danger_ratio=75)
        result = self._parse(data)
        self.assertIsNotNone(result)
        self.assertEqual(result.top_ship, "Vexor")
        self.assertEqual(result.kills_total, 10)

    def test_ship_type_key_ignored(self):
        """Old 'ship' key must no longer match — type must be 'shipType'."""
        data = self._make_data([
            {"type": "ship", "values": [{"shipName": "Rifter", "id": 1}]}
        ])
        result = self._parse(data)
        self.assertIsNotNone(result)
        self.assertIsNone(result.top_ship)

    def test_empty_values_list_gives_none_ship(self):
        data = self._make_data([
            {"type": "shipType", "values": []}
        ])
        result = self._parse(data)
        self.assertIsNotNone(result)
        self.assertIsNone(result.top_ship)

    def test_danger_ratio_from_zkb(self):
        data = self._make_data([], danger_ratio=81, ships_destroyed=2414, ships_lost=199)
        result = self._parse(data)
        self.assertAlmostEqual(result.danger_ratio, 0.81)


class NeverIndexedCharacterTests(unittest.TestCase):
    """#208: zKillboard returns HTTP 200 with {"error": "Invalid type or
    id"} for a character it has never seen in any killmail — NOT the same
    as a real character with zero kills/losses. Regression for a live 404:
    a 35-day-old pilot's zkillboard.com/character/<id>/ link 404'd because
    the app was linking to a page zkillboard has never created."""

    def _parse(self, data, status=200):
        import asyncio, respx
        from httpx import Response
        from evealert.tools.esi_standings import EsiLookup

        lookup = EsiLookup()
        with respx.mock:
            respx.get("https://zkillboard.com/api/stats/characterID/2124449072/").mock(
                return_value=Response(status, json=data)
            )
            return asyncio.run(lookup._fetch_zkb_profile(2124449072))

    def test_error_body_returns_none_not_zero_profile(self):
        result = self._parse({"error": "Invalid type or id"})
        self.assertIsNone(
            result,
            "An {'error': ...} response must be treated as 'no data', not "
            "parsed as a zero-kills/zero-losses KillProfile.",
        )

    def test_real_zero_stats_character_still_returns_a_profile(self):
        """A genuine character with a valid zkillboard page but literally
        zero recorded activity (no 'error' key) must still parse normally —
        this fix must not overreact and treat all-zero stats as an error."""
        data = {"shipsDestroyed": 0, "shipsLost": 0, "dangerRatio": 0, "topLists": []}
        result = self._parse(data)
        self.assertIsNotNone(result)
        self.assertEqual(result.kills_total, 0)
        self.assertEqual(result.losses_total, 0)


class PurgeExpiredTests(unittest.TestCase):
    """#229: EsiLookup._cache/_zkb_cache only skip a stale entry on read --
    purge_expired() must actually evict them, mirroring
    ZkillboardClient.purge_expired() (#177)."""

    def test_purge_removes_only_stale_entries(self):
        import time as time_mod

        from evealert.tools.esi_standings import EsiLookup, _CACHE_TTL

        lookup = EsiLookup()
        stale = time_mod.time() - _CACHE_TTL - 10
        fresh = time_mod.time()
        lookup._cache["stale pilot"] = (stale, None)
        lookup._cache["fresh pilot"] = (fresh, None)
        lookup._zkb_cache[111] = (stale, None)
        lookup._zkb_cache[222] = (fresh, None)

        removed = lookup.purge_expired()

        self.assertEqual(removed, 2)
        self.assertNotIn("stale pilot", lookup._cache)
        self.assertIn("fresh pilot", lookup._cache)
        self.assertNotIn(111, lookup._zkb_cache)
        self.assertIn(222, lookup._zkb_cache)

    def test_purge_on_empty_caches_returns_zero(self):
        from evealert.tools.esi_standings import EsiLookup

        self.assertEqual(EsiLookup().purge_expired(), 0)


if __name__ == "__main__":
    unittest.main()
