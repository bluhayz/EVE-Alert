"""Tests for evealert.tools.pilot_history_analytics (#216/#217, v7.0)."""

import unittest

from evealert.tools.pilot_history_store import Sighting


def _sighting(system=None, ship=None, source="local", seen_at=0.0, pilot="Bad Guy"):
    return Sighting(
        pilot_name=pilot, system=system, ship=ship, source=source,
        corp=None, alliance=None, seen_at=seen_at,
    )


class _FakeCache:
    """A minimal async stand-in for UniverseCache: every system resolves
    (the name itself stands in for an ID) and every route is 1 jump
    (always "plausible"), unless a pair is listed in *long_routes* --
    those return a route long enough to exceed MAX_PLAUSIBLE_JUMPS."""

    def __init__(self, long_routes: set | None = None):
        self.long_routes = long_routes or set()

    async def get_system_id(self, name):
        return name

    async def get_route(self, id_a, id_b):
        # Route length is len(route)-1 jumps; 10 hops safely exceeds
        # MAX_PLAUSIBLE_JUMPS (5) for the "implausible" case.
        hop_count = 10 if (id_a, id_b) in self.long_routes or (id_b, id_a) in self.long_routes else 1
        return list(range(hop_count + 1))


class SummarizeTests(unittest.TestCase):
    def test_none_when_below_minimum_sightings(self):
        from evealert.tools.pilot_history_analytics import summarize

        sightings = [_sighting(seen_at=1.0), _sighting(seen_at=2.0)]  # only 2
        self.assertIsNone(summarize("Bad Guy", sightings=sightings))

    def test_summary_present_at_minimum_sightings(self):
        from evealert.tools.pilot_history_analytics import summarize

        sightings = [_sighting(seen_at=float(i)) for i in range(3)]
        summary = summarize("Bad Guy", sightings=sightings)
        self.assertIsNotNone(summary)
        self.assertEqual(summary.sighting_count, 3)

    def test_top_systems_ordered_by_frequency(self):
        from evealert.tools.pilot_history_analytics import summarize

        sightings = (
            [_sighting(system="J5A-IX", seen_at=float(i)) for i in range(5)]
            + [_sighting(system="1DQ1-A", seen_at=float(i) + 10) for i in range(2)]
            + [_sighting(system="Jita", seen_at=20.0)]
        )
        summary = summarize("Bad Guy", sightings=sightings)
        self.assertEqual(
            summary.top_systems, [("J5A-IX", 5), ("1DQ1-A", 2), ("Jita", 1)]
        )

    def test_top_ship_is_most_common(self):
        from evealert.tools.pilot_history_analytics import summarize

        sightings = (
            [_sighting(ship="Loki", seen_at=float(i)) for i in range(4)]
            + [_sighting(ship="Legion", seen_at=float(i) + 10) for i in range(2)]
        )
        summary = summarize("Bad Guy", sightings=sightings)
        self.assertEqual(summary.top_ship, "Loki")

    def test_top_ship_none_when_no_ships_recorded(self):
        from evealert.tools.pilot_history_analytics import summarize

        sightings = [_sighting(seen_at=float(i)) for i in range(3)]
        summary = summarize("Bad Guy", sightings=sightings)
        self.assertIsNone(summary.top_ship)

    def test_first_and_last_seen_span_full_range(self):
        from evealert.tools.pilot_history_analytics import summarize

        sightings = [
            _sighting(seen_at=1000.0),
            _sighting(seen_at=5000.0),
            _sighting(seen_at=3000.0),
        ]
        summary = summarize("Bad Guy", sightings=sightings)
        self.assertEqual(summary.first_seen, 1000.0)
        self.assertEqual(summary.last_seen, 5000.0)

    def test_active_hour_range_finds_the_peak_window(self):
        from evealert.tools.pilot_history_analytics import summarize

        # Epoch 0 = 1970-01-01T00:00:00 UTC; use whole-day offsets so every
        # sighting lands at the same hour-of-day regardless of which day.
        day = 86400
        # 5 sightings clustered at hour 20 UTC, 1 sighting at hour 3 UTC.
        sightings = [_sighting(seen_at=day * i + 20 * 3600) for i in range(5)]
        sightings.append(_sighting(seen_at=day * 10 + 3 * 3600))
        summary = summarize("Bad Guy", sightings=sightings)
        self.assertIsNotNone(summary.active_hour_range)
        start_hour = int(summary.active_hour_range.split(":")[0])
        # The 3-hour window containing hour 20 must be the winner.
        self.assertIn(20, {start_hour, (start_hour + 1) % 24, (start_hour + 2) % 24})

    def test_uses_provided_sightings_without_querying_store(self):
        """Passing sightings= must never touch the real DB/get_sightings."""
        from unittest.mock import patch

        from evealert.tools.pilot_history_analytics import summarize

        sightings = [_sighting(seen_at=float(i)) for i in range(3)]
        with patch(
            "evealert.tools.pilot_history_analytics.get_sightings"
        ) as mock_get:
            summarize("Bad Guy", sightings=sightings)
        mock_get.assert_not_called()


