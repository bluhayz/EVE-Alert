"""Tests for evealert.tools.threat_score (#141)."""

import unittest

from evealert.tools.threat_score import ThreatAssessment, compute_threat_score


class ThreatScoreTests(unittest.TestCase):
    def test_cyno_always_critical_10(self):
        a = compute_threat_score(is_cyno=True)
        self.assertEqual(a.score, 10)
        self.assertEqual(a.label, "CRITICAL")
        self.assertIn("Cynosural", a.reasons[0])

    def test_empty_returns_zero_caution(self):
        a = compute_threat_score()
        self.assertEqual(a.score, 0)
        self.assertEqual(a.label, "CAUTION")

    def test_single_hostile_1pt(self):
        a = compute_threat_score(local_hostile_count=1)
        self.assertEqual(a.score, 1)

    def test_three_hostiles_3pts(self):
        a = compute_threat_score(local_hostile_count=3)
        self.assertGreaterEqual(a.score, 3)

    def test_kos_adds_2(self):
        a = compute_threat_score(is_kos=True)
        self.assertGreaterEqual(a.score, 2)

    def test_full_danger_ratio_adds_2(self):
        a = compute_threat_score(danger_ratio=1.0)
        self.assertGreaterEqual(a.score, 2)

    def test_tackle_class_adds_2(self):
        a = compute_threat_score(dscan_threat_class="tackle")
        self.assertGreaterEqual(a.score, 2)

    def test_dictor_class_adds_2(self):
        a = compute_threat_score(dscan_threat_class="dictor")
        self.assertGreaterEqual(a.score, 2)

    def test_combat_class_adds_1(self):
        a_combat = compute_threat_score(dscan_threat_class="combat")
        a_empty  = compute_threat_score()
        self.assertEqual(a_combat.score - a_empty.score, 1)

    def test_adjacent_kills_adds_1(self):
        a = compute_threat_score(adjacent_kills=2)
        self.assertEqual(a.score, 1)

    def test_combined_high(self):
        a = compute_threat_score(
            local_hostile_count=1,
            is_kos=True,
            danger_ratio=0.8,
            dscan_threat_class="dictor",
        )
        self.assertGreaterEqual(a.score, 7)
        self.assertEqual(a.label, "CRITICAL")

    def test_score_capped_at_10(self):
        a = compute_threat_score(
            local_hostile_count=5,
            is_kos=True,
            danger_ratio=1.0,
            dscan_threat_class="force_recon",
            adjacent_kills=5,
        )
        self.assertEqual(a.score, 10)

    def test_str_format(self):
        a = ThreatAssessment(score=7, label="CRITICAL", reasons=["KOS", "Sabre"])
        self.assertIn("7/10", str(a))
        self.assertIn("CRITICAL", str(a))

    def test_high_label_threshold(self):
        # 4–6 = HIGH
        a = compute_threat_score(local_hostile_count=2, is_kos=True)
        self.assertGreaterEqual(a.score, 4)
        self.assertIn(a.label, ("HIGH", "CRITICAL"))

    def test_zero_history_leaves_behavioral_label_none(self):
        """#218: no history args passed -- behavioral_label must be None,
        matching pre-#218 ThreatAssessment (default field value)."""
        a = compute_threat_score(local_hostile_count=2, is_kos=True)
        self.assertIsNone(a.behavioral_label)


