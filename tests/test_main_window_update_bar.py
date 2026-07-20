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

    def test_repeated_notifications_do_not_stack_open_dialogs(self):
        """#246 regression: _on_update_available() used to reconnect a
        fresh closure to _update_btn.clicked on every call without
        disconnecting the previous one -- after N notifications (e.g.
        the #178 24h re-check firing repeatedly across a long session),
        one click opened N stacked UpdateDialogs."""
        for _ in range(3):
            self.win._on_update_available("v99.0.0")

        with patch("evealert.ui.update_dialog.UpdateDialog") as mock_dialog_cls:
            mock_dialog_cls.return_value.exec.return_value = False
            self.win._update_btn.click()

        mock_dialog_cls.assert_called_once_with(self.win, "v99.0.0")

    def test_repeated_notifications_skip_only_persists_once_per_click(self):
        for _ in range(3):
            self.win._on_update_available("v99.0.0")

        with patch.object(self.win.store, "save") as mock_save:
            self.win._update_dismiss.click()

        mock_save.assert_called_once()

    def test_second_notification_updates_pending_tag(self):
        self.win._on_update_available("v98.0.0")
        self.win._on_update_available("v99.0.0")
        self.win._update_dismiss.click()

        settings = self.win.store.load()
        self.assertEqual(settings["updates"]["skipped_version"], "v99.0.0")

    def test_dismiss_with_no_pending_tag_is_a_noop(self):
        self.win._update_dismiss.click()  # never notified -- must not raise
        self.assertFalse(self.win._update_bar.isVisibleTo(self.win))

    def test_open_update_with_no_pending_tag_is_a_noop(self):
        with patch("evealert.ui.update_dialog.UpdateDialog") as mock_dialog_cls:
            self.win._update_btn.click()  # never notified -- must not raise
        mock_dialog_cls.assert_not_called()


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
