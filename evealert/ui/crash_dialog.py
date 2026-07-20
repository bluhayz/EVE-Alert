"""Crash notification dialog for EVE Alert (#180, v8.0).

Shown either immediately (a non-fatal error caught in a Qt slot or the
engine's asyncio loop -- the app is still running) or on the next launch
(a fatal main-thread crash killed the previous session before a dialog
could show). Mirrors bug_reporter.py's GitHub-issue-prefill pattern, but
sources its report from a written crash bundle instead of the live log
pane.
"""

from __future__ import annotations

import json
import textwrap
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from evealert import __version__

_GITHUB_NEW_ISSUE = "https://github.com/bluhayz/EVE-Alert/issues/new"
# #252: this is a byte budget on the fully percent-ENCODED URL, not a
# character cap on the raw traceback -- percent-encoding inflates size
# roughly 3x for the newlines/quotes/backslashes tracebacks are full of
# (especially Windows paths), so capping raw characters first still let
# URLs sail past GitHub's ~8KB new-issue-URL limit for large tracebacks.
_MAX_URL_BYTES = 7_500
_TRIM_STEP_CHARS = 500


class CrashDialog(QDialog):
    """"EVE Alert hit an error" — view the report, open a GitHub issue, or dismiss."""

    def __init__(self, parent, bundle_dir: Path) -> None:
        super().__init__(parent)
        self.setWindowTitle("EVE Alert — Unexpected Error")
        self.setMinimumWidth(560)
        self.setMinimumHeight(400)
        self._bundle_dir = bundle_dir
        self._build_ui()
        self._populate()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        info = QLabel(
            "EVE Alert hit an unexpected error. A local diagnostic report was "
            "saved -- nothing was sent anywhere. You can review it below, open "
            "a pre-filled GitHub issue (opens your browser; you can edit or "
            "cancel it before submitting), or just dismiss this."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self._report_view = QPlainTextEdit()
        self._report_view.setReadOnly(True)
        layout.addWidget(self._report_view, 1)

        btn_row = QHBoxLayout()
        btn_open_folder = QPushButton("View Report Folder")
        btn_open_folder.clicked.connect(self._open_folder)
        btn_github = QPushButton("Open GitHub Issue")
        btn_github.clicked.connect(self._open_github_issue)
        btn_dismiss = QPushButton("Dismiss")
        btn_dismiss.clicked.connect(self.accept)
        btn_row.addWidget(btn_open_folder)
        btn_row.addWidget(btn_github)
        btn_row.addStretch()
        btn_row.addWidget(btn_dismiss)
        layout.addLayout(btn_row)

    def _populate(self) -> None:
        self._traceback = self._read(self._bundle_dir / "traceback.txt")
        self._context = self._read(self._bundle_dir / "context.json")
        self._report_view.setPlainText(
            f"{self._traceback}\n\n--- context ---\n{self._context}"
        )

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return "(not available)"

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._bundle_dir)))

    def _crash_summary(self) -> str:
        """First line of the traceback's exception (e.g. "ValueError:
        bad thing happened"), or a generic fallback."""
        for line in reversed(self._traceback.strip().splitlines()):
            if line and not line.startswith(" "):
                return line
        return "Unhandled exception"

    def _body_for(self, tb_text: str, *, truncated: bool) -> str:
        note = (
            f"\n<!-- Traceback truncated to fit the URL length limit -- "
            f"full traceback is in {self._bundle_dir}/traceback.txt -->\n"
            if truncated
            else ""
        )
        return textwrap.dedent(f"""\
            ## Environment
            ```
            EVE Alert version: {__version__}
            ```

            ## What happened
            <!-- What were you doing when this happened? -->

            ## Traceback
            ```
            {tb_text}
            ```
            {note}"""
        )

    def github_url(self) -> str:
        title = f"Crash: {self._crash_summary()}"[:250]
        tb = self._traceback
        truncated = False

        def _url_for(text: str) -> str:
            params = urllib.parse.urlencode(
                {"title": title, "body": self._body_for(text, truncated=truncated)}
            )
            return f"{_GITHUB_NEW_ISSUE}?{params}"

        url = _url_for(tb)
        # #252: cap the ENCODED url length, not the raw traceback -- see
        # _MAX_URL_BYTES.
        while len(url.encode("utf-8")) > _MAX_URL_BYTES and tb:
            tb = tb[: max(0, len(tb) - _TRIM_STEP_CHARS)]
            truncated = True
            url = _url_for(tb)
        return url

    def _open_github_issue(self) -> None:
        QDesktopServices.openUrl(QUrl(self.github_url()))


def maybe_show_pending_crash(parent) -> None:
    """Check for an unacknowledged crash bundle from a previous session
    (the fatal-main-thread-crash case) and show CrashDialog for it if
    found. Call once at startup, after the main window exists.
    """
    from evealert.tools.crash_reporter import find_unacknowledged_crash, mark_acknowledged  # noqa: PLC0415

    bundle_dir = find_unacknowledged_crash()
    if bundle_dir is None:
        return
    dlg = CrashDialog(parent, bundle_dir)
    dlg.exec()
    mark_acknowledged(bundle_dir)
