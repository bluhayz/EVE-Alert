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
            respx.post("https://esi.evetech.net/latest/universe/ids/").mock(
                return_value=Response(
                    200, json={"systems": [{"id": 30000142, "name": "Jita"}]}
                )
            )
            result = asyncio.run(client._resolve_system_id("Jita"))
        self.assertEqual(result, 30000142)

    def test_resolve_system_id_returns_none_on_http_error(self):
        import asyncio

        from evealert.tools.zkillboard import ZkillboardClient

        client = ZkillboardClient()
        with respx.mock:
            respx.post("https://esi.evetech.net/latest/universe/ids/").mock(
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
            respx.post("https://esi.evetech.net/latest/universe/ids/").mock(
                return_value=Response(200, json={"systems": []})
            )
            result = asyncio.run(client.get_recent_kills("Jita", limit=1))

        # ESI returned no system ID, so result is None (cache was bypassed)
        self.assertIsNone(result)


class TestPurgeExpired(unittest.TestCase):
    """#177: purge_expired() must evict entries the TTL check would
    already treat as stale on read, not just skip them -- otherwise a
    system looked up once and never revisited sits in memory forever."""

    def test_purge_removes_expired_entries(self):
        from evealert.tools.zkillboard import ZkillboardClient, _CACHE_TTL

        client = ZkillboardClient()
        client._cache["stale"] = (time.time() - _CACHE_TTL - 1, [])

        removed = client.purge_expired()

        self.assertEqual(removed, 1)
        self.assertNotIn("stale", client._cache)

    def test_purge_keeps_fresh_entries(self):
        from evealert.tools.zkillboard import ZkillboardClient

        client = ZkillboardClient()
        client._cache["fresh"] = (time.time(), [])

        removed = client.purge_expired()

        self.assertEqual(removed, 0)
        self.assertIn("fresh", client._cache)

    def test_purge_leaves_system_id_cache_untouched(self):
        """System name->ID mappings never expire (names don't change) --
        purge_expired() must only ever touch the TTL'd kills cache."""
        from evealert.tools.zkillboard import ZkillboardClient, _CACHE_TTL

        client = ZkillboardClient()
        client._cache["stale"] = (time.time() - _CACHE_TTL - 1, [])
        client._system_id_cache["jita"] = 30000142

        client.purge_expired()

        self.assertEqual(client._system_id_cache["jita"], 30000142)

    def test_purge_returns_zero_on_empty_cache(self):
        from evealert.tools.zkillboard import ZkillboardClient

        self.assertEqual(ZkillboardClient().purge_expired(), 0)


class TestGetRecentKills(unittest.TestCase):
    def test_returns_none_when_system_not_found(self):
        import asyncio

        from evealert.tools.zkillboard import ZkillboardClient

        client = ZkillboardClient()
        with respx.mock:
            respx.post("https://esi.evetech.net/latest/universe/ids/").mock(
                return_value=Response(200, json={"systems": []})
            )
            result = asyncio.run(client.get_recent_kills("UnknownSystem"))
        self.assertIsNone(result)

    def test_returns_none_when_zkb_returns_non_list(self):
        import asyncio

        from evealert.tools.zkillboard import ZkillboardClient

        client = ZkillboardClient()
        with respx.mock:
            respx.post("https://esi.evetech.net/latest/universe/ids/").mock(
                return_value=Response(
                    200, json={"systems": [{"id": 30000142, "name": "Jita"}]}
                )
            )
            respx.get(
                "https://zkillboard.com/api/kills/solarSystemID/30000142/"
            ).mock(return_value=Response(200, json={"error": "not a list"}))
            result = asyncio.run(client.get_recent_kills("Jita", limit=3))
        self.assertIsNone(result)

    def test_error_dict_response_returns_none(self):
        """zKB returns {"error": "..."} (HTTP 200) — must return None without raising."""
        import asyncio

        from evealert.tools.zkillboard import ZkillboardClient

        client = ZkillboardClient()
        with respx.mock:
            respx.post("https://esi.evetech.net/latest/universe/ids/").mock(
                return_value=Response(
                    200, json={"systems": [{"id": 30000142, "name": "Jita"}]}
                )
            )
            respx.get(
                "https://zkillboard.com/api/kills/solarSystemID/30000142/"
            ).mock(return_value=Response(200, json={"error": "revoked"}))
            result = asyncio.run(client.get_recent_kills("Jita", limit=3))
        self.assertIsNone(result)


class CleanZkbEntriesTests(unittest.TestCase):
    """Unit tests for clean_zkb_entries() — the [null] normalizer (#133)."""

    def setUp(self):
        from evealert.tools.zkillboard import clean_zkb_entries
        self.fn = clean_zkb_entries

    def test_null_list_returns_empty(self):
        self.assertEqual(self.fn([None]), [])

    def test_empty_list_returns_empty(self):
        self.assertEqual(self.fn([]), [])

    def test_valid_entry_passes_through(self):
        entry = {"killmail_id": 1}
        self.assertEqual(self.fn([entry]), [entry])

    def test_mixed_null_and_valid(self):
        entry = {"killmail_id": 2}
        self.assertEqual(self.fn([None, entry]), [entry])

    def test_error_dict_returns_empty(self):
        self.assertEqual(self.fn({"error": "x"}), [])

    def test_none_input_returns_empty(self):
        self.assertEqual(self.fn(None), [])


if __name__ == "__main__":
    unittest.main()
