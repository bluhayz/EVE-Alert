"""Tests for evealert.tools.neighbor_monitor.NeighborMonitor -- #233.

#233: _poll_once() fired one concurrent zKB request per nearby system with
no bound at all -- at max_jumps=5 in well-connected space that's easily
50-150 simultaneous requests every poll cycle, risking zKB rate-limiting
(which then degrades every other zKB-backed feature).
"""

import asyncio
import unittest
from unittest import mock

from evealert.tools.neighbor_monitor import (
    _MAX_CONCURRENT_ZKB_REQUESTS,
    NeighborMonitor,
)


class _FakeCache:
    """Minimal stand-in: N nearby systems, each 1 jump away."""

    def __init__(self, system_ids):
        self._nearby = {sid: 1 for sid in system_ids}
        self._names = {sid: f"Sys{sid}" for sid in system_ids}

    async def get_systems_within_jumps(self, origin_id, max_jumps):
        return dict(self._nearby)

    async def get_system_name(self, system_id):
        return self._names.get(system_id)


class BoundedConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_zkb_calls_never_exceed_the_cap(self):
        system_ids = list(range(1, 41))  # 40 "nearby" systems
        cache = _FakeCache(system_ids)
        monitor = NeighborMonitor("Jita", max_jumps=5, min_kills=999)

        concurrent = {"current": 0, "peak": 0}
        lock = asyncio.Lock()

        async def fake_kills_15min(system_id):
            async with lock:
                concurrent["current"] += 1
                concurrent["peak"] = max(concurrent["peak"], concurrent["current"])
            await asyncio.sleep(0.01)  # hold the "slot" long enough to overlap
            async with lock:
                concurrent["current"] -= 1
            return 0

        with mock.patch.object(monitor, "_kills_15min", fake_kills_15min):
            await monitor._poll_once(cache, origin_id=0)

        self.assertLessEqual(concurrent["peak"], _MAX_CONCURRENT_ZKB_REQUESTS)
        self.assertGreater(concurrent["peak"], 1)  # actually ran concurrently, not serially

    async def test_all_systems_still_get_checked_despite_the_cap(self):
        system_ids = list(range(1, 11))
        cache = _FakeCache(system_ids)
        monitor = NeighborMonitor("Jita", max_jumps=5, min_kills=1)

        checked = []

        async def fake_kills_15min(system_id):
            checked.append(system_id)
            return 5 if system_id == 3 else 0

        alerts = []
        monitor._callback = alerts.append

        with mock.patch.object(monitor, "_kills_15min", fake_kills_15min):
            await monitor._poll_once(cache, origin_id=0)

        self.assertEqual(sorted(checked), system_ids)
        self.assertEqual(len(alerts), 1)
        self.assertIn("Sys3", alerts[0])


if __name__ == "__main__":
    unittest.main()
