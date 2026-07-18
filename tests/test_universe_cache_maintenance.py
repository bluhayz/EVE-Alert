"""Tests for UniverseCache's #177 soak-reliability fixes: purging expired
kill-count cache entries, and a size guard on the (permanently-cached,
since system identity never changes) _neighbors dict."""

import time
import unittest
from unittest.mock import AsyncMock

from evealert.tools.universe import (
    UniverseCache,
    _KILL_COUNT_CACHE_TTL,
    _MAX_IDENTITY_CACHE_SIZE,
)


class PurgeExpiredKillCountsTests(unittest.TestCase):
    def test_purge_removes_expired_entries(self):
        cache = UniverseCache()
        cache._kill_count_cache[30000142] = (
            time.time() - _KILL_COUNT_CACHE_TTL - 1, 5
        )
        removed = cache.purge_expired_kill_counts()
        self.assertEqual(removed, 1)
        self.assertNotIn(30000142, cache._kill_count_cache)

    def test_purge_keeps_fresh_entries(self):
        cache = UniverseCache()
        cache._kill_count_cache[30000142] = (time.time(), 5)
        removed = cache.purge_expired_kill_counts()
        self.assertEqual(removed, 0)
        self.assertIn(30000142, cache._kill_count_cache)

    def test_purge_returns_zero_on_empty_cache(self):
        self.assertEqual(UniverseCache().purge_expired_kill_counts(), 0)

    def test_purge_only_removes_expired_leaves_fresh_mixed(self):
        cache = UniverseCache()
        cache._kill_count_cache[1] = (time.time() - _KILL_COUNT_CACHE_TTL - 1, 1)
        cache._kill_count_cache[2] = (time.time(), 2)
        removed = cache.purge_expired_kill_counts()
        self.assertEqual(removed, 1)
        self.assertNotIn(1, cache._kill_count_cache)
        self.assertIn(2, cache._kill_count_cache)


class NeighborsCacheSizeGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_grows_normally_under_the_cap(self):
        cache = UniverseCache()
        cache._fetch_neighbors = AsyncMock(return_value=[2, 3])
        await cache.get_neighbors(1)
        self.assertEqual(len(cache._neighbors), 1)
        self.assertIn(1, cache._neighbors)

    async def test_cache_clears_when_it_would_exceed_the_cap(self):
        cache = UniverseCache()
        cache._fetch_neighbors = AsyncMock(return_value=[999])
        # Pre-fill to exactly the cap -- the next insert must trigger a clear.
        for i in range(_MAX_IDENTITY_CACHE_SIZE):
            cache._neighbors[i] = []
        self.assertEqual(len(cache._neighbors), _MAX_IDENTITY_CACHE_SIZE)

        new_system_id = _MAX_IDENTITY_CACHE_SIZE + 1
        result = await cache.get_neighbors(new_system_id)

        self.assertEqual(result, [999])
        # Old entries are gone; only the newly-fetched one remains.
        self.assertEqual(len(cache._neighbors), 1)
        self.assertIn(new_system_id, cache._neighbors)

    async def test_cached_lookup_never_triggers_a_clear(self):
        """A cache HIT (system_id already known) must not even check the
        size guard -- only a genuine new fetch can trigger a clear."""
        cache = UniverseCache()
        cache._fetch_neighbors = AsyncMock(return_value=[2, 3])
        for i in range(_MAX_IDENTITY_CACHE_SIZE):
            cache._neighbors[i] = []
        cache._neighbors[0] = [42]  # a real, already-cached entry

        result = await cache.get_neighbors(0)

        self.assertEqual(result, [42])
        self.assertEqual(len(cache._neighbors), _MAX_IDENTITY_CACHE_SIZE)  # unchanged
        cache._fetch_neighbors.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
