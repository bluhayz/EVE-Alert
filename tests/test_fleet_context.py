"""Tests for fleet_context ZKB handling + killmail dedup (issues #101, #106)."""

import asyncio
import unittest

import respx
from httpx import Response

from evealert.tools import fleet_context
from evealert.tools.fleet_context import KillmailMonitor, _zkb_get


class ZkbGetTests(unittest.TestCase):
    def test_error_dict_returns_none(self):
        with respx.mock:
            respx.get("https://zkillboard.com/api/test/").mock(
                return_value=Response(200, json={"error": "limit revoked"})
            )
            # Patch sleep so the rate-limit spacing doesn't slow the test.
            orig = fleet_context.asyncio.sleep

            async def fast_sleep(_):
                return None

            fleet_context.asyncio.sleep = fast_sleep
            try:
                result = asyncio.run(_zkb_get("https://zkillboard.com/api/test/"))
            finally:
                fleet_context.asyncio.sleep = orig
        self.assertIsNone(result)

    def test_list_payload_returned(self):
        async def fast_sleep(_):
            return None

        with respx.mock:
            respx.get("https://zkillboard.com/api/test/").mock(
                return_value=Response(200, json=[{"killmail_id": 1}])
            )
            orig = fleet_context.asyncio.sleep
            fleet_context.asyncio.sleep = fast_sleep
            try:
                result = asyncio.run(_zkb_get("https://zkillboard.com/api/test/"))
            finally:
                fleet_context.asyncio.sleep = orig
        self.assertEqual(result, [{"killmail_id": 1}])


class KillmailDedupTests(unittest.TestCase):
    def test_mark_seen_dedups(self):
        m = KillmailMonitor([1], callback=lambda msg: None)
        self.assertFalse(m._mark_seen(100))  # first time
        self.assertTrue(m._mark_seen(100))  # already seen

    def test_seen_set_is_bounded(self):
        m = KillmailMonitor([1], callback=lambda msg: None)
        maxlen = m._seen_order.maxlen
        for i in range(maxlen + 500):
            m._mark_seen(i)
        # The set never grows beyond the deque cap
        self.assertLessEqual(len(m._seen_ids), maxlen)
        self.assertEqual(len(m._seen_order), maxlen)


if __name__ == "__main__":
    unittest.main()
