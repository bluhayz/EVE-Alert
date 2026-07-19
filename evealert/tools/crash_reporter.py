"""Crash reporting for EVE Alert (#180, v8.0).

Global exception capture across every context the app can fail in --
main thread, background threads, Qt slots/signals, and the engine's own
asyncio loop -- funneled into one local, redacted crash bundle. Strictly
local and opt-in: writing a bundle never leaves the machine on its own;
only a user clicking "Open GitHub issue" in the crash dialog sends
anything anywhere (and that's a browser navigation the user drives, not
a network call this module makes).

Reuses evealert.settings.diagnostics's redaction/context-gathering
helpers so a crash bundle's redaction rules never drift from the
existing "Export Diagnostics Bundle" feature's rules.
"""

import json
import logging
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

logger = logging.getLogger("main")

# How many trailing lines of the most-recently-touched log file to
# include -- enough context around the crash without embedding an
# unbounded amount of (potentially sensitive) session history.
_MAX_LOG_LINES = 200

_installed = False
_on_crash_callback: Callable[[Path], None] | None = None


def get_crash_dir() -> Path:
    """Return (creating if needed) the directory crash bundles are
    written under: <config_dir>/crashes/."""
    from evealert.settings.logger import get_log_dir  # noqa: PLC0415

    crash_dir = get_log_dir().parent / "crashes"
    crash_dir.mkdir(parents=True, exist_ok=True)
    return crash_dir


def _tail_recent_logs(max_lines: int = _MAX_LOG_LINES) -> str:
    """Best-effort tail of the most-recently-modified app log file."""
    from evealert.settings.logger import get_log_dir  # noqa: PLC0415

    log_dir = get_log_dir()
    if not log_dir.is_dir():
        return ""
    log_files = sorted(
        log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not log_files:
        return ""
    try:
        lines = log_files[0].read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except OSError:
        return ""


def _get_settings_snapshot() -> dict | None:
    try:
        from evealert.settings.store import get_settings_store  # noqa: PLC0415

        return get_settings_store().load()
    except Exception:
        return None


def write_crash_bundle(
    exc_type, exc_value, exc_tb, *, context: str = "unknown", settings: dict | None = None
) -> Path | None:
    """Write a crash bundle to get_crash_dir()/<timestamp>/ and return its
    directory. Returns None (never raises) if writing itself fails --
    crash reporting must not be a second way to crash the app.

    Bundle contents: traceback.txt, context.json (app/platform/python
    info, no secrets), redacted_settings.json (when *settings* given,
    via diagnostics._redact_settings -- same rules as the existing
    diagnostics bundle export), recent_log.txt (tail of the most
    recently touched log file).
    """
    try:
        from evealert.settings.diagnostics import _redact_settings, gather_context  # noqa: PLC0415

        ts = time.strftime("%Y%m%d_%H%M%S")
        bundle_dir = get_crash_dir() / ts
        bundle_dir.mkdir(parents=True, exist_ok=True)

        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        (bundle_dir / "traceback.txt").write_text(tb_text, encoding="utf-8")

        ctx = gather_context(settings)
        ctx["crash"] = {
            "context": context,
            "exception_type": exc_type.__name__ if exc_type else None,
            "exception_message": str(exc_value) if exc_value else None,
        }
        (bundle_dir / "context.json").write_text(
            json.dumps(ctx, indent=2, default=str), encoding="utf-8"
        )

        if settings:
            redacted = _redact_settings(settings)
            (bundle_dir / "redacted_settings.json").write_text(
                json.dumps(redacted, indent=2, default=str), encoding="utf-8"
            )

        recent_logs = _tail_recent_logs()
        if recent_logs:
            (bundle_dir / "recent_log.txt").write_text(recent_logs, encoding="utf-8")

        logger.error("Crash bundle written (%s): %s", context, bundle_dir)
        return bundle_dir
    except Exception:
        logger.exception("Failed to write crash bundle")
        return None


def mark_acknowledged(bundle_dir: Path) -> None:
    """Mark *bundle_dir* as shown to the user, so install()'s startup
    scan doesn't re-show the same crash dialog on the next launch."""
    try:
        (bundle_dir / ".acknowledged").touch()
    except OSError:
        pass


def find_unacknowledged_crash() -> Path | None:
    """Return the most recent crash bundle directory that has no
    `.acknowledged` marker, or None. Used at startup for the "on next
    launch" half of the crash dialog's trigger."""
    try:
        crash_dir = get_crash_dir()
        candidates = [
            d for d in crash_dir.iterdir()
            if d.is_dir() and not (d / ".acknowledged").exists()
        ]
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.stat().st_mtime)


