"""Bug reporter dialog for EVE Alert.

Collects the current log pane contents, system information, and the installed
version, then lets the user review the information before opening a pre-filled
GitHub issue in their browser.

No GitHub token is required — the report URL uses GitHub's new-issue query
parameters (?title=...&body=...) which pre-populate the issue form.
"""

from __future__ import annotations

import platform
import sys
import textwrap

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from evealert import __version__

_GITHUB_NEW_ISSUE = "https://github.com/bluhayz/EVE-Alert/issues/new"
_MAX_LOG_CHARS = 3_000  # GitHub URL length limit is ~8 KB; keep body manageable


class BugReporterDialog(QDialog):
    """Collect diagnostics and preview the GitHub issue before opening."""

    def __init__(self, parent, log_pane) -> None:
        super().__init__(parent)
        self.setWindowTitle("EVE Alert — Report a Bug")
        self.setMinimumWidth(600)
        self.setMinimumHeight(480)
        self._log_pane = log_pane
        self._body: str = ""
        self._build_ui()
        self._populate()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        info = QLabel(
            "Review the information below, then click <b>Open GitHub Issue</b> "
            "to pre-fill a bug report in your browser.<br>"
            "You can edit the title and body on GitHub before submitting."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self._title_edit = QPlainTextEdit()
        self._title_edit.setPlaceholderText("Short one-line summary of the bug…")
        self._title_edit.setMaximumHeight(50)
        layout.addWidget(QLabel("Issue title:"))
        layout.addWidget(self._title_edit)

        layout.addWidget(QLabel("Diagnostics (included in report):"))
        self._body_edit = QPlainTextEdit()
        self._body_edit.setReadOnly(False)  # allow user to trim sensitive lines
        layout.addWidget(self._body_edit, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Open GitHub Issue")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate(self) -> None:
        self._title_edit.setPlainText("Bug: ")

        sysinfo = "\n".join([
            f"EVE Alert version : {__version__}",
            f"Python            : {sys.version.split()[0]}",
            f"Platform          : {platform.platform()}",
        ])

        log_text = self._collect_log()

        body = textwrap.dedent(f"""\
            ## Environment
            ```
            {sysinfo}
            ```

            ## Steps to reproduce
            1. 
            2. 

            ## Expected behaviour
            <!-- What should happen -->

            ## Actual behaviour
            <!-- What actually happened -->

            ## Log output (last {_MAX_LOG_CHARS // 1000}k chars)
            <details>
            <summary>Click to expand</summary>

            ```
            {log_text}
            ```
            </details>
        """)
        self._body_edit.setPlainText(body)
        self._body = body

    def _collect_log(self) -> str:
        """Return recent log lines from the LogPane ring buffer."""
        try:
            # LogPane stores entries as (text, color) tuples in _entries deque.
            entries = list(getattr(self._log_pane, "_entries", []))
            lines = [text for text, _color in entries]
            combined = "\n".join(lines)
            if len(combined) > _MAX_LOG_CHARS:
                combined = "...(truncated — oldest lines removed)...\n" + combined[-_MAX_LOG_CHARS:]
            return combined or "(no log entries)"
        except Exception:
            return "(log unavailable)"

    # ------------------------------------------------------------------
    # Public API

    def github_url(self) -> str:
        """Build the pre-filled GitHub new-issue URL from the current dialog state."""
        import urllib.parse  # noqa: PLC0415

        title = self._title_edit.toPlainText().strip() or "Bug report"
        body = self._body_edit.toPlainText()
        params = urllib.parse.urlencode({"title": title, "body": body})
        return f"{_GITHUB_NEW_ISSUE}?{params}"
