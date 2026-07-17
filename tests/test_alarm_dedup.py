"""Regression tests for per-enemy alarm dedup (issue #100).

A single enemy that stays on screen must alarm once (not once per poll);
a distinct enemy must alarm; sub-pixel jitter must not re-alarm; and an
enemy that leaves (seen-set cleared) must be able to re-alarm on return.
"""

import unittest
from unittest import mock

from evealert.manager.alertmanager import AlertAgent


class _DedupAgent(AlertAgent):
    """AlertAgent with the heavy __init__ bypassed — only the state the
    dedup helpers touch is initialised."""

    def __init__(self, cooldown=30):  # pylint: disable=super-init-not-called
        self._cooldown_enemy = cooldown
        self._enemy_points = []
        self._seen_enemies = {}
        self._rearm_minutes = 0  # disabled by default in unit tests


class QuantizeTests(unittest.TestCase):
    def test_points_in_same_grid_cell_collapse(self):
        self.assertEqual(
            AlertAgent._quantize_point((100, 100)),
            AlertAgent._quantize_point((103, 101)),
        )

    def test_distant_points_differ(self):
        self.assertNotEqual(
            AlertAgent._quantize_point((100, 100)),
            AlertAgent._quantize_point((500, 500)),
        )


class AlarmDedupTests(unittest.TestCase):
    def setUp(self):
        self.agent = _DedupAgent(cooldown=30)

    def test_same_enemy_alarms_once_then_suppressed(self):
        self.agent._enemy_points = [(100, 100)]
        self.assertTrue(self.agent._should_alarm_enemy())  # first sighting
        self.assertFalse(self.agent._should_alarm_enemy())  # still there → no re-alarm
        self.assertFalse(self.agent._should_alarm_enemy())

    def test_distinct_enemy_triggers_new_alarm(self):
        self.agent._enemy_points = [(100, 100)]
        self.assertTrue(self.agent._should_alarm_enemy())
        # Same enemy plus a new one at a distant position
        self.agent._enemy_points = [(100, 100), (500, 500)]
        self.assertTrue(self.agent._should_alarm_enemy())

    def test_subpixel_jitter_does_not_realarm(self):
        self.agent._enemy_points = [(100, 100)]
        self.assertTrue(self.agent._should_alarm_enemy())
        self.agent._enemy_points = [(103, 101)]  # same 20px grid cell
        self.assertFalse(self.agent._should_alarm_enemy())

    def test_reeligible_after_cooldown_window(self):
        with mock.patch("evealert.manager.alertmanager.time.time", return_value=1000.0):
            self.agent._enemy_points = [(100, 100)]
            self.assertTrue(self.agent._should_alarm_enemy())
            self.assertFalse(self.agent._should_alarm_enemy())
        # Advance beyond the cooldown window
        with mock.patch(
            "evealert.manager.alertmanager.time.time", return_value=1000.0 + 31
        ):
            self.assertTrue(self.agent._should_alarm_enemy())

    def test_cleared_seen_set_allows_realarm(self):
        self.agent._enemy_points = [(100, 100)]
        self.assertTrue(self.agent._should_alarm_enemy())
        # Simulates reset_alarm("Enemy") clearing the set when the enemy leaves
        self.agent._seen_enemies = {}
        self.assertTrue(self.agent._should_alarm_enemy())

    def test_seen_set_pruned_to_current_enemies(self):
        self.agent._enemy_points = [(100, 100), (500, 500)]
        self.agent._should_alarm_enemy()
        self.assertEqual(len(self.agent._seen_enemies), 2)
        # One enemy leaves; the seen-set must not retain the stale key.
        self.agent._enemy_points = [(100, 100)]
        self.agent._should_alarm_enemy()
        self.assertEqual(len(self.agent._seen_enemies), 1)


class NameBasedDedupTests(unittest.TestCase):
    """#213: dedup identity is the OCR-resolved pilot name (when available),
    not just the icon's screen position — a Local roster re-sort must not
    make a still-present pilot look like a brand-new enemy, and a genuinely
    different pilot landing on an old position must not be suppressed."""

    def setUp(self):
        self.agent = _DedupAgent(cooldown=30)

    def test_roster_resort_same_name_does_not_realarm(self):
        """The exact bug scenario: same pilot, position shifts (roster
        re-sorted), name stays constant -> must NOT re-alarm mid-cooldown."""
        pos1 = (100, 100)
        pos2 = (100, 300)  # different row after a re-sort
        key1 = AlertAgent._quantize_point(pos1)
        key2 = AlertAgent._quantize_point(pos2)

        self.agent._enemy_points = [pos1]
        self.assertTrue(
            self.agent._should_alarm_enemy({key1: "Bad Guy"})
        )  # first sighting, identified by name

        # Roster re-sorts: same pilot, new row/position, but OCR still
        # resolves the SAME name.
        self.agent._enemy_points = [pos2]
        self.assertFalse(
            self.agent._should_alarm_enemy({key2: "Bad Guy"}),
            "Same pilot name at a new screen position must not re-alarm "
            "within the cooldown window.",
        )

    def test_different_names_same_position_are_not_conflated(self):
        """Pilot A leaves; pilot B lands on the exact position A used to
        occupy. B must not inherit A's cooldown."""
        pos = (100, 100)
        key = AlertAgent._quantize_point(pos)

        self.agent._enemy_points = [pos]
        self.assertTrue(self.agent._should_alarm_enemy({key: "Pilot A"}))
        self.assertFalse(self.agent._should_alarm_enemy({key: "Pilot A"}))

        # Same screen position, but OCR now identifies a DIFFERENT pilot.
        self.assertTrue(
            self.agent._should_alarm_enemy({key: "Pilot B"}),
            "A different pilot at the same screen position must alarm "
            "even though that position was recently in cooldown.",
        )

    def test_unresolved_icon_falls_back_to_position_identity(self):
        """When OCR can't identify a specific icon (missing from the
        identities dict), dedup falls back to that icon's position -- same
        behavior as before #213 for the OCR-failed case."""
        pos = (100, 100)
        self.agent._enemy_points = [pos]
        self.assertTrue(self.agent._should_alarm_enemy({}))  # OCR resolved nothing
        self.assertFalse(self.agent._should_alarm_enemy({}))  # still falls back, still cooling down

    def test_no_identities_arg_behaves_like_pure_position_dedup(self):
        """Backward-compatible default: calling with no argument at all
        (e.g. from any caller not yet OCR-aware) is identical to the
        pre-#213 position-only behavior."""
        self.agent._enemy_points = [(100, 100)]
        self.assertTrue(self.agent._should_alarm_enemy())
        self.assertFalse(self.agent._should_alarm_enemy())


if __name__ == "__main__":
    unittest.main()
