"""Tests for evealert.statistics — AlarmStatistics and AlarmEvent."""

import time
import unittest


class TestAlarmEvent(unittest.TestCase):
    def _make_event(self, alarm_type="Enemy"):
        from evealert.statistics import AlarmEvent

        return AlarmEvent(alarm_type=alarm_type, timestamp=time.time())

    def test_formatted_time_is_string(self):
        ev = self._make_event()
        self.assertIsInstance(ev.formatted_time(), str)
        self.assertGreater(len(ev.formatted_time()), 0)

    def test_alarm_type_stored(self):
        ev = self._make_event("Faction")
        self.assertEqual(ev.alarm_type, "Faction")


class TestAlarmStatistics(unittest.TestCase):
    def _make_stats(self):
        from evealert.statistics import AlarmStatistics

        return AlarmStatistics()

    def test_initial_state(self):
        s = self._make_stats()
        self.assertEqual(s.total_alarms, 0)
        self.assertEqual(s.session_alarms, 0)
        self.assertEqual(s.total_by_type["Enemy"], 0)
        self.assertEqual(s.total_by_type["Faction"], 0)
        self.assertEqual(s.session_by_type["Enemy"], 0)
        self.assertEqual(s.session_by_type["Faction"], 0)
        self.assertEqual(len(s.alarm_history), 0)

    def test_add_alarm_enemy(self):
        s = self._make_stats()
        s.add_alarm("Enemy")
        self.assertEqual(s.total_alarms, 1)
        self.assertEqual(s.session_alarms, 1)
        self.assertEqual(s.total_by_type["Enemy"], 1)
        self.assertEqual(s.session_by_type["Enemy"], 1)
        self.assertEqual(len(s.alarm_history), 1)

    def test_add_alarm_faction(self):
        s = self._make_stats()
        s.add_alarm("Faction")
        self.assertEqual(s.total_by_type["Faction"], 1)
        self.assertEqual(s.session_by_type["Faction"], 1)

    def test_add_multiple_alarms_accumulate(self):
        s = self._make_stats()
        for _ in range(5):
            s.add_alarm("Enemy")
        for _ in range(3):
            s.add_alarm("Faction")
        self.assertEqual(s.total_alarms, 8)
        self.assertEqual(s.total_by_type["Enemy"], 5)
        self.assertEqual(s.total_by_type["Faction"], 3)

    def test_load_lifetime_restores_totals(self):
        s = self._make_stats()
        s.add_alarm("Enemy")  # session count = 1
        s.load_lifetime(
            {"total_alarms": 100, "total_by_type": {"Enemy": 80, "Faction": 20}}
        )
        self.assertEqual(s.total_alarms, 100)
        self.assertEqual(s.total_by_type["Enemy"], 80)
        # Session counters must stay unchanged
        self.assertEqual(s.session_alarms, 1)
        self.assertEqual(s.session_by_type["Enemy"], 1)

    def test_load_lifetime_empty_dict_noop(self):
        s = self._make_stats()
        s.load_lifetime({})
        self.assertEqual(s.total_alarms, 0)

    def test_reset_session_zeroes_session_fields(self):
        s = self._make_stats()
        s.add_alarm("Enemy")
        s.add_alarm("Faction")
        s.reset_session()
        self.assertEqual(s.session_alarms, 0)
        self.assertEqual(s.session_by_type["Enemy"], 0)
        self.assertEqual(s.session_by_type["Faction"], 0)
        # Lifetime totals must be preserved
        self.assertEqual(s.total_alarms, 2)

    def test_clear_history_empties_deque(self):
        s = self._make_stats()
        s.add_alarm("Enemy")
        s.clear_history()
        self.assertEqual(len(s.alarm_history), 0)

    def test_get_recent_history_respects_limit(self):
        s = self._make_stats()
        for _ in range(20):
            s.add_alarm("Enemy")
        recent = s.get_recent_history(5)
        self.assertEqual(len(recent), 5)

    def test_get_session_duration_is_string(self):
        s = self._make_stats()
        result = s.get_session_duration()
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_history_deque_maxlen(self):
        s = self._make_stats()
        # Overflow the deque (maxlen=50) to ensure oldest are dropped
        for i in range(60):
            s.add_alarm("Enemy")
        self.assertEqual(len(s.alarm_history), 50)
        self.assertEqual(s.total_alarms, 60)


if __name__ == "__main__":
    unittest.main()