class FormatSummaryTests(unittest.TestCase):
    def test_full_summary_matches_expected_format(self):
        from evealert.tools.pilot_history_analytics import PilotSummary, format_summary

        summary = PilotSummary(
            pilot_name="Bad Guy",
            sighting_count=14,
            first_seen=0.0,
            last_seen=45 * 86400.0,
            top_systems=[("J5A-IX", 9), ("1DQ1-A", 3)],
            top_ship="Loki",
            active_hour_range="19:00-22:00",
        )
        text = format_summary(summary)
        self.assertEqual(
            text,
            "14 sightings over 45d — mostly in J5A-IX (9x), 1DQ1-A (3x); "
            "usually flies Loki; most active 19:00-22:00",
        )

    def test_minimal_summary_has_no_trailing_dash(self):
        from evealert.tools.pilot_history_analytics import PilotSummary, format_summary

        summary = PilotSummary(
            pilot_name="Bad Guy", sighting_count=3, first_seen=0.0, last_seen=100.0,
            top_systems=[], top_ship=None, active_hour_range=None,
        )
        text = format_summary(summary)
        self.assertEqual(text, "3 sightings over 1d")
        self.assertNotIn("—", text)

    def test_only_top_two_systems_shown(self):
        from evealert.tools.pilot_history_analytics import PilotSummary, format_summary

        summary = PilotSummary(
            pilot_name="Bad Guy", sighting_count=10, first_seen=0.0, last_seen=86400.0,
            top_systems=[("A", 5), ("B", 3), ("C", 2)],
            top_ship=None, active_hour_range=None,
        )
        text = format_summary(summary)
        self.assertIn("A (5x), B (3x)", text)
        self.assertNotIn("C (2x)", text)


class GroupIntoSessionsTests(unittest.TestCase):
    def test_splits_on_gaps_larger_than_session_gap_hours(self):
        from evealert.tools.pilot_history_analytics import _group_into_sessions

        hour = 3600.0
        sightings = [
            _sighting(seen_at=0.0),
            _sighting(seen_at=1 * hour),   # same session: gap = 1h < 4h
            _sighting(seen_at=10 * hour),  # new session: gap = 9h > 4h
            _sighting(seen_at=11 * hour),  # same session as previous
        ]
        sessions = _group_into_sessions(sightings)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(len(sessions[0]), 2)
        self.assertEqual(len(sessions[1]), 2)

    def test_empty_input_returns_empty_list(self):
        from evealert.tools.pilot_history_analytics import _group_into_sessions

        self.assertEqual(_group_into_sessions([]), [])


class InferPathingTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_sightings_returns_none(self):
        from evealert.tools.pilot_history_analytics import infer_pathing

        result = await infer_pathing("Nobody", sightings=[], cache=_FakeCache())
        self.assertIsNone(result)

    async def test_clear_repeated_transition_identified(self):
        from evealert.tools.pilot_history_analytics import infer_pathing

        day = 86400.0
        sightings = []
        for i in range(3):
            t = i * day
            sightings.append(_sighting(system="J5A-IX", seen_at=t))
            sightings.append(_sighting(system="1DQ1-A", seen_at=t + 3600))
        # Extra J5A-IX sighting (own session) so it's unambiguously "home".
        sightings.append(_sighting(system="J5A-IX", seen_at=10 * day))

        result = await infer_pathing("Bad Guy", sightings=sightings, cache=_FakeCache())

        self.assertIsNotNone(result)
        self.assertEqual(result.home_system, "J5A-IX")
        self.assertEqual(result.top_transitions[0], (("J5A-IX", "1DQ1-A"), 3))

    async def test_low_data_no_repeated_transition_returns_none(self):
        from evealert.tools.pilot_history_analytics import infer_pathing

        sightings = [
            _sighting(system="Jita", seen_at=0.0),
            _sighting(system="Amarr", seen_at=3600.0),  # transition occurs once only
        ]
        result = await infer_pathing("Bad Guy", sightings=sightings, cache=_FakeCache())
        self.assertIsNone(result)

    async def test_session_split_prevents_a_false_transition(self):
        """Two systems that are hours apart (different sessions) must not
        be counted as a same-session transition -- confirmed indirectly:
        3 far-apart occurrences of the same raw pair never accumulate a
        same-session transition count, so the result stays None."""
        from evealert.tools.pilot_history_analytics import infer_pathing

        day = 86400.0
        sightings = []
        for i in range(3):
            t = i * day
            sightings.append(_sighting(system="Jita", seen_at=t))
            # 6h later -- exceeds SESSION_GAP_HOURS (4h), so this starts a
            # NEW session and is never counted as a transition FROM Jita.
            sightings.append(_sighting(system="Amarr", seen_at=t + 6 * 3600))

        result = await infer_pathing("Bad Guy", sightings=sightings, cache=_FakeCache())
        self.assertIsNone(result)

    async def test_implausible_transition_filtered_out(self):
        from evealert.tools.pilot_history_analytics import infer_pathing

        day = 86400.0
        sightings = []
        for i in range(3):
            t = i * day
            sightings.append(_sighting(system="Jita", seen_at=t))
            sightings.append(_sighting(system="FarAwaySystem", seen_at=t + 3600))

        cache = _FakeCache(long_routes={("Jita", "FarAwaySystem")})
        result = await infer_pathing("Bad Guy", sightings=sightings, cache=cache)
        self.assertIsNone(result)

    async def test_uses_provided_sightings_without_querying_store(self):
        from unittest.mock import patch

        from evealert.tools.pilot_history_analytics import infer_pathing

        day = 86400.0
        sightings = []
        for i in range(3):
            t = i * day
            sightings.append(_sighting(system="Jita", seen_at=t))
            sightings.append(_sighting(system="Amarr", seen_at=t + 3600))

        with patch(
            "evealert.tools.pilot_history_analytics.get_sightings"
        ) as mock_get:
            await infer_pathing("Bad Guy", sightings=sightings, cache=_FakeCache())
        mock_get.assert_not_called()


class FormatPathingTests(unittest.TestCase):
    def test_matches_expected_format(self):
        from evealert.tools.pilot_history_analytics import PathingSummary, format_pathing

        pathing = PathingSummary(
            pilot_name="Bad Guy", home_system="J5A-IX",
            top_transitions=[(("J5A-IX", "1DQ1-A"), 5)],
        )
        text = format_pathing(pathing)
        self.assertEqual(text, "home J5A-IX; often moves J5A-IX -> 1DQ1-A")

    def test_no_transitions_shows_only_home(self):
        from evealert.tools.pilot_history_analytics import PathingSummary, format_pathing

        pathing = PathingSummary(
            pilot_name="Bad Guy", home_system="J5A-IX", top_transitions=[],
        )
        self.assertEqual(format_pathing(pathing), "home J5A-IX")


if __name__ == "__main__":
    unittest.main()
