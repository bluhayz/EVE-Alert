"""D-scan log watcher for EVE Alert.

EVE Online writes D-scan results to ~/Documents/EVE/logs/Dscan/ each time
the player presses the scan button. This module tails the most-recent file
in that directory, classifies ship names against a threat tier map, and
fires callbacks for new entries.

Threat tiers (#78):
  RED    — immediate danger: interceptors, HICs, recons, bombers, capitals
  ORANGE — combat ships: assault frigates, battlecruisers, battleships
  YELLOW — watch: cloaky T3Cs, strategic cruisers, logistics
  GREEN  — safe: pods, industrials, mining ships

Probe detection (#79):
  Core Scanner Probes / Combat Scanner Probes trigger a distinct PROBE_DETECTED
  callback regardless of ship tier.

D-scan timeline (#80):
  Entries are recorded with timestamps so callers can show what appeared and
  disappeared over the session.
"""

import asyncio
import logging
import time
from collections import deque
from pathlib import Path
from typing import Callable, NamedTuple

logger = logging.getLogger("alert.dscan")

_POLL_INTERVAL = 1.5  # seconds between file polls
_MAX_READ = 65_536
_HISTORY_MAXLEN = 200


# ------------------------------------------------------------------
# Ship classification (#78)
# ------------------------------------------------------------------

# Substrings matched case-insensitively against ship/object names.
# Order matters — first match wins.
_TIER_MAP: dict[str, list[str]] = {
    "red": [
        # Covert-ops / recon
        "recon",
        "force recon",
        "combat recon",
        # Interdictors / HICs
        "interdictor",
        "heavy interdictor",
        # Interceptors
        "interceptor",
        # Electronic attack
        "electronic attack",
        # Command destroyers
        "command destroyer",
        # Stealth bombers
        "stealth bomber",
        # Black ops
        "black ops",
        # Capitals
        "carrier",
        "dreadnought",
        "force auxiliary",
        "fax",
        "titan",
        "supercarrier",
        "super carrier",
        # Specific named ships often used for danger
        "sabre",
        "flycatcher",
        "heretic",
        "eris",
        "rapier",
        "huginn",
        "falcon",
        "rook",
        "pilgrim",
        "curse",
        "astero",
        "stratios",
    ],
    "orange": [
        "assault frigate",
        "assault ship",
        "heavy assault cruiser",
        "attack battlecruiser",
        "combat battlecruiser",
        "battlecruiser",
        "battleship",
        "destroyer",
        "frigate",
        "cruiser",
        "combat ship",
    ],
    "yellow": [
        "strategic cruiser",
        "t3 cruiser",
        "logistics cruiser",
        "logistics",
        "command ship",
        "tech 3",
    ],
    "green": [
        "capsule",
        "pod",
        "industrial",
        "hauler",
        "freighter",
        "jump freighter",
        "mining barge",
        "exhumer",
        "venture",
        "shuttle",
        "rookie ship",
    ],
}

# Probe names that trigger the distinct probe alert (#79)
_PROBE_NAMES = [
    "core scanner probe",
    "combat scanner probe",
    "sisters core scanner probe",
    "sisters combat scanner probe",
    "expanded probe launcher",  # sometimes appears on scan
]


def classify_entry(name: str) -> str:
    """Return 'red', 'orange', 'yellow', 'green', 'probe', or 'unknown'."""
    lower = name.lower()
    for probe in _PROBE_NAMES:
        if probe in lower:
            return "probe"
    for tier, keywords in _TIER_MAP.items():
        for kw in keywords:
            if kw in lower:
                return tier
    return "unknown"


# ------------------------------------------------------------------
# Timeline entry (#80)
# ------------------------------------------------------------------


class DscanEntry(NamedTuple):
    name: str
    tier: str  # red / orange / yellow / green / probe / unknown
    timestamp: float
    appeared: bool  # True = appeared, False = disappeared


# ------------------------------------------------------------------
# Watcher
# ------------------------------------------------------------------


