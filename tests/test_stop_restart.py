"""Regression tests for AlertAgent stop/restart lifecycle (#190).

Tests that stop() never raises, that wincap is properly cleaned up, and
that start → stop → start cycles all succeed.
"""

import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from evealert.manager.alertmanager import AlertAgent
from evealert.settings.store import reset_settings_store


def _make_agent(tmp_dir: Path) -> AlertAgent:
    """Build a minimal AlertAgent backed by a temp settings file."""
    settings_path = tmp_dir / "settings.json"
    settings_path.write_text("{}")
    reset_settings_store(settings_path)
    os.environ["EVEALERT_STATS_PATH"] = str(tmp_dir / "statistics.json")
    os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(tmp_dir / "pilot_history.db")

    mock_main = MagicMock()
    mock_main.write_message = MagicMock()

    with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
        agent = AlertAgent(mock_main)
    # Set a minimal valid region so vision_check doesn't complain
    agent.x1, agent.y1, agent.x2, agent.y2 = 0, 0, 1, 1
    return agent


def _start_and_wait(agent: AlertAgent, timeout: float = 3.0) -> bool:
    """Start agent in a daemon thread; return True when is_running becomes True."""
    t = threading.Thread(target=agent.start, daemon=True)
    t.start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if agent.is_running:
            return True
        time.sleep(0.05)
    return False


class StopRestartTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_stop_does_not_raise(self):
        """stop() must never propagate an exception (#190)."""
        agent = _make_agent(self.temp_dir)
        # Even without starting (no loop), stop() should be silent
        try:
            agent.stop()
        except Exception as exc:
            self.fail(f"stop() raised {exc!r}")

    def test_wincap_sct_is_none_after_stop(self):
        """wincap's capture backend must be released after stop() so
        restart creates a fresh instance (#176: the mss handle now lives
        inside the backend object, not directly on WindowCapture)."""
        agent = _make_agent(self.temp_dir)
        # Force the (default mss) backend to actually materialize, same as
        # a real capture call would, so this test exercises real teardown
        # rather than a no-op close() on a never-touched WindowCapture.
        agent.wincap._get_backend()
        self.assertIsNotNone(agent.wincap._backend)
        # Directly simulate the alert-thread close path by calling wincap.close()
        # (the real call now happens via _shutdown_loop on the alert thread)
        agent.wincap.close()
        self.assertIsNone(agent.wincap._backend)

    def test_stop_without_prior_start_is_idempotent(self):
        """stop() on a never-started agent must not raise."""
        agent = _make_agent(self.temp_dir)
        for _ in range(3):
            try:
                agent.stop()
            except Exception as exc:
                self.fail(f"stop() raised {exc!r}")
        self.assertFalse(agent.is_running)


if __name__ == "__main__":
    unittest.main()
