"""Tests for UniverseCache.suggest_safer_route() (#172) -- weighted-
Dijkstra route avoidance using live kill counts, gate camps, and
security-status penalties, with a synthetic jump graph so no real ESI/
zKB traffic is needed."""

import unittest
from unittest.mock import AsyncMock, patch

import respx
from httpx import Response

from evealert.tools.universe import UniverseCache

_ESI_SYSTEM_URL = "https://esi.evetech.net/v4/universe/systems/30000142/"
_ZKB_KILLS_URL = (
    "https://zkillboard.com/api/kills/solarSystemID/30000142/pastSeconds/3600/"
)


class SecurityStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_and_caches_security_status(self):
        cache = UniverseCache()
        with respx.mock:
            route = respx.get(_ESI_SYSTEM_URL).mock(
                return_value=Response(200, json={"security_status": -0.42})
            )
            first = await cache.get_security_status(30000142)
            second = await cache.get_security_status(30000142)  # cached, no 2nd call

        self.assertAlmostEqual(first, -0.42)
        self.assertAlmostEqual(second, -0.42)
        self.assertEqual(route.call_count, 1)

    async def test_http_failure_returns_none(self):
        cache = UniverseCache()
        with respx.mock:
            respx.get(_ESI_SYSTEM_URL).mock(return_value=Response(500))
            result = await cache.get_security_status(30000142)
        self.assertIsNone(result)


class KillCountCachingTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_cached_kill_count_within_ttl(self):
        cache = UniverseCache()
        with respx.mock:
            route = respx.get(_ZKB_KILLS_URL).mock(
                return_value=Response(200, json=[{"killmail_id": 1}])
            )
            first = await cache._zkb_kills_last_hour(30000142)
            second = await cache._zkb_kills_last_hour(30000142)

        self.assertEqual(first, 1)
        self.assertEqual(second, 1)
        self.assertEqual(route.call_count, 1)

    async def test_refetches_after_ttl_expires(self):
        import time as time_module

        from evealert.tools import universe as universe_module

        cache = UniverseCache()
        # Seed a stale cache entry directly rather than mocking time.time()
        # across a real httpx/respx round trip -- httpx's own internals
        # also read the wall clock, so a globally-patched time.time() with
        # a short canned sequence is fragile here.
        cache._kill_count_cache[30000142] = (
            time_module.time() - universe_module._KILL_COUNT_CACHE_TTL - 1, 99
        )
        with respx.mock:
            route = respx.get(_ZKB_KILLS_URL).mock(
                return_value=Response(200, json=[{"killmail_id": 1}])
            )
            result = await cache._zkb_kills_last_hour(30000142)

        self.assertEqual(result, 1)  # fresh fetch, not the stale seeded 99
        self.assertEqual(route.call_count, 1)


def _graph_cache(neighbors: dict, kills: dict, sec: dict, camped=None):
    """Build a UniverseCache whose graph/kill/sec lookups are all
    synthetic, so the Dijkstra search runs over injected data only."""
    cache = UniverseCache()
    cache.get_neighbors = AsyncMock(side_effect=lambda sid: neighbors.get(sid, []))
    cache.get_system_name = AsyncMock(side_effect=lambda sid: f"Sys{sid}")
    cache._zkb_kills_last_hour = AsyncMock(side_effect=lambda sid: kills.get(sid, 0))
    cache.get_security_status = AsyncMock(side_effect=lambda sid: sec.get(sid, 0.9))
    return cache


class SuggestSaferRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_detours_around_a_hot_system_within_a_short_alternative(self):
        # 1 -> 2 -> 3 is the 2-jump shortest path, but system 2 is very hot.
        # 1 -> 4 -> 5 -> 3 is a 3-jump (+1) detour through quiet systems.
        neighbors = {
            1: [2, 4],
            2: [1, 3],
            3: [2, 5],
            4: [1, 5],
            5: [4, 3],
        }
        kills = {2: 20}  # everything else defaults to 0
        sec = {}  # everything defaults to high-sec (0.9) -- isolate the kill signal
        cache = _graph_cache(neighbors, kills, sec)

        result = await cache.suggest_safer_route(1, 3, max_hops=10)

        self.assertIsNotNone(result)
        self.assertTrue(result.detoured)
        shortest_ids = [leg.system_id for leg in result.shortest]
        suggested_ids = [leg.system_id for leg in result.suggested]
        self.assertEqual(shortest_ids, [2, 3])
        self.assertEqual(suggested_ids, [4, 5, 3])
        self.assertTrue(all(leg.threat_level != "danger" for leg in result.suggested))

    async def test_no_detour_offered_when_hot_system_is_unavoidable(self):
        # Only path from 1 to 3 goes through 2 -- no alternative exists.
        neighbors = {1: [2], 2: [1, 3], 3: [2]}
        kills = {2: 20}
        cache = _graph_cache(neighbors, kills, {})

        result = await cache.suggest_safer_route(1, 3, max_hops=10)

        self.assertIsNotNone(result)
        self.assertFalse(result.detoured)
        self.assertEqual(
            [leg.system_id for leg in result.shortest],
            [leg.system_id for leg in result.suggested],
        )

    async def test_quiet_shortest_route_is_returned_undetoured(self):
        neighbors = {1: [2], 2: [1, 3], 3: [2]}
        cache = _graph_cache(neighbors, kills={}, sec={})

        result = await cache.suggest_safer_route(1, 3, max_hops=10)

        self.assertFalse(result.detoured)
        self.assertEqual([leg.system_id for leg in result.suggested], [2, 3])

    async def test_returns_none_when_no_path_exists(self):
        neighbors = {1: [2], 2: [1]}  # 3 is unreachable
        cache = _graph_cache(neighbors, kills={}, sec={})

        result = await cache.suggest_safer_route(1, 3, max_hops=10)

        self.assertIsNone(result)

    async def test_low_and_null_sec_systems_are_penalized(self):
        # 1 -> 2 -> 3 is high-sec throughout; 1 -> 4 -> 3 is shorter (2j vs 2j
        # tie) but 4 is null-sec -- the search should prefer the high-sec leg
        # when both routes are otherwise equal length.
        neighbors = {1: [2, 4], 2: [1, 3], 3: [2, 4], 4: [1, 3]}
        sec = {2: 0.9, 4: -0.5}
        cache = _graph_cache(neighbors, kills={}, sec=sec)

        result = await cache.suggest_safer_route(1, 3, max_hops=10)

        suggested_ids = [leg.system_id for leg in result.suggested]
        self.assertEqual(suggested_ids, [2, 3])

    async def test_active_camp_is_heavily_penalized(self):
        neighbors = {1: [2, 4], 2: [1, 3], 3: [2, 5], 4: [1, 5], 5: [4, 3]}
        cache = _graph_cache(neighbors, kills={}, sec={})

        result = await cache.suggest_safer_route(
            1, 3, max_hops=10, camped_system_ids={2}
        )

        self.assertTrue(result.detoured)
        suggested_ids = [leg.system_id for leg in result.suggested]
        self.assertNotIn(2, suggested_ids)
        # Camped leg is still shown as dangerous on the shortest route.
        shortest_leg_2 = next(leg for leg in result.shortest if leg.system_id == 2)
        self.assertTrue(shortest_leg_2.has_camp)
        self.assertEqual(shortest_leg_2.threat_level, "danger")

    async def test_same_origin_and_destination_returns_trivial_route(self):
        cache = _graph_cache({}, kills={}, sec={})

        result = await cache.suggest_safer_route(1, 1, max_hops=10)

        self.assertIsNotNone(result)
        self.assertEqual(result.shortest, [])
        self.assertEqual(result.suggested, [])
        self.assertFalse(result.detoured)

    async def test_zkb_call_budget_is_capped_during_search(self):
        """#172 etiquette requirement: suggest_safer_route() must not probe
        more than _ROUTE_SEARCH_MAX_ZKB_CALLS systems' kill counts even
        when the graph has far more candidate systems than that."""
        from evealert.tools import universe as universe_module

        # A long chain graph with many more systems than the zKB call cap.
        n = universe_module._ROUTE_SEARCH_MAX_ZKB_CALLS + 20
        neighbors = {i: [i - 1, i + 1] for i in range(1, n)}
        neighbors[0] = [1]
        neighbors[n] = [n - 1]
        cache = _graph_cache(neighbors, kills={}, sec={})

        await cache.suggest_safer_route(0, n, max_hops=n + 5)

        self.assertLessEqual(
            cache._zkb_kills_last_hour.await_count,
            universe_module._ROUTE_SEARCH_MAX_ZKB_CALLS,
        )


if __name__ == "__main__":
    unittest.main()
