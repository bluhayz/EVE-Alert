"""Tests for UniverseCache.route_threat()'s camped_system_ids integration
(#170) -- active gate camps must force a leg to "danger" regardless of
the raw zKB kill count, and mark has_camp so the UI can render a
distinct CAMP marker."""

import unittest
from unittest.mock import AsyncMock, patch

from evealert.tools.universe import UniverseCache


class RouteThreatGateCampTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cache = UniverseCache()

    async def _run_route_threat(self, camped_system_ids=None):
        with patch.object(
            self.cache, "_bfs_path", new=AsyncMock(return_value=[1, 2, 3])
        ), patch.object(
            self.cache, "get_system_name",
            new=AsyncMock(side_effect=lambda sid: f"System{sid}"),
        ), patch.object(
            self.cache, "_zkb_kills_last_hour",
            new=AsyncMock(return_value=0),  # no organic kill activity anywhere
        ):
            return await self.cache.route_threat(1, 3, camped_system_ids=camped_system_ids)

    async def test_no_camps_leaves_zero_kill_legs_safe(self):
        legs = await self._run_route_threat(camped_system_ids=None)
        self.assertTrue(all(leg.threat_level == "safe" for leg in legs))
        self.assertTrue(all(not leg.has_camp for leg in legs))

    async def test_camped_system_forced_to_danger_despite_zero_kills(self):
        legs = await self._run_route_threat(camped_system_ids={2})
        by_id = {leg.system_id: leg for leg in legs}
        self.assertEqual(by_id[2].threat_level, "danger")
        self.assertTrue(by_id[2].has_camp)
        # Uncamped legs on the same route are unaffected.
        self.assertEqual(by_id[3].threat_level, "safe")
        self.assertFalse(by_id[3].has_camp)

    async def test_camp_flag_does_not_leak_into_kills_last_hour(self):
        """has_camp changes threat_level, not the underlying kill count --
        the UI needs both pieces of information separately."""
        legs = await self._run_route_threat(camped_system_ids={2})
        by_id = {leg.system_id: leg for leg in legs}
        self.assertEqual(by_id[2].kills_last_hour, 0)


if __name__ == "__main__":
    unittest.main()
