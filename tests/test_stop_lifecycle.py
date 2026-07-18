"""Regression tests for asyncio task lifecycle in stop() (issue #102)."""

import unittest
from unittest import mock

from evealert.manager.alertmanager import AlertAgent


class _StopAgent(AlertAgent):
    """AlertAgent with __init__ bypassed — only the state stop()/_shutdown_loop
    touch is initialised."""

    def __init__(self):  # pylint: disable=super-init-not-called
        self.running = True
        self.loop = mock.MagicMock()
        self.loop.is_running.return_value = True
        self.wincap = mock.MagicMock()
        self.main = mock.MagicMock()
        self._bridge = mock.MagicMock()
        self.statistics = mock.MagicMock()
        self.alert_vision = mock.MagicMock()
        self.alert_vision_faction = mock.MagicMock()
        self.currently_playing_sounds = {}
        self.alarm_trigger_counts = {}
        self.cooldown_timers = {}
        # Task handles
        self.vision_t = mock.MagicMock()
        self.vision_faction_t = mock.MagicMock()
        self.alert_t = mock.MagicMock()
        self._thera_task = mock.MagicMock()
        self._sov_task = mock.MagicMock()
        self._esi_standings_task = mock.MagicMock()
        self._gatecamp_task = mock.MagicMock()
        self._cache_maintenance_task_handle = mock.MagicMock()
        for t in (
            self.vision_t,
            self.vision_faction_t,
            self.alert_t,
            self._thera_task,
            self._sov_task,
            self._esi_standings_task,
            self._gatecamp_task,
            self._cache_maintenance_task_handle,
        ):
            t.done.return_value = False
        # Class-based monitors
        self._web_server = mock.MagicMock()
        self._neighbor_monitor = mock.MagicMock()
        self._dscan_watcher = mock.MagicMock()
        self._killmail_monitor = mock.MagicMock()
        self._intel_watchers = [mock.MagicMock()]
        self._r2z2_consumer = None
        self._extra_clients = []
        self._extra_client_tasks = []
        self._ui = mock.MagicMock()


class StopLifecycleTests(unittest.TestCase):
    def setUp(self):
        # Avoid the plugin/stats side effects reaching real modules.
        self.agent = _StopAgent()
        self._patchers = [
            mock.patch("evealert.manager.alertmanager.save_lifetime_stats"),
            mock.patch("evealert.manager.alertmanager.save_session_report"),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_running_flag_cleared(self):
        self.agent.stop()
        self.assertFalse(self.agent.running)

    def test_class_monitors_stopped(self):
        web = self.agent._web_server
        neighbor = self.agent._neighbor_monitor
        intel_watcher = self.agent._intel_watchers[0]
        self.agent.stop()
        web.stop.assert_called_once()
        neighbor.stop.assert_called_once()
        intel_watcher.stop.assert_called_once()

    def test_shutdown_scheduled_on_loop_thread(self):
        self.agent.stop()
        # Loop teardown must be deferred to the loop's own thread
        self.agent.loop.call_soon_threadsafe.assert_called_once_with(
            self.agent._shutdown_loop
        )
        # stop() must NOT tear the loop down directly from the caller thread
        self.agent.loop.stop.assert_not_called()

    def test_shutdown_loop_cancels_all_tasks_then_stops(self):
        tasks = [
            self.agent.vision_t,
            self.agent.vision_faction_t,
            self.agent.alert_t,
            self.agent._thera_task,
            self.agent._sov_task,
            self.agent._esi_standings_task,
            self.agent._gatecamp_task,
            self.agent._cache_maintenance_task_handle,
        ]
        self.agent._shutdown_loop()
        for t in tasks:
            t.cancel.assert_called_once()
        self.agent.loop.stop.assert_called_once()

    def test_shutdown_loop_skips_done_tasks(self):
        self.agent._thera_task.done.return_value = True
        self.agent._shutdown_loop()
        self.agent._thera_task.cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
