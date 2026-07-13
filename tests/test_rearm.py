"""Tests for per-enemy re-alert after sustained presence (#144)."""

import time
import unittest

from evealert.manager.alertmanager import AlertAgent, _EnemySighting


def _make_agent():
    """Build a minimal AlertAgent without requiring a main window."""
    from unittest.mock import MagicMock
    agent = AlertAgent.__new__(AlertAgent)
    # Minimal __init__ stubs for the attributes we exercise
    agent._enemy_points = []
    agent._seen_enemies = {}
    agent._cooldown_enemy = 999_999  # effectively disable cooldown for tests
    agent._rearm_minutes = 0
    return agent


class EnemySightingTests(unittest.TestCase):
    def test_first_sight_triggers(self):
        ag = _make_agent()
        ag._enemy_points = [(100, 100)]
        self.assertTrue(ag._should_alarm_enemy())

    def test_second_sight_within_cooldown_does_not_trigger(self):
        ag = _make_agent()
        ag._enemy_points = [(100, 100)]
        ag._should_alarm_enemy()  # first call — trigger
        self.assertFalse(ag._should_alarm_enemy())  # same frame — no trigger

    def test_rearm_triggers_after_duration(self):
        ag = _make_agent()
        ag._rearm_minutes = 1  # 60 seconds
        ag._enemy_points = [(100, 100)]
        ag._should_alarm_enemy()  # register sighting

        # Fast-forward rearm_at to just past "now"
        key = (100 // 20, 100 // 20)
        old = ag._seen_enemies[key]
        ag._seen_enemies[key] = _EnemySighting(
            first_seen=old.first_seen,
            last_alarm=old.last_alarm,
            rearm_at=time.time() - 1,  # already expired
        )
        self.assertTrue(ag._should_alarm_enemy())

    def test_rearm_disabled_when_zero(self):
        ag = _make_agent()
        ag._rearm_minutes = 0
        ag._enemy_points = [(100, 100)]
        ag._should_alarm_enemy()

        key = (100 // 20, 100 // 20)
        sighting = ag._seen_enemies[key]
        self.assertEqual(sighting.rearm_at, 0)

    def test_rearm_at_advances_after_firing(self):
        ag = _make_agent()
        ag._rearm_minutes = 1
        ag._enemy_points = [(100, 100)]
        ag._should_alarm_enemy()  # register

        key = (100 // 20, 100 // 20)
        old = ag._seen_enemies[key]
        # Force rearm_at into the past so it fires
        ag._seen_enemies[key] = _EnemySighting(
            first_seen=old.first_seen,
            last_alarm=old.last_alarm,
            rearm_at=time.time() - 1,
        )
        ag._should_alarm_enemy()  # fires and should schedule next
        new_rearm = ag._seen_enemies[key].rearm_at
        self.assertGreater(new_rearm, time.time())

    def test_enemy_leaving_and_returning_triggers(self):
        ag = _make_agent()
        ag._enemy_points = [(100, 100)]
        ag._should_alarm_enemy()  # first sight

        # Enemy leaves — empty points → seen_enemies cleared
        ag._enemy_points = []
        ag._should_alarm_enemy()  # clears the set for key (-1,-1)

        # Enemy returns
        ag._enemy_points = [(100, 100)]
        self.assertTrue(ag._should_alarm_enemy())


if __name__ == "__main__":
    unittest.main()