class DscanWatcher:
    """Async task that tails the EVE D-scan log and fires callbacks.

    Callbacks:
      on_threat(tier: str, name: str)  — called for each new RED/ORANGE entry
      on_probe()                        — called when probes detected
      on_entry(entry: DscanEntry)       — called for every new entry (for timeline)
    """

    def __init__(
        self,
        on_threat: Callable[[str, str], None] = lambda t, n: None,
        on_probe: Callable[[], None] = lambda: None,
        on_entry: Callable[[DscanEntry], None] = lambda e: None,
    ) -> None:
        self._on_threat = on_threat
        self._on_probe = on_probe
        self._on_entry = on_entry
        self._running = False
        self._log_path: Path | None = None
        self._file_pos: int = 0
        # Track what's currently visible to detect disappearances
        self._visible: set[str] = set()
        # Session timeline
        self.timeline: deque[DscanEntry] = deque(maxlen=_HISTORY_MAXLEN)

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        self._running = True
        dscan_dir = self._find_dscan_dir()
        if dscan_dir is None:
            logger.warning("D-scan log directory not found. D-scan monitor inactive.")
            return

        logger.info("D-scan watcher started in %s", dscan_dir)

        while self._running:
            new_path = self._latest_log(dscan_dir)
            if new_path != self._log_path:
                self._log_path = new_path
                self._file_pos = new_path.stat().st_size if new_path else 0
                self._visible.clear()

            if self._log_path is not None:
                self._tail_once()

            await asyncio.sleep(_POLL_INTERVAL)

    # ------------------------------------------------------------------

    def _tail_once(self) -> None:
        assert self._log_path is not None
        try:
            size = self._log_path.stat().st_size
        except OSError:
            return

        if size < self._file_pos:
            self._file_pos = 0
        if size == self._file_pos:
            return

        try:
            with open(self._log_path, encoding="utf-16", errors="replace") as fh:
                fh.seek(self._file_pos)
                chunk = fh.read(min(size - self._file_pos, _MAX_READ))
                self._file_pos = fh.tell()
        except (OSError, UnicodeError):
            # EVE D-scan files may be UTF-16 or UTF-8 depending on client version
            try:
                with open(self._log_path, encoding="utf-8", errors="replace") as fh:
                    fh.seek(self._file_pos)
                    chunk = fh.read(min(size - self._file_pos, _MAX_READ))
                    self._file_pos = fh.tell()
            except OSError:
                return

        # Each D-scan line: "Name\tDistance\tType\tGroup"
        # We care about the Type column (index 2) for classification.
        current_scan: set[str] = set()
        probe_detected = False

        for line in chunk.splitlines():
            parts = line.strip().split("\t")
            if len(parts) < 1:
                continue
            # Use the full name for classification; first column is the object name
            obj_name = parts[0].strip()
            if not obj_name:
                continue

            current_scan.add(obj_name)
            tier = classify_entry(obj_name)

            entry = DscanEntry(
                name=obj_name, tier=tier, timestamp=time.time(), appeared=True
            )
            self.timeline.append(entry)

            try:
                self._on_entry(entry)
            except Exception:
                pass

            if tier == "probe":
                probe_detected = True
            elif tier in ("red", "orange"):
                try:
                    self._on_threat(tier, obj_name)
                except Exception:
                    pass

        if probe_detected:
            try:
                self._on_probe()
            except Exception:
                pass

        # Detect disappearances from previous scan
        disappeared = self._visible - current_scan
        for name in disappeared:
            entry = DscanEntry(
                name=name,
                tier=classify_entry(name),
                timestamp=time.time(),
                appeared=False,
            )
            self.timeline.append(entry)
            try:
                self._on_entry(entry)
            except Exception:
                pass

        if current_scan:
            self._visible = current_scan

    @staticmethod
    def _find_dscan_dir() -> Path | None:
        home = Path.home()
        for candidate in [
            home / "Documents" / "EVE" / "logs" / "Dscan",
            home / "Documents" / "EVE" / "Logs" / "Dscan",
        ]:
            if candidate.is_dir():
                return candidate
        return None

    @staticmethod
    def _latest_log(dscan_dir: Path) -> Path | None:
        files = list(dscan_dir.glob("*.txt"))
        if not files:
            return None
        return max(files, key=lambda p: p.stat().st_mtime)
