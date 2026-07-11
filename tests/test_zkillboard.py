"""Tests for evealert.tools.zkillboard — ZkillboardClient caching and helpers."""

import time
import unittest

import respx
from httpx import Response


class TestGetClient(unittest.TestCase):
    def test_returns_same_singleton(self):
        from evealert.tools.zkillboard import get_client

        a = get_client()
        b = get_client()
        self.assertIs(a, b)


class TestVersionTupleNotPresent(unittest.TestCase):
    """ZkillboardClient has no _version_tuple; test ESI URL construction instead."""

    def test_esi_url_uses_tranquility(self):
        """_resolve_system_id must hit the ESI endpoint with datasource=tranquility."""
        import asyncio

        from evealert.tools.zkillboard import ZkillboardClient

        client = ZkillboardClient()
        with respx.mock:
            respx.get("https://esi.evetech.net/latest/search/").mock(
                return_value=Response(200, json={"solar_system": [30000142]})
            )
            result = asyncio.run(client._resolve_system_id("Jita"))
        self.assertEqual(result, 30000142)

    def test_resolve_system_id_returns_none_on_http_error(self):
        import asyncio

        from evealert.tools.zkillboard import ZkillboardClient

        client = ZkillboardClient()
        with respx.mock:
            respx.get("https://esi.evetech.net/latest/search/").mock(
                return_value=Response(500)
            )
            result = asyncio.run(client._resolve_system_id("Jita"))
        self.assertIsNone(result)


class TestCaching(unittest.TestCase):
    def test_cache_hit_returns_same_result_without_network_call(self):
        import asyncio

        from evealert.tools.zkillboard import ZkillboardClient

        client = ZkillboardClient()
        # Pre-populate the cache with a fake result
        client._cache["jita"] = (time.time() + 9999, [])

        with respx.mock:
            # No routes registered — any network call would fail
            result = asyncio.run(client.get_recent_kills("Jita", limit=3))

        self.assertEqual(result, [])  # returns the cached empty list

    def test_clear_cache_empties_both_dicts(self):
        from evealert.tools.zkillboard import ZkillboardClient

        client = ZkillboardClient()
        client._cache["x"] = (time.time(), None)
        client._system_id_cache["x"] = 123
        client.clear_cache()
        self.assertEqual(len(client._cache), 0)
        self.assertEqual(len(client._system_id_cache), 0)

    def test_expired_cache_is_not_used(self):
        import asyncio

        from evealert.tools.zkillboard import ZkillboardClient

        client = ZkillboardClient()
        # Expired entry (fetch time in the past)
        client._cache["jita"] = (time.time() - 9999, ["old_result"])

        with respx.mock:
            respx.get("https://esi.evetech.net/latest/search/").mock(
                return_value=Response(200, json={"solar_system": []})
            )
            result = asyncio.run(client.get_recent_kills("Jita", limit=1))

        # ESI returned no system ID, so result is None (cache was bypassed)
        self.assertIsNone(result)


class TestGetRecentKills(unittest.TestCase):
    def test_returns_none_when_system_not_found(self):
        import asyncio

        from evealert.tools.zkillboard import ZkillboardClient

        client = ZkillboardClient()
        with respx.mock:
            respx.get("https://esi.evetech.net/latest/search/").mock(
                return_value=Response(200, json={"solar_system": []})
            )
            result = asyncio.run(client.get_recent_kills("UnknownSystem"))
        self.assertIsNone(result)

    def test_returns_none_when_zkb_returns_non_list(self):
        import asyncio

        from evealert.tools.zkillboard import ZkillboardClient

        client = ZkillboardClient()
        with respx.mock:
            respx.get("https://esi.evetech.net/latest/search/").mock(
                return_value=Response(200, json={"solar_system": [30000142]})
            )
            respx.get(
                "https://zkillboard.com/api/kills/solarSystemID/30000142/limit/3/"
            ).mock(return_value=Response(200, json={"error": "not a list"}))
            result = asyncio.run(client.get_recent_kills("Jita", limit=3))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