def _handle_exception(exc_type, exc_value, exc_tb, *, context: str) -> None:
    if exc_type is None or issubclass(exc_type, KeyboardInterrupt):
        if exc_type is not None:
            sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    bundle_dir = write_crash_bundle(
        exc_type, exc_value, exc_tb, context=context, settings=_get_settings_snapshot()
    )
    # Always still print to stderr -- crash reporting must not make a
    # dev-run/console session go silent on an unhandled error.
    traceback.print_exception(exc_type, exc_value, exc_tb)
    if _on_crash_callback is not None and bundle_dir is not None:
        try:
            _on_crash_callback(bundle_dir)
        except Exception:
            logger.exception("Crash notification callback failed")


def _sys_excepthook(exc_type, exc_value, exc_tb) -> None:
    _handle_exception(exc_type, exc_value, exc_tb, context="main-thread")


def _threading_excepthook(args) -> None:
    thread_name = args.thread.name if args.thread else "unknown"
    _handle_exception(
        args.exc_type, args.exc_value, args.exc_traceback,
        context=f"thread:{thread_name}",
    )


def install_asyncio_handler(loop) -> None:
    """Route uncaught exceptions on an asyncio event loop (the engine's
    own dedicated loop, per-instance -- not a global hook) through the
    same crash-bundle path."""

    def _handler(_loop, ctx: dict) -> None:
        exc = ctx.get("exception")
        if exc is not None:
            _handle_exception(type(exc), exc, exc.__traceback__, context="asyncio")
        else:
            logger.error("Asyncio error: %s", ctx.get("message"))

    loop.set_exception_handler(_handler)


def install_qt_handler() -> None:
    """Route Qt's own internal fatal/critical messages (not Python
    exceptions -- e.g. a failed signal connection) into the log at
    ERROR level. Does not write a crash bundle: most QtCriticalMsg
    output is a warning about a specific widget/signal, not an
    application-fatal event, and Qt provides no traceback to bundle."""
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler  # noqa: PLC0415

    def _handler(msg_type, _context, message) -> None:
        if msg_type in (QtMsgType.QtFatalMsg, QtMsgType.QtCriticalMsg):
            logger.error("Qt %s: %s", msg_type.name, message)
        elif msg_type == QtMsgType.QtWarningMsg:
            logger.debug("Qt warning: %s", message)

    qInstallMessageHandler(_handler)


def install(on_crash: Callable[[Path], None] | None = None, *, enabled: bool = True) -> None:
    """Install the process-wide exception hooks (sys.excepthook,
    threading.excepthook). Call once at app startup, before creating
    the main window.

    *on_crash*, if given, is called with the crash bundle directory
    whenever ANY hook (including install_asyncio_handler's) writes one
    -- wired to show the crash dialog. *enabled=False* corresponds to
    the diagnostics.crash_reports settings toggle being off: hooks are
    not installed at all (Python's default excepthook still prints to
    stderr, same as before this feature existed).
    """
    global _installed, _on_crash_callback
    _on_crash_callback = on_crash
    if not enabled:
        return
    if _installed:
        return
    sys.excepthook = _sys_excepthook
    threading.excepthook = _threading_excepthook
    _installed = True
