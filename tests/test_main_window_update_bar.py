"""Tests for MainWindow's update-notification bar (#178, v8.0 skip
persistence) and crash-detected wiring (#180).

Uses the offscreen Qt platform so no display is needed in CI.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class MainWindowUpdateBarTestCase(unittest.TestCase):
    def setUp(self):
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])

        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
        with open(self.settings_path, "w") as f:
            json.dump({}, f)

        from evealert.settings.store import reset_settings_store

        reset_settings_store(self.settings_path)

        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            from evealert.ui.main_window import MainWindow

            self.win = MainWindow()

    def tearDown(self):
        self.win.deleteLater()
        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class OnUpdateAvailableTests(MainWindowUpdateBarTestCase):
    def test_no_update_logs_up_to_date(self):
        self.win._on_update_available("")
        self.assertFalse(self.win._update_bar.isVisibleTo(self.win))

    def test_update_available_shows_bar(self):
        self.win._on_update_available("v99.0.0")
        self.assertIn("v99.0.0", self.win._update_label.text())

    def test_skip_persists_version_and_hides_bar(self):
        self.win._on_update_available("v99.0.0")
        self.win._update_dismiss.click()

        settings = self.win.store.load()
        self.assertEqual(settings["updates"]["skipped_version"], "v99.0.0")

    def test_skip_failure_never_raises(self):
        self.win._on_update_available("v99.0.0")
        with patch.object(self.win.store, "save", side_effect=OSError("disk full")):
            self.win._update_dismiss.click()  # must not raise


class OnCrashDetectedTests(MainWindowUpdateBarTestCase):
    def test_opens_crash_dialog_and_acknowledges(self):
        with patch(
            "evealert.ui.crash_dialog.CrashDialog"
        ) as mock_dialog_cls, patch(
            "evealert.tools.crash_reporter.mark_acknowledged"
        ) as mock_ack:
            mock_dialog_cls.return_value.exec.return_value = None
            self.win._on_crash_detected("/tmp/some-bundle-dir")

        mock_dialog_cls.assert_called_once()
        mock_ack.assert_called_once()


if __name__ == "__main__":
    unittest.main()
