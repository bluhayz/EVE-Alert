"""Tests for evealert.tools.universe.UniverseCache -- #226/#227/#232.

#226: get_route() didn't exist on UniverseCache at all, even though two
production call sites (alertmanager._lookup_jump_distance,
pilot_history_analytics._is_plausible_transition) call it and silently
no-op/soft-fail via their surrounding except-blocks.

#227: _fetch_neighbors() truncated to the first 8 stargates (silently
dropping the rest for any system with more gates) and permanently cached
an empty neighbor list on a transient ESI failure, with no retry.

#232: a failed sov-map fetch was cached as a successful "no sov anywhere"
result for the full 5-minute TTL.
"""

import asyncio
import time
import unittest

import respx
from httpx import Response

from evealert.tools.universe import UniverseCache

_SYSTEMS_URL = "https://esi.evetech.net/v4/universe/systems"
_STARGATES_URL = "https://esi.evetech.net/v2/universe/stargates"
_SOV_URL = "https://esi.evetech.net/v1/sovereignty/map/"


def _system_response(system_id: int, name: str, stargates: list[int]) -> dict:
    return {"system_id": system_id, "name": name, "stargates": stargates}


class GetRouteTests(unittest.TestCase):
    """#226: get_route() must exist and return the shortest path."""

    def test_get_route_returns_shortest_path_inclusive(self):
        cache = UniverseCache()
        # Seed the neighbor graph directly -- pure BFS logic, no ESI needed.
        cache._neighbors = {
            1: [2, 3],
            2: [1, 4],
            3: [1, 4],
            4: [2, 3, 5],
            5: [4],
        }
        route = asyncio.run(cache.get_route(1, 5))
        self.assertIsNotNone(route)
        self.assertEqual(route[0], 1)
        self.assertEqual(route[-1], 5)
        self.assertEqual(len(route) - 1, 3)  # 1 -> 3/2 -> 4 -> 5

    def test_get_route_same_origin_and_destination(self):
        cache = UniverseCache()
        cache._neighbors = {1: []}
        route = asyncio.run(cache.get_route(1, 1))
        self.assertEqual(route, [1])

    def test_get_route_returns_none_when_unreachable(self):
        cache = UniverseCache()
        cache._neighbors = {1: [2], 2: [1], 99: []}
        route = asyncio.run(cache.get_route(1, 99))
        self.assertIsNone(route)

    def test_get_route_respects_max_hops(self):
        cache = UniverseCache()
        cache._neighbors = {1: [2], 2: [3], 3: [4], 4: [5], 5: []}
        route = asyncio.run(cache.get_route(1, 5, max_hops=2))
        self.assertIsNone(route)


class FetchNeighborsTruncationTests(unittest.TestCase):
    """#227: all stargates must resolve, not just the first 8."""

    def test_more_than_eight_stargates_all_resolve(self):
        gate_ids = list(range(100, 110))  # 10 gates
        cache = UniverseCache()
        with respx.mock:
            respx.get(f"{_SYSTEMS_URL}/1/").mock(
                return_value=Response(200, json=_system_response(1, "Origin", gate_ids))
            )
            for i, gid in enumerate(gate_ids):
                respx.get(f"{_STARGATES_URL}/{gid}/").mock(
                    return_value=Response(
                        200, json={"destination": {"system_id": 1000 + i}}
                    )
                )
            neighbors = asyncio.run(cache.get_neighbors(1))

        self.assertEqual(len(neighbors), 10)
        self.assertEqual(sorted(neighbors), [1000 + i for i in range(10)])


