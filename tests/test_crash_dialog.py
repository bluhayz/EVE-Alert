"""Tests for evealert.ui.crash_dialog (#180, v8.0).

Uses the offscreen Qt platform so no display is needed in CI.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qapp():
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    return QApplication.instance() or QApplication([])


def _make_bundle(tmp_dir: Path, *, traceback_text="ValueError: boom\n", context=None) -> Path:
    import json

    bundle_dir = tmp_dir / "20260101_000000"
    bundle_dir.mkdir()
    (bundle_dir / "traceback.txt").write_text(traceback_text, encoding="utf-8")
    (bundle_dir / "context.json").write_text(
        json.dumps(context or {"app": {"version": "8.0.0"}}), encoding="utf-8"
    )
    return bundle_dir


class CrashDialogTestCase(unittest.TestCase):
    def setUp(self):
        _qapp()
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class CrashDialogPopulateTests(CrashDialogTestCase):
    def test_loads_traceback_and_context(self):
        from evealert.ui.crash_dialog import CrashDialog

        bundle_dir = _make_bundle(self.temp_dir, traceback_text="RuntimeError: kaboom\n")
        dlg = CrashDialog(None, bundle_dir)
        text = dlg._report_view.toPlainText()
        self.assertIn("RuntimeError: kaboom", text)
        self.assertIn("8.0.0", text)
        dlg.deleteLater()

    def test_missing_files_show_placeholder_not_crash(self):
        from evealert.ui.crash_dialog import CrashDialog

        bundle_dir = self.temp_dir / "empty"
        bundle_dir.mkdir()
        dlg = CrashDialog(None, bundle_dir)  # must not raise
        self.assertIn("not available", dlg._report_view.toPlainText())
        dlg.deleteLater()


class CrashDialogGithubUrlTests(CrashDialogTestCase):
    def test_url_contains_traceback_and_version(self):
        from evealert.ui.crash_dialog import CrashDialog

        bundle_dir = _make_bundle(self.temp_dir, traceback_text="ValueError: boom\n")
        dlg = CrashDialog(None, bundle_dir)
        url = dlg.github_url()
        self.assertTrue(url.startswith("https://github.com/bluhayz/EVE-Alert/issues/new?"))
        self.assertIn("ValueError", url)
        dlg.deleteLater()

    def test_title_summarizes_exception(self):
        from evealert.ui.crash_dialog import CrashDialog

        bundle_dir = _make_bundle(
            self.temp_dir,
            traceback_text="Traceback (most recent call last):\n  File x\nValueError: boom\n",
        )
        dlg = CrashDialog(None, bundle_dir)
        self.assertIn("ValueError%3A+boom", dlg.github_url())
        dlg.deleteLater()

    def test_huge_traceback_still_produces_url_under_budget(self):
        """#252 regression: capping raw traceback CHARACTERS (the old
        _MAX_BODY_CHARS = 6000) still let percent-encoding inflate the
        URL past GitHub's ~8KB new-issue limit -- the check must be on
        the ENCODED length."""
        from evealert.ui.crash_dialog import CrashDialog, _MAX_URL_BYTES

        huge_tb = "\n".join(
            f'  File "C:\\Users\\test\\some\\deep\\path\\module_{i}.py", line {i}, in func_{i}'
            for i in range(500)
        ) + "\nValueError: boom"
        bundle_dir = _make_bundle(self.temp_dir, traceback_text=huge_tb)
        dlg = CrashDialog(None, bundle_dir)
        url = dlg.github_url()
        self.assertLessEqual(len(url.encode("utf-8")), _MAX_URL_BYTES)
        dlg.deleteLater()

    def test_truncated_body_notes_full_traceback_location(self):
        from evealert.ui.crash_dialog import CrashDialog

        huge_tb = "\n".join(f"line {i} of a very long traceback body" for i in range(2000))
        bundle_dir = _make_bundle(self.temp_dir, traceback_text=huge_tb)
        dlg = CrashDialog(None, bundle_dir)
        url = dlg.github_url()
        self.assertIn("truncated", url.lower())
        dlg.deleteLater()

    def test_small_traceback_not_marked_truncated(self):
        from evealert.ui.crash_dialog import CrashDialog

        bundle_dir = _make_bundle(self.temp_dir, traceback_text="ValueError: boom\n")
        dlg = CrashDialog(None, bundle_dir)
        url = dlg.github_url()
        self.assertNotIn("truncated", url.lower())
        dlg.deleteLater()


class MaybeShowPendingCrashTests(CrashDialogTestCase):
    def test_no_pending_crash_does_not_open_dialog(self):
        from evealert.ui.crash_dialog import maybe_show_pending_crash

        with patch(
            "evealert.tools.crash_reporter.find_unacknowledged_crash", return_value=None
        ), patch("evealert.ui.crash_dialog.CrashDialog") as mock_dialog:
            maybe_show_pending_crash(None)
        mock_dialog.assert_not_called()

    def test_pending_crash_opens_and_acknowledges(self):
        from evealert.ui.crash_dialog import maybe_show_pending_crash

        bundle_dir = _make_bundle(self.temp_dir)
        with patch(
            "evealert.tools.crash_reporter.find_unacknowledged_crash",
            return_value=bundle_dir,
        ), patch(
            "evealert.tools.crash_reporter.mark_acknowledged"
        ) as mock_ack, patch(
            "evealert.ui.crash_dialog.CrashDialog"
        ) as mock_dialog_cls:
            mock_dialog_cls.return_value.exec.return_value = None
            maybe_show_pending_crash(None)

        mock_dialog_cls.assert_called_once_with(None, bundle_dir)
        mock_ack.assert_called_once_with(bundle_dir)


if __name__ == "__main__":
    unittest.main()
