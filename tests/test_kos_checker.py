"""Tests for KOS checker cache keying + singleton (issues #101, #106)."""

import asyncio
import unittest
from unittest import mock

from evealert.tools import kos_checker
from evealert.tools.kos_checker import KosChecker, KosResult, get_kos_checker


class CacheKeyTests(unittest.TestCase):
    def test_cache_key_includes_corp_and_alliance(self):
        checker = KosChecker(cva_enabled=False)
        calls = []

        async def fake_do_check(pilot, corp, alliance):
            calls.append((pilot, corp, alliance))
            return None

        checker._do_check = fake_do_check
        # Same pilot, different corp — must NOT reuse the first cache entry.
        asyncio.run(checker.check("Bob", "CorpA", ""))
        asyncio.run(checker.check("Bob", "CorpB", ""))
        self.assertEqual(len(calls), 2)

    def test_same_key_is_cached(self):
        checker = KosChecker(cva_enabled=False)
        calls = []

        async def fake_do_check(pilot, corp, alliance):
            calls.append(1)
            return None

        checker._do_check = fake_do_check
        asyncio.run(checker.check("Bob", "CorpA", "AllyA"))
        asyncio.run(checker.check("Bob", "CorpA", "AllyA"))
        self.assertEqual(len(calls), 1)  # second hit served from cache

    def test_local_list_matches_corp(self):
        checker = KosChecker(cva_enabled=False)
        checker.update_local_list({"evil corp": "red"})
        result = asyncio.run(checker.check("Innocent Pilot", "Evil Corp", ""))
        self.assertIsInstance(result, KosResult)
        self.assertTrue(result.is_kos)


class SingletonTests(unittest.TestCase):
    def setUp(self):
        kos_checker._checker = None

    def tearDown(self):
        kos_checker._checker = None

    def test_singleton_not_rebuilt_on_kwargs(self):
        first = get_kos_checker(cva_enabled=True)
        first._cache[("x", "", "")] = (9e18, None)  # sentinel cache entry
        second = get_kos_checker(cva_enabled=False)  # reconfigure, keep cache
        self.assertIs(second, first)
        self.assertIn(("x", "", ""), second._cache)  # cache preserved
        self.assertFalse(second._cva_enabled)  # but reconfigured


class DeadSourceTests(unittest.TestCase):
    """CVA KOS domain is offline — connection errors must disable the source (#135)."""

    def setUp(self):
        import asyncio
        import httpx
        import respx
        from httpx import Response
        from evealert.tools.kos_checker import KosChecker

        self.asyncio = asyncio
        self.httpx = httpx
        self.respx = respx
        self.Response = Response
        self.KosChecker = KosChecker

    def test_connect_error_disables_cva_and_returns_none(self):
        import asyncio, respx, httpx
        from evealert.tools.kos_checker import KosChecker, _CVA_KOS_URL

        checker = KosChecker(cva_enabled=True)
        with respx.mock:
            respx.get(_CVA_KOS_URL).mock(side_effect=httpx.ConnectError("no route"))
            result = asyncio.run(checker.check("Bad Guy", "Bad Corp", "Bad Alliance"))

        self.assertIsNone(result)
        self.assertIn(_CVA_KOS_URL, checker._dead_sources)

    def test_dead_source_not_retried(self):
        import asyncio, respx, httpx
        from evealert.tools.kos_checker import KosChecker, _CVA_KOS_URL

        checker = KosChecker(cva_enabled=True)
        checker._dead_sources.add(_CVA_KOS_URL)  # pre-mark as dead

        call_count = 0
        with respx.mock:
            route = respx.get(_CVA_KOS_URL).mock(side_effect=httpx.ConnectError("dead"))
            result = asyncio.run(checker.check("Bad Guy"))

        # Route must NOT have been called
        self.assertFalse(route.called)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