class HistorySignalTests(unittest.TestCase):
    """#218: sighting history is an additive signal on top of #141's
    existing inputs -- a pilot with frequent recent local sightings must
    score measurably higher than an identical pilot with no history."""

    def test_frequent_sightings_score_higher_than_no_history(self):
        a_with_history = compute_threat_score(local_hostile_count=1, history_frequency=5)
        a_without = compute_threat_score(local_hostile_count=1)
        self.assertGreater(a_with_history.score, a_without.score)

    def test_below_threshold_frequency_does_not_add_points(self):
        below = compute_threat_score(local_hostile_count=1, history_frequency=2)
        baseline = compute_threat_score(local_hostile_count=1)
        self.assertEqual(below.score, baseline.score)

    def test_at_threshold_frequency_adds_one_point(self):
        at_threshold = compute_threat_score(local_hostile_count=1, history_frequency=3)
        baseline = compute_threat_score(local_hostile_count=1)
        self.assertEqual(at_threshold.score - baseline.score, 1)

    def test_regular_route_adds_one_point(self):
        with_route = compute_threat_score(
            local_hostile_count=1, history_is_regular_route=True
        )
        baseline = compute_threat_score(local_hostile_count=1)
        self.assertEqual(with_route.score - baseline.score, 1)

    def test_frequency_and_route_combine_to_max_two_points(self):
        both = compute_threat_score(
            local_hostile_count=1, history_frequency=5, history_is_regular_route=True
        )
        baseline = compute_threat_score(local_hostile_count=1)
        self.assertEqual(both.score - baseline.score, 2)

    def test_history_still_respects_the_overall_10_cap(self):
        a = compute_threat_score(
            local_hostile_count=5, is_kos=True, danger_ratio=1.0,
            dscan_threat_class="force_recon", adjacent_kills=5,
            history_frequency=10, history_is_regular_route=True,
        )
        self.assertEqual(a.score, 10)


class WatchlistSignalTests(unittest.TestCase):
    """#240: watchlist membership is an additive signal, same pattern as
    #218's history signal -- defaults to False/no-op so pre-#240 callers
    (and this milestone's other tests) get byte-identical scores."""

    def test_watchlisted_adds_one_point(self):
        watchlisted = compute_threat_score(local_hostile_count=1, is_watchlisted=True)
        baseline = compute_threat_score(local_hostile_count=1)
        self.assertEqual(watchlisted.score - baseline.score, 1)

    def test_default_is_watchlisted_false_leaves_score_unaffected(self):
        explicit_false = compute_threat_score(local_hostile_count=1, is_watchlisted=False)
        baseline = compute_threat_score(local_hostile_count=1)
        self.assertEqual(explicit_false.score, baseline.score)

    def test_reason_mentions_watchlist(self):
        a = compute_threat_score(local_hostile_count=1, is_watchlisted=True)
        self.assertIn("on hostile watchlist", a.reasons)

    def test_watchlist_still_respects_the_overall_10_cap(self):
        a = compute_threat_score(
            local_hostile_count=5, is_kos=True, danger_ratio=1.0,
            dscan_threat_class="force_recon", adjacent_kills=5,
            history_frequency=10, history_is_regular_route=True,
            is_watchlisted=True,
        )
        self.assertEqual(a.score, 10)


class BehavioralLabelTests(unittest.TestCase):
    """#218: explicit thresholds for the behavioral label, separate from
    the numeric score."""

    def test_five_or_more_is_frequent_resident(self):
        a = compute_threat_score(history_frequency=5)
        self.assertEqual(a.behavioral_label, "frequent resident")

    def test_two_to_four_is_occasional_visitor(self):
        for freq in (2, 3, 4):
            with self.subTest(freq=freq):
                a = compute_threat_score(history_frequency=freq)
                self.assertEqual(a.behavioral_label, "occasional visitor")

    def test_exactly_one_is_single_sighting(self):
        a = compute_threat_score(history_frequency=1)
        self.assertEqual(a.behavioral_label, "single sighting")

    def test_zero_frequency_but_regular_route_is_pass_through(self):
        a = compute_threat_score(history_frequency=0, history_is_regular_route=True)
        self.assertEqual(a.behavioral_label, "known to pass through")

    def test_no_signal_at_all_is_no_label(self):
        a = compute_threat_score(history_frequency=0, history_is_regular_route=False)
        self.assertIsNone(a.behavioral_label)

    def test_label_appears_in_str_output(self):
        a = compute_threat_score(local_hostile_count=1, history_frequency=5)
        self.assertIn("(frequent resident)", str(a))

    def test_no_label_no_parens_in_str_output(self):
        a = compute_threat_score(local_hostile_count=1)
        self.assertNotIn("(", str(a))


if __name__ == "__main__":
    unittest.main()
