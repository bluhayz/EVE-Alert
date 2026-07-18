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
import re
from collections import Counter
from pathlib import Path
from typing import Callable

from evealert.tools.intel_parser import IntelReport, parse_line

logger = logging.getLogger("alert.intel")

# How often (seconds) to poll the log file for new lines
_POLL_INTERVAL = 2.0
# Maximum bytes to read per poll cycle (prevents runaway memory on huge appends)
_MAX_READ = 65_536

# EVE log filenames are '<ChannelName>_<YYYYMMDD>_<HHMMSS>_<ownerID>.txt'
# (the trailing owner/character ID segment is present on real EVE clients;
# the two-part '<YYYYMMDD>_<HHMMSS>.txt' suffix is kept optional for older
# logs/fixtures that lack it, #226). Anchored on the trailing date/time
# suffix (not split on the first underscore) so multi-word/hyphenated
# channel names like 'Local_D7-ZAC' or 'I. Ftn Intel' stay intact (#191).
_LOG_FILENAME_RE = re.compile(r"^(.*)_\d{8}_\d{6}(?:_\d+)?\.txt$", re.IGNORECASE)


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


def discover_channels(chatlog_dir: Path) -> list[str]:
    """Return the sorted, de-duplicated list of channel names present in
    *chatlog_dir*, derived from log filenames (#191).

    De-duplicates case-insensitively but preserves the most-frequently-seen
    casing for each channel (ties broken by filename sort order, for
    deterministic results). Files that aren't ".txt" or don't end in the
    standard "_YYYYMMDD_HHMMSS.txt" suffix are ignored. Returns an empty
    list for a missing/non-directory path -- never raises.
    """
    if not chatlog_dir.is_dir():
        return []

    casing_counts: dict[str, Counter] = {}
    for entry in sorted(chatlog_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_file():
            continue
        m = _LOG_FILENAME_RE.match(entry.name)
        if not m:
            continue
        channel = m.group(1)
        if not channel:
            continue
        key = channel.lower()
        casing_counts.setdefault(key, Counter())[channel] += 1

    names = [counts.most_common(1)[0][0] for counts in casing_counts.values()]
    return sorted(names)


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
        channel_name: str | None = None,
        is_duplicate: Callable[[str], bool] | None = None,
    ) -> None:
        self.channel_pattern = channel_pattern.strip() or "Intel"
        # #171: the human-readable channel label attached to each parsed
        # IntelReport (report.channel) -- defaults to channel_pattern so
        # single-channel callers don't need to pass this separately.
        self.channel_name = (channel_name or self.channel_pattern).strip()
        self.callback = callback
        self._chatlog_dir = chatlog_dir or get_eve_chatlog_dir()
        self._running = False
        self._log_path: Path | None = None
        self._file_pos: int = 0
        self._encoding: str | None = None  # detected once per file from the BOM
        self._on_intel: Callable[[IntelReport], None] | None = on_intel
        self._parse_all: bool = parse_all  # if False, skip clear-only reports
        # #171: optional cross-instance dedup check (line) -> bool, shared
        # across multiple IntelWatcher instances by the caller (e.g. the
        # same paste posted in two channels within seconds of each other).
        # None (default) means every line is treated as unique, matching
        # pre-#171 behavior exactly.
        self._is_duplicate = is_duplicate

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
                self._encoding = None
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

    def _detect_encoding(self) -> str:
        """Detect the log encoding once from the BOM.

        EVE Online chat logs are UTF-16 LE (with BOM \xff\xfe) on all modern
        clients.  Some older EVE installs write UTF-8.  We sniff the first
        4 bytes once per file and cache the result.
        """
        if self._encoding:
            return self._encoding
        bom = b""
        try:
            with open(self._log_path, "rb") as fh:
                bom = fh.read(4)
        except OSError:
            pass
        if bom.startswith(b"\xff\xfe"):
            self._encoding = "utf-16-le"
        elif bom.startswith(b"\xfe\xff"):
            self._encoding = "utf-16-be"
        else:
            self._encoding = "utf-8"
        return self._encoding

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

        # Read raw bytes and decode manually.  Opening a UTF-16 file in text
        # mode and seeking to an arbitrary byte offset is invalid — only
        # values returned by tell() are legal seek targets — and causes
        # misaligned reads that produce squares/replacement characters (#enc).
        try:
            with open(self._log_path, "rb") as fh:
                fh.seek(self._file_pos)
                raw = fh.read(min(size - self._file_pos, _MAX_READ))
        except OSError:
            return

        encoding = self._detect_encoding()
        # UTF-16 stores each code unit as 2 bytes; keep the chunk length even
        # so we never split a surrogate pair across poll cycles.
        if encoding.startswith("utf-16") and len(raw) % 2:
            raw = raw[:-1]
        chunk = raw.decode(encoding, errors="replace")
        self._file_pos += len(raw)

        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            # #171: cross-channel dedup -- the same paste often hits
            # multiple watched channels within seconds; skip the whole
            # line (both callbacks) if the caller's shared check says a
            # different channel already saw it recently. A failing check
            # must never block real intel, so it's treated as "not a dup."
            if self._is_duplicate is not None:
                try:
                    if self._is_duplicate(line):
                        continue
                except Exception:
                    pass
            try:
                self.callback(line)
            except Exception:
                pass
            if self._on_intel is not None:
                try:
                    report = parse_line(line)
                    if report is not None:
                        report.channel = self.channel_name
                        if self._parse_all or not report.is_clear:
                            self._on_intel(report)
                except Exception:
                    pass
