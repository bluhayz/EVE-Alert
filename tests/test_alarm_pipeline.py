"""Tests for the alarm pipeline end-to-end: _ui() dispatch, statistics, OCR wiring.

Covers the regressions fixed in v6.3.4:
  - _ui() bound-method identity bug — messages were silently dropped
  - statistics_window AlarmStatistics attribute mismatch
"""

import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from evealert.manager.alertmanager import AlertAgent
from evealert.settings.store import reset_settings_store
from evealert.statistics import AlarmStatistics


def _make_agent(tmp_dir: Path) -> tuple[AlertAgent, list]:
    """Return (agent, log_calls) where log_calls accumulates all _ui calls."""
    settings_path = tmp_dir / "settings.json"
    settings_path.write_text("{}")
    reset_settings_store(settings_path)
    os.environ["EVEALERT_STATS_PATH"] = str(tmp_dir / "statistics.json")
    os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(tmp_dir / "pilot_history.db")

    log_calls: list[tuple[str, str]] = []

    mock_main = MagicMock()
    mock_main.write_message = MagicMock(
        side_effect=lambda text, color="normal": log_calls.append((text, color))
    )
    mock_main.update_alert_button = MagicMock()
    mock_main.update_faction_button = MagicMock()
    mock_main.after = MagicMock()

    with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
        agent = AlertAgent(mock_main)
    return agent, log_calls


class UiDispatchTests(unittest.TestCase):
    """_ui() must call write_message regardless of Python's bound-method identity."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_message_reaches_main(self):
        """_ui(main.write_message, text, color) must invoke write_message."""
        agent, log_calls = _make_agent(self.tmp)
        log_calls.clear()  # flush any startup messages from load_settings()
        agent._ui(agent.main.write_message, "hello", "red")
        self.assertIn(("hello", "red"), log_calls)

    def test_multiple_calls_all_delivered(self):
        """Every _ui call must reach write_message, not just the first."""
        agent, log_calls = _make_agent(self.tmp)
        log_calls.clear()
        for i in range(5):
            agent._ui(agent.main.write_message, f"msg{i}", "cyan")
        texts = [t for t, _ in log_calls]
        for i in range(5):
            self.assertIn(f"msg{i}", texts)

    def test_update_button_triggers_method(self):
        """_ui(main.update_alert_button) must call the method."""
        agent, _ = _make_agent(self.tmp)
        agent._ui(agent.main.update_alert_button)
        agent.main.update_alert_button.assert_called()


class AlarmDetectionPipelineTests(unittest.TestCase):
    """alarm_detection() must log AND update statistics — both must succeed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_alarm_detection(self, agent: AlertAgent, text: str, alarm_type: str) -> None:
        """Run alarm_detection synchronously on a fresh event loop."""
        async def _task():
            with patch.object(agent, "play_sound", return_value=None):
                with patch.object(agent, "send_webhook_message", return_value=None):
                    await agent.alarm_detection(text, sound=None, alarm_type=alarm_type)

        asyncio.run(_task())

    def test_alarm_logs_message(self):
        agent, log_calls = _make_agent(self.tmp)
        self._run_alarm_detection(agent, "Enemy Appears!", "Enemy")
        texts = [t for t, _ in log_calls]
        self.assertIn("Enemy Appears!", texts)

    def test_alarm_updates_statistics(self):
        agent, _ = _make_agent(self.tmp)
        before = agent.statistics.session_alarms
        self._run_alarm_detection(agent, "Enemy Appears!", "Enemy")
        self.assertEqual(agent.statistics.session_alarms, before + 1)
        self.assertEqual(agent.statistics.total_alarms, before + 1)

    def test_alarm_type_tracked_by_type(self):
        agent, _ = _make_agent(self.tmp)
        self._run_alarm_detection(agent, "Enemy Appears!", "Enemy")
        self.assertEqual(agent.statistics.session_by_type.get("Enemy", 0), 1)

    def test_alarm_message_color_is_red(self):
        agent, log_calls = _make_agent(self.tmp)
        self._run_alarm_detection(agent, "Enemy Appears!", "Enemy")
        colors = [c for t, c in log_calls if t == "Enemy Appears!"]
        self.assertIn("red", colors)


class AlarmStatisticsAttributeTests(unittest.TestCase):
    """AlarmStatistics must have the attributes the statistics window reads."""

    def test_required_attributes_exist(self):
        s = AlarmStatistics()
        # These are the names _refresh_live() now uses (fixed in v6.3.4)
        _ = s.total_alarms
        _ = s.session_alarms
        _ = s.session_start_time
        _ = s.total_by_type
        _ = s.session_by_type
        _ = s.alarm_history

    def test_add_alarm_increments_correctly(self):
        s = AlarmStatistics()
        s.add_alarm("Enemy")
        self.assertEqual(s.total_alarms, 1)
        self.assertEqual(s.session_alarms, 1)
        self.assertEqual(s.total_by_type["Enemy"], 1)
        self.assertEqual(s.session_by_type["Enemy"], 1)
        self.assertEqual(len(s.alarm_history), 1)
        self.assertEqual(s.alarm_history[-1].alarm_type, "Enemy")


if __name__ == "__main__":
    unittest.main()
