"""Update dialog for EVE Alert.

Shows a download progress bar and confirmation before the app replaces
itself with the new release.

Only displayed when:
  - A newer GitHub release was detected by _check_for_update()
  - The app is running as a frozen Windows .exe  (is_updatable() == True)
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from evealert import __version__
from evealert.tools.self_updater import (
    cleanup_temp_download,
    get_current_exe,
    launch_swap_and_exit,
    temp_download_path,
    write_swap_script,
)
from evealert.tools.update_checker import download_release, fetch_latest_asset_url

logger = logging.getLogger("alert.update")


# ---------------------------------------------------------------------------
# Background download worker (runs inside a QThread)
# ---------------------------------------------------------------------------

from PySide6.QtCore import QObject, QThread  # noqa: E402


class _DownloadWorker(QObject):
    """Streams the release asset to disk and emits progress/finished/failed."""

    progress = Signal(int, int)   # bytes_done, total_bytes
    finished = Signal(Path)       # destination path on success
    failed = Signal(str)          # error message on failure

    def __init__(self, asset_url: str, dest: Path) -> None:
        super().__init__()
        self._asset_url = asset_url
        self._dest = dest
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @Slot()
    def run(self) -> None:
        def _cb(done: int, total: int) -> None:
            if not self._cancelled:
                self.progress.emit(done, total)

        try:
            asyncio.run(download_release(self._asset_url, self._dest, _cb))
            if self._cancelled:
                cleanup_temp_download()
            else:
                self.finished.emit(self._dest)
        except Exception as exc:
            cleanup_temp_download()
            self.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class UpdateDialog(QDialog):
    """Download + swap confirmation dialog."""

    # Thread-safe signal: emitted from the URL-resolution daemon thread
    _url_ready = Signal(object)   # str | None

    def __init__(self, parent, new_tag: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("EVE Alert — Update Available")
        self.setMinimumWidth(440)
        self._new_tag = new_tag
        self._asset_url: str | None = None
        self._dest: Path = temp_download_path()
        self._thread: QThread | None = None
        self._worker: _DownloadWorker | None = None

        self._url_ready.connect(self._on_url_resolved)
        self._build_ui()

        # Resolve the asset download URL in the background so the dialog
        # opens instantly even on a slow connection.
        threading.Thread(
            target=self._resolve_url, daemon=True, name="eve-update-resolve"
        ).start()

    # ------------------------------------------------------------------
    # UI construction

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._header = QLabel(
            f"<b>EVE Alert {self._new_tag}</b> is available.<br>"
            f"You are running <b>v{__version__}</b>."
        )
        self._header.setWordWrap(True)
        layout.addWidget(self._header)

        self._status = QLabel("Resolving download URL…")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._note = QLabel(
            "<small>The app will close and relaunch automatically after updating.</small>"
        )
        self._note.setWordWrap(True)
        layout.addWidget(self._note)

        self._buttons = QDialogButtonBox()
        self._btn_update = QPushButton("Download && Update")
        self._btn_update.setEnabled(False)
        self._btn_cancel = QPushButton("Cancel")
        self._buttons.addButton(
            self._btn_update, QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._buttons.addButton(
            self._btn_cancel, QDialogButtonBox.ButtonRole.RejectRole
        )
        self._btn_update.clicked.connect(self._start_download)
        self._btn_cancel.clicked.connect(self._on_cancel)
        layout.addWidget(self._buttons)

    # ------------------------------------------------------------------
    # URL resolution — runs on a daemon thread, marshals back via signal

    def _resolve_url(self) -> None:
        try:
            url = asyncio.run(fetch_latest_asset_url(self._new_tag))
        except Exception as exc:
            logger.debug("Asset URL resolution failed: %s", exc)
            url = None
        self._url_ready.emit(url)

    @Slot(object)
    def _on_url_resolved(self, url: str | None) -> None:
        if url:
            self._asset_url = url
            self._status.setText("Ready to download.")
            self._btn_update.setEnabled(True)
        else:
            self._status.setText(
                "Could not find a download asset for this release.\n"
                "Visit https://github.com/bluhayz/EVE-Alert/releases "
                "to update manually."
            )

    # ------------------------------------------------------------------
    # Download

    def _start_download(self) -> None:
        if not self._asset_url:
            return

        self._btn_update.setEnabled(False)
        self._progress.show()
        self._status.setText("Downloading…")

        self._worker = _DownloadWorker(self._asset_url, self._dest)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_download_finished)
        self._worker.failed.connect(self._on_download_failed)
        self._thread.start()

    @Slot(int, int)
    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress.setRange(0, 100)
            self._progress.setValue(int(done / total * 100))
        else:
            # Content-Length not provided — show indeterminate bar
            self._progress.setRange(0, 0)

    @Slot(Path)
    def _on_download_finished(self, dest: Path) -> None:
        self._thread.quit()
        self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._btn_cancel.setEnabled(False)

        current_exe = get_current_exe()
        if current_exe is None:
            # Dev / source run — can't do the file swap
            self._status.setText(
                f"Downloaded to:\n{dest}\n\n"
                "Running from source — replace the .exe manually."
            )
            return

        self._status.setText("Download complete — relaunching after close…")
        swap = write_swap_script(current_exe, dest, os.getpid(), relaunch=True)
        launch_swap_and_exit(swap)
        # accept() signals the main window to call exit_app()
        self.accept()

    @Slot(str)
    def _on_download_failed(self, msg: str) -> None:
        self._thread.quit()
        self._status.setText(f"Download failed: {msg}")
        self._progress.hide()
        self._btn_update.setEnabled(True)

    # ------------------------------------------------------------------
    # Cancel

    def _on_cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
        cleanup_temp_download()
        self.reject()
