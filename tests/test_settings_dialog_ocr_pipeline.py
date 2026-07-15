"""Tests for the Settings dialog's OCR-test -> intel-check wiring (#201, #204).

Uses the offscreen Qt platform so no display is needed in CI.
"""

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_dialog(parent=None):
    """Return a SettingsDialog instance backed by a temp-file store."""
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    QApplication.instance() or QApplication([])  # ensure app exists

    from evealert.settings.store import reset_settings_store  # noqa: PLC0415
    from evealert.ui.settings_dialog import SettingsDialog  # noqa: PLC0415

    td = tempfile.mkdtemp()
    store = reset_settings_store(td)
    return SettingsDialog(parent=parent, store=store)


class _FakeMainWindow:
    """Stand-in for MainWindow exposing just what SettingsDialog needs.

    Not a real QWidget on purpose: SettingsDialog reads self.parent().alert
    via a plain attribute lookup, so a lightweight object is enough and
    avoids needing a full Qt parent/child relationship in these tests.
    """

    def __init__(self, alert):
        self.alert = alert


class OcrTestStatusMessageTests(unittest.TestCase):
    """#204: the OCR test result must warn when 'Enable OCR Name Detection'
    is not currently checked, since a successful test doesn't mean OCR will
    run during real alarms."""

    def setUp(self):
        self.dlg = _make_dialog()

    def test_warns_when_ocr_enabled_checkbox_unchecked(self):
        self.dlg._controls["ocr.enabled"].setChecked(False)
        with mock.patch.object(self.dlg, "_run_intel_check_on_names"):
            self.dlg._on_ocr_test_done({"ok": True, "names": ["Bad Guy"]})
        text = self.dlg._ocr_test_status.text()
        self.assertIn("OCR will NOT run during real alarms", text)

    def test_no_warning_when_ocr_enabled_checkbox_checked(self):
        self.dlg._controls["ocr.enabled"].setChecked(True)
        with mock.patch.object(self.dlg, "_run_intel_check_on_names"):
            self.dlg._on_ocr_test_done({"ok": True, "names": ["Bad Guy"]})
        text = self.dlg._ocr_test_status.text()
        self.assertNotIn("OCR will NOT run", text)


class OcrTestTriggersIntelCheckTests(unittest.TestCase):
    """#201: a successful OCR test must run the real intel pipeline on the
    names it found, instead of discarding them."""

    def setUp(self):
        self.dlg = _make_dialog()

    def test_success_with_names_triggers_intel_check(self):
        with mock.patch.object(self.dlg, "_run_intel_check_on_names") as mock_run:
            self.dlg._on_ocr_test_done({"ok": True, "names": ["Bad Guy", "Other"]})
        mock_run.assert_called_once_with(["Bad Guy", "Other"])

    def test_no_names_does_not_trigger_intel_check(self):
        with mock.patch.object(self.dlg, "_run_intel_check_on_names") as mock_run:
            self.dlg._on_ocr_test_done({"ok": True, "names": []})
        mock_run.assert_not_called()


class RunIntelCheckOnNamesTests(unittest.TestCase):
    """#201: verifies the worker actually calls AlertAgent.run_intel_check
    on the app's real AlertAgent instance, via parent().alert."""

    def test_calls_run_intel_check_on_parent_alert(self):
        fake_alert = mock.MagicMock()
        fake_alert.load_settings = mock.MagicMock()
        fake_alert.run_intel_check = mock.AsyncMock()
        fake_alert._ui = mock.MagicMock()
        fake_alert.main = mock.MagicMock()

        parent = _FakeMainWindow(alert=fake_alert)
        dlg = _make_dialog(parent=None)  # parent must be a real QWidget or None
        # Patch self.parent() to return our fake main window instead of using
        # a real Qt parent/child relationship (SettingsDialog only ever calls
        # .parent() to reach `.alert`, never any QWidget-specific API on it).
        with mock.patch.object(dlg, "parent", return_value=parent), \
             mock.patch("threading.Thread") as mock_thread_cls:
            # Make Thread.start() run the target synchronously so the test
            # doesn't need to poll/sleep waiting for a real background thread.
            def _fake_thread(target=None, **kwargs):
                thread = mock.MagicMock()
                thread.start.side_effect = target
                return thread

            mock_thread_cls.side_effect = _fake_thread
            dlg._run_intel_check_on_names(["Bad Guy"])

        fake_alert.load_settings.assert_called_once()
        fake_alert.run_intel_check.assert_awaited_once_with(["Bad Guy"])

    def test_noop_when_parent_has_no_alert(self):
        dlg = _make_dialog(parent=None)
        with mock.patch.object(dlg, "parent", return_value=object()), \
             mock.patch("threading.Thread") as mock_thread_cls:
            dlg._run_intel_check_on_names(["Bad Guy"])
        mock_thread_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
