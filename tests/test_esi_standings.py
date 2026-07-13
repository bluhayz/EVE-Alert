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


if __name__ == "__main__":
    unittest.main()
