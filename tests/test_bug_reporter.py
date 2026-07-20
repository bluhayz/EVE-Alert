"""Tests for evealert.ui.bug_reporter (#252 URL-length safety net).

Uses the offscreen Qt platform so no display is needed in CI.
"""

import os
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qapp():
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    return QApplication.instance() or QApplication([])


def _make_dialog(log_text: str = "short log"):
    from evealert.ui.bug_reporter import BugReporterDialog  # noqa: PLC0415

    log_pane = MagicMock()
    log_pane.get_log_text.return_value = log_text
    return BugReporterDialog(None, log_pane)


class BugReporterDialogTestCase(unittest.TestCase):
    def setUp(self):
        _qapp()


class GithubUrlLengthTests(BugReporterDialogTestCase):
    def test_short_body_produces_url_under_budget(self):
        from evealert.ui.bug_reporter import _MAX_URL_BYTES

        dlg = _make_dialog("a short log line")
        url = dlg.github_url()
        self.assertLessEqual(len(url.encode("utf-8")), _MAX_URL_BYTES)
        dlg.deleteLater()

    def test_huge_log_still_produces_url_under_budget(self):
        """#252 regression: percent-encoding inflates size for the
        brackets/colons/spaces log timestamps are full of -- a body just
        under _MAX_LOG_CHARS could still overflow GitHub's URL limit
        after encoding."""
        from evealert.ui.bug_reporter import _MAX_URL_BYTES

        huge_log = "\n".join(f"[12:00:{i:02d}] some log line with content {i}" for i in range(500))
        dlg = _make_dialog(huge_log)
        url = dlg.github_url()
        self.assertLessEqual(len(url.encode("utf-8")), _MAX_URL_BYTES)
        dlg.deleteLater()

    def test_huge_user_edited_body_still_produces_url_under_budget(self):
        from evealert.ui.bug_reporter import _MAX_URL_BYTES

        dlg = _make_dialog()
        dlg._body_edit.setPlainText("x" * 50_000)
        url = dlg.github_url()
        self.assertLessEqual(len(url.encode("utf-8")), _MAX_URL_BYTES)
        dlg.deleteLater()

    def test_title_always_preserved_even_when_body_trimmed(self):
        dlg = _make_dialog()
        dlg._title_edit.setPlainText("Bug: something specific broke")
        dlg._body_edit.setPlainText("x" * 50_000)
        url = dlg.github_url()
        self.assertIn("something", url)
        dlg.deleteLater()

    def test_normal_body_content_unaffected(self):
        dlg = _make_dialog("normal log content")
        url = dlg.github_url()
        self.assertIn("github.com", url)
        self.assertIn("title=", url)
        self.assertIn("body=", url)
        dlg.deleteLater()


if __name__ == "__main__":
    unittest.main()
