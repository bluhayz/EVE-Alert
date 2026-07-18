"""Tests for the #172 Route Check UI in SettingsDialog.

Uses the offscreen Qt platform so no display is needed in CI.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_dialog(store, agent=None):
    """Returns (dialog, parent_widget) -- the caller must keep a reference
    to parent_widget alive for as long as the dialog is used, or PySide's
    GC can tear down the underlying C++ QWidget out from under it."""
    from PySide6.QtWidgets import QApplication, QWidget  # noqa: PLC0415

    QApplication.instance() or QApplication([])

    from evealert.ui.settings_dialog import SettingsDialog  # noqa: PLC0415

    parent = None
    if agent is not None:
        # Stand-in for MainWindow -- a real QWidget (Qt requires an actual
        # QWidget or None as a dialog parent) with an `.alert` attribute.
        parent = QWidget()
        parent.alert = agent
    return SettingsDialog(parent, store), parent


class RouteCheckUiTestCase(unittest.TestCase):
    def setUp(self):
        from evealert.settings.store import SettingsStore  # noqa: PLC0415

        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = Path(self.temp_dir) / "settings.json"
        with open(self.settings_path, "w") as f:
            json.dump({}, f)
        self.store = SettingsStore(self.settings_path)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)


class NoAgentTests(RouteCheckUiTestCase):
    def setUp(self):
        super().setUp()
        self.dialog, self._parent = _make_dialog(self.store)  # parent=None -> no agent

    def tearDown(self):
        self.dialog.deleteLater()
        super().tearDown()

    def test_check_button_disabled_without_an_agent(self):
        self.assertFalse(self.dialog._route_check_btn.isEnabled())
        self.assertIn("not available", self.dialog._route_check_status.text())

    def test_check_route_without_agent_shows_status_and_starts_nothing(self):
        self.dialog._route_origin_entry.setText("Jita")
        self.dialog._route_dest_entry.setText("Amarr")

        self.dialog._check_route()

        self.assertIn("not available", self.dialog._route_check_status.text())
        self.assertIsNone(self.dialog._route_check_thread)


class WithAgentTests(RouteCheckUiTestCase):
    def setUp(self):
        super().setUp()
        self.mock_agent = MagicMock()
        self.dialog, self._parent = _make_dialog(self.store, agent=self.mock_agent)

    def tearDown(self):
        self.dialog.deleteLater()
        super().tearDown()

    def test_check_button_enabled_with_an_agent(self):
        self.assertTrue(self.dialog._route_check_btn.isEnabled())

    def test_empty_fields_show_validation_message_and_start_no_thread(self):
        self.dialog._route_origin_entry.setText("")
        self.dialog._route_dest_entry.setText("Amarr")

        self.dialog._check_route()

        self.assertIn("Enter both", self.dialog._route_check_status.text())
        self.assertIsNone(self.dialog._route_check_thread)

    def test_valid_fields_start_a_thread_and_disable_the_button(self):
        self.dialog._route_origin_entry.setText("Jita")
        self.dialog._route_dest_entry.setText("Amarr")

        with patch("evealert.ui.settings_dialog._RouteCheckThread") as mock_cls:
            mock_thread = MagicMock()
            mock_cls.return_value = mock_thread

            self.dialog._check_route()

            mock_cls.assert_called_once_with(self.mock_agent, "Jita", "Amarr")
            mock_thread.start.assert_called_once()
        self.assertFalse(self.dialog._route_check_btn.isEnabled())
        self.assertIn("Checking", self.dialog._route_check_status.text())

    def test_on_done_with_no_error_reenables_button_and_shows_success(self):
        self.dialog._route_check_btn.setEnabled(False)

        self.dialog._on_route_check_done("")

        self.assertTrue(self.dialog._route_check_btn.isEnabled())
        self.assertIn("Done", self.dialog._route_check_status.text())

    def test_on_done_with_error_reenables_button_and_shows_failure(self):
        self.dialog._route_check_btn.setEnabled(False)

        self.dialog._on_route_check_done("no path found")

        self.assertTrue(self.dialog._route_check_btn.isEnabled())
        self.assertIn("Failed: no path found", self.dialog._route_check_status.text())


if __name__ == "__main__":
    unittest.main()
