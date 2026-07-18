"""Tests for evealert.tools.r2z2 (#169, v7.1) -- the live killmail
stream consumer (R2Z2, RedisQ's documented replacement after its
2026-05-31 sunset)."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import respx
from httpx import Request, Response

from evealert.tools import r2z2
from evealert.tools.r2z2 import LiveKillmail, R2Z2Consumer, _parse_package

_SEQUENCE_URL = "https://r2z2.zkillboard.com/ephemeral/sequence.json"


def _make_package(
    killmail_id=1, solar_system_id=30000142, ship_type_id=587,
    victim_alliance_id=None, attacker_alliance_ids=None, location_id=None,
):
    attackers = []
    for aid in (attacker_alliance_ids or []):
        attackers.append({"alliance_id": aid, "character_id": 111, "corporation_id": 222})
    return {
        "killmail_id": killmail_id,
        "killmail": {
            "solar_system_id": solar_system_id,
            "victim": {
                "ship_type_id": ship_type_id,
                "alliance_id": victim_alliance_id,
                "corporation_id": 456,
            },
            "attackers": attackers,
        },
        "zkb": {"locationID": location_id},
    }


class ParsePackageTests(unittest.TestCase):
    def test_parses_a_well_formed_package(self):
        package = _make_package(
            killmail_id=42, solar_system_id=30000142,
            attacker_alliance_ids=[99, 100],
        )
        km = _parse_package(package)
        self.assertIsNotNone(km)
        self.assertEqual(km.killmail_id, 42)
        self.assertEqual(km.solar_system_id, 30000142)
        self.assertEqual(km.attacker_count, 2)
        self.assertEqual(km.alliance_ids, {99, 100})

    def test_victim_alliance_included(self):
        package = _make_package(victim_alliance_id=77)
        km = _parse_package(package)
        self.assertIn(77, km.alliance_ids)

    def test_missing_killmail_id_returns_none(self):
        package = _make_package()
        del package["killmail_id"]
        self.assertIsNone(_parse_package(package))

    def test_missing_killmail_key_returns_none(self):
        self.assertIsNone(_parse_package({"zkb": {}}))

    def test_missing_solar_system_id_returns_none(self):
        package = _make_package()
        del package["killmail"]["solar_system_id"]
        self.assertIsNone(_parse_package(package))

    def test_missing_victim_and_attackers_still_parses(self):
        package = {
            "killmail_id": 1,
            "killmail": {"solar_system_id": 30000142},
        }
        km = _parse_package(package)
        self.assertIsNotNone(km)
        self.assertEqual(km.attacker_count, 0)
        self.assertEqual(km.alliance_ids, set())

    def test_non_dict_attacker_entries_ignored(self):
        package = _make_package()
        package["killmail"]["attackers"] = ["not-a-dict", 42, None]
        km = _parse_package(package)
        self.assertIsNotNone(km)
        self.assertEqual(km.attacker_count, 3)  # still counted
        self.assertEqual(km.alliance_ids, set())  # but none contribute IDs

    def test_completely_malformed_package_returns_none_not_raises(self):
        self.assertIsNone(_parse_package({}))
        self.assertIsNone(_parse_package({"killmail": "not-a-dict"}))
        self.assertIsNone(_parse_package("not-even-a-dict"))

    def test_location_id_extracted_from_zkb_block(self):
        package = _make_package(location_id=60003760)
        km = _parse_package(package)
        self.assertEqual(km.location_id, 60003760)


class ResolveShipNameTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        r2z2._ship_name_cache.clear()

    def tearDown(self):
        r2z2._ship_name_cache.clear()

    async def test_resolves_and_caches_name(self):
        mock_client = AsyncMock()
        fake_request = Request("GET", "https://esi.evetech.net/latest/universe/types/587/")
        mock_client.get = AsyncMock(
            return_value=Response(200, json={"name": "Rifter"}, request=fake_request)
        )
        name = await r2z2.resolve_ship_name(mock_client, 587)
        self.assertEqual(name, "Rifter")
        mock_client.get.assert_awaited_once()

        # Second call for the same type_id must NOT hit the network again.
        name2 = await r2z2.resolve_ship_name(mock_client, 587)
        self.assertEqual(name2, "Rifter")
        mock_client.get.assert_awaited_once()

    async def test_none_type_id_returns_none_without_a_call(self):
        mock_client = AsyncMock()
        result = await r2z2.resolve_ship_name(mock_client, None)
        self.assertIsNone(result)
        mock_client.get.assert_not_called()

    async def test_http_failure_returns_none(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("boom"))
        result = await r2z2.resolve_ship_name(mock_client, 999)
        self.assertIsNone(result)


class HandlePackageFilterTests(unittest.TestCase):
    """#169: filtering (jump-radius + alliance watchlist) happens inside
    the consumer, so on_kill only fires for relevant kills and the
    rolling buffer never grows with every kill in the game."""

    def test_kill_within_nearby_systems_is_matched(self):
        received = []
        consumer = R2Z2Consumer(on_kill=lambda km, jd: received.append((km, jd)))
        consumer._nearby_systems = {30000142: 2}

        consumer._handle_package(_make_package(solar_system_id=30000142))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][1], 2)  # jump distance
        self.assertEqual(len(consumer.get_recent_kills()), 1)

    def test_kill_matching_watchlist_alliance_is_matched(self):
        received = []
        consumer = R2Z2Consumer(
            alliance_watchlist={99}, on_kill=lambda km, jd: received.append((km, jd))
        )
        consumer._handle_package(
            _make_package(solar_system_id=1, attacker_alliance_ids=[99])
        )
        self.assertEqual(len(received), 1)
        self.assertIsNone(received[0][1])  # no jump distance -- watchlist match only

    def test_kill_matching_neither_is_discarded(self):
        received = []
        consumer = R2Z2Consumer(on_kill=lambda km, jd: received.append((km, jd)))
        consumer._nearby_systems = {30000142: 2}

        consumer._handle_package(_make_package(solar_system_id=99999999))

        self.assertEqual(received, [])
        self.assertEqual(consumer.get_recent_kills(), [])

    def test_malformed_package_does_not_crash_or_call_on_kill(self):
        received = []
        consumer = R2Z2Consumer(on_kill=lambda km, jd: received.append((km, jd)))
        consumer._nearby_systems = {30000142: 2}

        consumer._handle_package({"garbage": True})

        self.assertEqual(received, [])

    def test_on_kill_exception_does_not_propagate(self):
        def _raises(km, jd):
            raise RuntimeError("boom")

        consumer = R2Z2Consumer(on_kill=_raises)
        consumer._nearby_systems = {30000142: 2}

        consumer._handle_package(_make_package(solar_system_id=30000142))  # must not raise

    def test_no_on_kill_callback_still_buffers(self):
        consumer = R2Z2Consumer()
        consumer._nearby_systems = {30000142: 2}
        consumer._handle_package(_make_package(solar_system_id=30000142))
        self.assertEqual(len(consumer.get_recent_kills()), 1)


class RollingBufferWindowTests(unittest.TestCase):
    def test_get_recent_kills_respects_time_window(self):
        consumer = R2Z2Consumer()
        now = 1_000_000.0
        old_km = LiveKillmail(1, 1, None, 0, None)
        new_km = LiveKillmail(2, 1, None, 0, None)
        consumer._recent_kills.append((now - 3600, old_km))  # 1h ago
        consumer._recent_kills.append((now - 60, new_km))    # 1min ago

        with patch("evealert.tools.r2z2.time.time", return_value=now):
            recent = consumer.get_recent_kills(within_seconds=1800)  # 30 min

        self.assertEqual(recent, [new_km])

    def test_kill_count_since_matches_get_recent_kills_length(self):
        consumer = R2Z2Consumer()
        consumer._nearby_systems = {1: 1}
        consumer._handle_package(_make_package(solar_system_id=1, killmail_id=1))
        consumer._handle_package(_make_package(solar_system_id=1, killmail_id=2))
        self.assertEqual(consumer.kill_count_since(1800), 2)


class ConsumerRunLoopTests(unittest.IsolatedAsyncioTestCase):
    """#169: the actual polling loop against a mocked R2Z2 -- sequence
    fetch, 200/404 handling, and clean stop()."""

    def setUp(self):
        # Every backoff/poll-delay path in run() calls asyncio.sleep();
        # mocking it to a no-op keeps these tests fast (real 6s R2Z2
        # backoff sleeps would otherwise make this file slow) without
        # changing what's being verified (call sequencing, not timing).
        self._sleep_patcher = patch(
            "evealert.tools.r2z2.asyncio.sleep", new=AsyncMock(return_value=None)
        )
        self._sleep_patcher.start()

    def tearDown(self):
        self._sleep_patcher.stop()

    async def test_fetches_starting_sequence_then_polls_and_stops(self):
        received = []
        consumer = R2Z2Consumer(on_kill=lambda km, jd: received.append(km))

        call_count = {"n": 0}

        def _kill_route(request):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return Response(
                    200, json=_make_package(killmail_id=1, solar_system_id=1)
                )
            consumer.stop()
            return Response(404)

        with respx.mock:
            respx.get(_SEQUENCE_URL).mock(return_value=Response(200, json={"sequence": 500}))
            respx.get(url__regex=r".*/ephemeral/\d+\.json").mock(side_effect=_kill_route)
            consumer._nearby_systems = {1: 0}
            await consumer.run()

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].killmail_id, 1)
        # Sequence advanced past the one successful kill.
        self.assertEqual(consumer.last_sequence, 501)

    async def test_no_starting_sequence_available_exits_cleanly(self):
        consumer = R2Z2Consumer()
        with respx.mock:
            respx.get(_SEQUENCE_URL).mock(return_value=Response(500))
            await consumer.run()  # must return, not hang or raise
        self.assertIsNone(consumer.last_sequence)

    async def test_explicit_starting_sequence_skips_sequence_json_call(self):
        consumer = R2Z2Consumer(sequence=999)

        def _kill_route(request):
            consumer.stop()
            return Response(404)

        with respx.mock:
            seq_route = respx.get(_SEQUENCE_URL).mock(return_value=Response(200, json={"sequence": 1}))
            respx.get(url__regex=r".*/ephemeral/\d+\.json").mock(side_effect=_kill_route)
            await consumer.run()

        self.assertFalse(seq_route.called)

    async def test_stale_sequence_resyncs_to_live_after_threshold(self):
        """A resumed (persisted) sequence that 404s past the staleness
        threshold must resync to the live tail rather than waiting
        forever on an expired sequence."""
        consumer = R2Z2Consumer(sequence=100)
        call_times = iter([0.0, 0.0, 400.0])  # 3rd check exceeds the 300s threshold

        def _kill_route(request):
            try:
                next(call_times)
            except StopIteration:
                consumer.stop()
            return Response(404)

        with respx.mock:
            seq_route = respx.get(_SEQUENCE_URL).mock(
                return_value=Response(200, json={"sequence": 9999})
            )
            respx.get(url__regex=r".*/ephemeral/\d+\.json").mock(side_effect=_kill_route)

            times = [0.0, 0.0, 400.0, 400.0]
            with patch(
                "evealert.tools.r2z2.time.time",
                side_effect=lambda: times.pop(0) if times else 400.0,
            ):
                await consumer.run()

        self.assertTrue(seq_route.called)
        self.assertEqual(consumer.last_sequence, 9999)


if __name__ == "__main__":
    unittest.main()