class FetchNeighborsFailureCachingTests(unittest.TestCase):
    """#227: a transient failure must not be cached forever -- the next
    call must retry rather than returning a permanently-empty list."""

    def test_system_fetch_failure_is_not_cached(self):
        cache = UniverseCache()
        with respx.mock:
            respx.get(f"{_SYSTEMS_URL}/1/").mock(return_value=Response(500))
            result = asyncio.run(cache.get_neighbors(1))
        self.assertEqual(result, [])
        self.assertNotIn(1, cache._neighbors)

    def test_failed_fetch_is_retried_and_succeeds_on_second_call(self):
        cache = UniverseCache()
        with respx.mock:
            respx.get(f"{_SYSTEMS_URL}/1/").mock(return_value=Response(500))
            first = asyncio.run(cache.get_neighbors(1))
        self.assertEqual(first, [])

        with respx.mock:
            respx.get(f"{_SYSTEMS_URL}/1/").mock(
                return_value=Response(200, json=_system_response(1, "Origin", [200]))
            )
            respx.get(f"{_STARGATES_URL}/200/").mock(
                return_value=Response(200, json={"destination": {"system_id": 2}})
            )
            second = asyncio.run(cache.get_neighbors(1))
        self.assertEqual(second, [2])
        self.assertEqual(cache._neighbors[1], [2])

    def test_all_stargate_fetches_failing_is_not_cached(self):
        cache = UniverseCache()
        with respx.mock:
            respx.get(f"{_SYSTEMS_URL}/1/").mock(
                return_value=Response(200, json=_system_response(1, "Origin", [200, 201]))
            )
            respx.get(f"{_STARGATES_URL}/200/").mock(return_value=Response(500))
            respx.get(f"{_STARGATES_URL}/201/").mock(return_value=Response(500))
            result = asyncio.run(cache.get_neighbors(1))
        self.assertEqual(result, [])
        self.assertNotIn(1, cache._neighbors)

    def test_genuinely_gateless_system_is_cached_as_empty(self):
        """A system with a real, successfully-fetched empty stargate list
        (not a failure) is a legitimate [] and SHOULD be cached."""
        cache = UniverseCache()
        with respx.mock:
            respx.get(f"{_SYSTEMS_URL}/1/").mock(
                return_value=Response(200, json=_system_response(1, "Origin", []))
            )
            result = asyncio.run(cache.get_neighbors(1))
        self.assertEqual(result, [])
        self.assertIn(1, cache._neighbors)
        self.assertEqual(cache._neighbors[1], [])


class GetSystemIdCasingTests(unittest.TestCase):
    """#227 (minor): get_system_id() must not seed _id_to_name with the
    caller's typed casing -- get_system_name() should resolve the
    canonical ESI name on first use instead."""

    def test_lowercase_query_does_not_poison_id_to_name_cache(self):
        cache = UniverseCache()
        with respx.mock:
            respx.post("https://esi.evetech.net/latest/universe/ids/").mock(
                return_value=Response(
                    200, json={"systems": [{"id": 30000142, "name": "Jita"}]}
                )
            )
            system_id = asyncio.run(cache.get_system_id("jita"))
        self.assertEqual(system_id, 30000142)
        # The lowercase query string must NOT have been cached as the name.
        self.assertNotIn(30000142, cache._id_to_name)


class GetSovereigntyFailureCachingTests(unittest.TestCase):
    """#232: a failed sov-map fetch must not be cached as a successful
    empty map for the full TTL."""

    def test_failed_fetch_does_not_poison_a_previously_successful_map(self):
        cache = UniverseCache()
        with respx.mock:
            respx.get(_SOV_URL).mock(
                return_value=Response(
                    200, json=[{"system_id": 1, "alliance_id": 99, "corporation_id": 88}]
                )
            )
            first = asyncio.run(cache.get_sovereignty(1))
        self.assertEqual(first.alliance_id, 99)

        # Force the TTL to have expired, then fail the next fetch.
        cache._sov_cache = (cache._sov_cache[0], 0.0)
        with respx.mock:
            respx.get(_SOV_URL).mock(return_value=Response(500))
            respx.get("https://esi.evetech.net/v4/alliances/99/").mock(
                return_value=Response(200, json={"name": "Fake Alliance"})
            )
            second = asyncio.run(cache.get_sovereignty(1))

        # The previous (still-valid) sov data must still be served, not
        # wiped to "no sov" because the refresh attempt failed.
        self.assertEqual(second.alliance_id, 99)

    def test_failed_fetch_retries_soon_not_after_the_full_ttl(self):
        cache = UniverseCache()
        with respx.mock:
            respx.get(_SOV_URL).mock(return_value=Response(500))
            asyncio.run(cache.get_sovereignty(1))

        _, fetched_at = cache._sov_cache
        # Must be scheduled to retry well before the full 5-minute TTL.
        from evealert.tools.universe import _SOV_CACHE_TTL, _SOV_FAILURE_RETRY_SECONDS

        age_until_next_retry = _SOV_CACHE_TTL - (time.time() - fetched_at)
        self.assertLessEqual(age_until_next_retry, _SOV_FAILURE_RETRY_SECONDS + 1)

    def test_first_ever_call_failing_returns_no_sov_not_an_exception(self):
        cache = UniverseCache()
        with respx.mock:
            respx.get(_SOV_URL).mock(return_value=Response(500))
            result = asyncio.run(cache.get_sovereignty(1))
        self.assertIsNotNone(result)
        self.assertIsNone(result.alliance_id)


if __name__ == "__main__":
    unittest.main()
