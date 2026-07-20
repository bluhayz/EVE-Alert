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
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from evealert import __version__

_GITHUB_NEW_ISSUE = "https://github.com/bluhayz/EVE-Alert/issues/new"
_MAX_LOG_CHARS = 3_000  # GitHub URL length limit is ~8 KB; keep body manageable
# #252: github_url() re-checks the fully percent-ENCODED length against
# this byte budget and trims further if needed -- _MAX_LOG_CHARS alone
# caps raw characters, but encoding inflates size (spaces, brackets,
# colons in log timestamps), so a body under _MAX_LOG_CHARS could still
# produce a URL past GitHub's ~8KB new-issue limit, especially once the
# user's own edits/pastes are added in the editable body field.
_MAX_URL_BYTES = 7_500
_TRIM_STEP_CHARS = 500


class BugReporterDialog(QDialog):
    """Collect diagnostics and preview the GitHub issue before opening."""

    def __init__(self, parent, log_pane, extra_body: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("EVE Alert — Report a Bug")
        self.setMinimumWidth(600)
        self.setMinimumHeight(480)
        self._log_pane = log_pane
        self._extra_body = extra_body
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

        # OCR debug screenshot notice (shown only when a debug file exists)
        self._screenshot_bar = self._build_screenshot_bar()
        if self._screenshot_bar:
            layout.addWidget(self._screenshot_bar)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Open GitHub Issue")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_screenshot_bar(self):
        """Return a widget showing the OCR debug screenshot path, or None."""
        try:
            from evealert.tools.ocr_local import get_ocr_debug_path  # noqa: PLC0415
            path = get_ocr_debug_path()
            if not path.exists():
                return None
        except Exception:
            return None

        from PySide6.QtWidgets import QWidget  # noqa: PLC0415
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 2, 0, 2)

        lbl = QLabel(
            f"<b>OCR debug screenshot</b> available — attach to the issue:<br>"
            f"<small>{path}</small>"
        )
        lbl.setWordWrap(True)

        btn_open = QPushButton("Open folder")
        btn_open.setFixedWidth(100)
        btn_open.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        )

        row.addWidget(lbl, 1)
        row.addWidget(btn_open)
        return bar

    def _populate(self) -> None:
        self._title_edit.setPlainText("Bug: ")

        sysinfo = "\n".join([
            f"EVE Alert version : {__version__}",
            f"Python            : {sys.version.split()[0]}",
            f"Platform          : {platform.platform()}",
        ])

        log_text = self._collect_log()
        debug_screenshot_note = self._collect_ocr_debug_note()

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
            {debug_screenshot_note}
        """)
        if self._extra_body:
            body = body.rstrip() + "\n\n" + self._extra_body + "\n"
        self._body_edit.setPlainText(body)
        self._body = body

    def _collect_log(self) -> str:
        """Return recent log lines from the LogPane ring buffer."""
        try:
            return self._log_pane.get_log_text(max_chars=_MAX_LOG_CHARS) or "(no log entries)"
        except Exception:
            return "(log unavailable)"

    def _collect_ocr_debug_note(self) -> str:
        """Return a Markdown note about the OCR debug screenshot if one exists."""
        try:
            from evealert.tools.ocr_local import get_ocr_debug_path  # noqa: PLC0415
            path = get_ocr_debug_path()
            if path.exists():
                import time  # noqa: PLC0415
                age_s = int(time.time() - path.stat().st_mtime)
                age_str = f"{age_s // 60} min ago" if age_s >= 60 else f"{age_s}s ago"
                return (
                    f"\n## OCR debug screenshot ({age_str})\n"
                    f"A screenshot of the OCR capture region was saved when names "
                    f"could not be detected.  Please attach it to this issue:\n"
                    f"```\n{path}\n```\n"
                )
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # Public API

    def github_url(self) -> str:
        """Build the pre-filled GitHub new-issue URL from the current dialog state."""
        import urllib.parse  # noqa: PLC0415

        title = self._title_edit.toPlainText().strip() or "Bug report"
        body = self._body_edit.toPlainText()

        def _url_for(text: str) -> str:
            params = urllib.parse.urlencode({"title": title, "body": text})
            return f"{_GITHUB_NEW_ISSUE}?{params}"

        url = _url_for(body)
        # #252: cap the ENCODED url length, not raw body characters -- see
        # _MAX_URL_BYTES. Trim from the end: the environment/repro-steps
        # sections at the top are short and matter most; the log dump
        # (the largest chunk) sits later in the body.
        while len(url.encode("utf-8")) > _MAX_URL_BYTES and body:
            body = body[: max(0, len(body) - _TRIM_STEP_CHARS)]
            url = _url_for(body)
        return url
