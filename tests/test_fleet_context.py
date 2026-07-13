"""Tests for fleet_context ZKB handling + killmail dedup (issues #101, #106)."""

import asyncio
import unittest

import respx
from httpx import Response

from evealert.tools import fleet_context
from evealert.tools.fleet_context import (
    ActivityProfile,
    KillmailMonitor,
    _classify_fleet,
    _zkb_get,
)


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

    def test_null_list_returns_empty(self):
        """zKB [null] response must yield empty list, not crash (#133)."""
        async def fast_sleep(_):
            return None

        with respx.mock:
            respx.get("https://zkillboard.com/api/test/").mock(
                return_value=Response(200, json=[None])
            )
            orig = fleet_context.asyncio.sleep
            fleet_context.asyncio.sleep = fast_sleep
            try:
                result = asyncio.run(_zkb_get("https://zkillboard.com/api/test/"))
            finally:
                fleet_context.asyncio.sleep = orig
        self.assertEqual(result, [])


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


import collections


class ClassifyFleetTests(unittest.TestCase):
    def _counter(self, *ships):
        return collections.Counter(ships)

    def test_capital_fleet(self):
        self.assertEqual(
            _classify_fleet(self._counter("Nyx", "Carrier")), "Capital fleet"
        )

    def test_bomber_fleet(self):
        self.assertEqual(
            _classify_fleet(self._counter("Stealth Bomber")), "Bomber fleet"
        )

    def test_interceptor_gang(self):
        self.assertEqual(_classify_fleet(self._counter("Sabre")), "Interceptor gang")

    def test_battleship_fleet(self):
        self.assertEqual(
            _classify_fleet(self._counter("Battleship")), "Battleship fleet"
        )

    def test_mixed_composition_when_unrecognized(self):
        self.assertEqual(_classify_fleet(self._counter("Venture")), "Mixed composition")


class ActivityProfileTests(unittest.TestCase):
    def test_records_and_reports_peak_hours(self):
        prof = ActivityProfile()
        kills = [
            {"killmail_time": "2026-07-12T18:15:00Z"},
            {"killmail_time": "2026-07-12T18:45:00Z"},
            {"killmail_time": "2026-07-12T03:05:00Z"},
        ]
        prof.record_kills(30000142, kills)
        peaks = prof.peak_hours(30000142)
        self.assertEqual(peaks[0], (18, 2))  # 18:00 is the busiest hour
        self.assertIn("Peak hours", prof.summary(30000142))

    def test_summary_when_no_data(self):
        self.assertEqual(ActivityProfile().summary(999), "No activity data yet.")


if __name__ == "__main__":
    unittest.main()
