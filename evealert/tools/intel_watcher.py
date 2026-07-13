"""Intel channel log watcher for EVE Alert.

Tails the most-recently-modified EVE Online chat log whose filename contains
a configurable channel name substring (e.g. "Intel").  New lines are parsed
and forwarded to the alarm log on the main GUI thread.

EVE log directory locations:
  Windows : ~/Documents/EVE/logs/Chatlogs/
  macOS   : ~/Documents/EVE/logs/Chatlogs/
  Linux   : ~/Documents/EVE/logs/Chatlogs/

Log filenames look like:
  Intel_20240501_153022.txt
"""

import asyncio
import logging
import os
import platform
from pathlib import Path
from typing import Callable

from evealert.tools.intel_parser import IntelReport, parse_line

logger = logging.getLogger("alert.intel")

# How often (seconds) to poll the log file for new lines
_POLL_INTERVAL = 2.0
# Maximum bytes to read per poll cycle (prevents runaway memory on huge appends)
_MAX_READ = 65_536


def get_eve_chatlog_dir() -> Path | None:
    """Return the EVE chat-log directory for the current platform, or None."""
    home = Path.home()
    candidate = home / "Documents" / "EVE" / "logs" / "Chatlogs"
    if candidate.is_dir():
        return candidate
    # Some installations use a capital L
    candidate2 = home / "Documents" / "EVE" / "Logs" / "Chatlogs"
    if candidate2.is_dir():
        return candidate2
    return None


def find_intel_log(chatlog_dir: Path, channel_pattern: str) -> Path | None:
    """Return the most-recently-modified log file whose name contains *channel_pattern*.

    Returns None if no matching file is found.
    """
    if not chatlog_dir.is_dir():
        return None
    pattern = channel_pattern.lower()
    candidates = [p for p in chatlog_dir.glob("*.txt") if pattern in p.stem.lower()]
    if not candidates:
        return None
    # Pick the most recently modified file (EVE creates a new file each session)
    return max(candidates, key=lambda p: p.stat().st_mtime)


class IntelWatcher:
    """Async task that tails an EVE intel chat log and raises callbacks on new lines.

    Callbacks:
      callback(raw_line)         — every non-empty line (backward compat)
      on_intel(IntelReport)      — parsed intel reports; not called for clear lines
                                    unless parse_all=True

    Usage::

        watcher = IntelWatcher(
            channel_pattern="Intel",
            callback=handle_raw,
            on_intel=handle_report,
        )
        asyncio.ensure_future(watcher.run())
        ...
        watcher.stop()
    """

    def __init__(
        self,
        channel_pattern: str,
        callback: Callable[[str], None],
        chatlog_dir: Path | None = None,
        on_intel: Callable[[IntelReport], None] | None = None,
        parse_all: bool = True,
    ) -> None:
        self.channel_pattern = channel_pattern.strip() or "Intel"
        self.callback = callback
        self._chatlog_dir = chatlog_dir or get_eve_chatlog_dir()
        self._running = False
        self._log_path: Path | None = None
        self._file_pos: int = 0
        self._on_intel: Callable[[IntelReport], None] | None = on_intel
        self._parse_all: bool = parse_all  # if False, skip clear-only reports

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """Main tail loop — runs until stop() is called."""
        self._running = True

        if self._chatlog_dir is None:
            logger.warning(
                "EVE chatlog directory not found. Intel watcher disabled. "
                "Expected: %s/Documents/EVE/logs/Chatlogs/",
                Path.home(),
            )
            return

        logger.info(
            "Intel watcher started (pattern=%r, dir=%s)",
            self.channel_pattern,
            self._chatlog_dir,
        )

        while self._running:
            # Re-resolve the log file each cycle so we pick up new session files
            new_path = find_intel_log(self._chatlog_dir, self.channel_pattern)

            if new_path != self._log_path:
                # New file (or first time) — seek to end to avoid flooding old history
                self._log_path = new_path
                if new_path is not None:
                    try:
                        self._file_pos = new_path.stat().st_size
                        logger.info(
                            "Intel watcher tracking: %s (pos=%d)",
                            new_path,
                            self._file_pos,
                        )
                    except OSError:
                        self._file_pos = 0

            if self._log_path is not None:
                self._tail_once()

            await asyncio.sleep(_POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _tail_once(self) -> None:
        """Read any new bytes from the log file and call the callback per line."""
        assert self._log_path is not None
        try:
            size = self._log_path.stat().st_size
        except OSError:
            return

        if size < self._file_pos:
            # File was truncated / rotated — reset to start
            self._file_pos = 0

        if size == self._file_pos:
            return  # Nothing new

        try:
            with open(self._log_path, encoding="utf-8", errors="replace") as fh:
                fh.seek(self._file_pos)
                chunk = fh.read(min(size - self._file_pos, _MAX_READ))
                self._file_pos = fh.tell()
        except OSError:
            return

        for line in chunk.splitlines():
            line = line.strip()
            if line:
                try:
                    self.callback(line)
                except Exception:
                    pass
                if self._on_intel is not None:
                    try:
                        report = parse_line(line)
                        if report is not None:
                            if self._parse_all or not report.is_clear:
                                self._on_intel(report)
                    except Exception:
                        pass
