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


if __name__ == "__main__":
    unittest.main()
