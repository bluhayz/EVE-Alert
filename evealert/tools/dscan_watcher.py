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

from evealert.data.ship_classes import ShipThreatClass, classify_ship

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
        # Capitals — list "supercarrier"/"super carrier" before "carrier" so
        # the first-match loop labels them correctly (#106).
        "supercarrier",
        "super carrier",
        "carrier",
        "dreadnought",
        "force auxiliary",
        "fax",
        "titan",
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
    tier: str          # red / orange / yellow / green / probe / unknown
    threat_class: ShipThreatClass  # fine-grained class (TACKLE / CYNO / etc.)
    timestamp: float
    appeared: bool     # True = appeared, False = disappeared


# ------------------------------------------------------------------
# Watcher
# ------------------------------------------------------------------


class DscanWatcher:
    """Async task that tails the EVE D-scan log and fires callbacks.

    Callbacks:
      on_threat(tier, name, threat_class)      — called for each new RED/ORANGE entry
      on_probe()                               — called when probes detected
      on_entry(entry: DscanEntry)              — called for every new entry (for timeline)
      on_new_signature(old_count, new_count)   — fired when cosmic-sig count rises (#145)
    """

    def __init__(
        self,
        on_threat: Callable[[str, str, ShipThreatClass], None] = lambda t, n, c: None,
        on_probe: Callable[[], None] = lambda: None,
        on_entry: Callable[[DscanEntry], None] = lambda e: None,
        on_new_signature: Callable[[int, int], None] = lambda old, new: None,
    ) -> None:
        self._on_threat = on_threat
        self._on_probe = on_probe
        self._on_entry = on_entry
        self._on_new_signature = on_new_signature
        self._running = False
        self._log_path: Path | None = None
        self._file_pos: int = 0
        self._encoding: str | None = None  # detected once per file from the BOM
        # Track what's currently visible to detect disappearances
        self._visible: set[str] = set()
        # Type names (col 2) of currently-visible entries — for ship cross-reference (#150)
        self._visible_types: set[str] = set()
        # Cosmic-signature tracking (#145)
        self._sig_count: int = 0
        # Session timeline
        self.timeline: deque[DscanEntry] = deque(maxlen=_HISTORY_MAXLEN)

    @property
    def current_visible_types(self) -> set[str]:
        """Set of ship type-column values currently on D-scan (#150)."""
        return frozenset(self._visible_types)

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
                self._encoding = None
                self._visible.clear()
                self._visible_types.clear()

            if self._log_path is not None:
                self._tail_once()

            await asyncio.sleep(_POLL_INTERVAL)

    # ------------------------------------------------------------------

    def _detect_encoding(self) -> str:
        """Detect the log encoding once from the BOM. EVE writes UTF-16 (older
        clients) or UTF-8 (newer); mid-file chunks have no BOM, so we sniff the
        file header separately."""
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
        assert self._log_path is not None
        try:
            size = self._log_path.stat().st_size
        except OSError:
            return

        if size < self._file_pos:  # file truncated / rotated
            self._file_pos = 0
            self._visible.clear()
            self._visible_types.clear()
        if size <= self._file_pos:
            return

        # Read new bytes from the last byte offset and decode manually. Seeking
        # a text-mode UTF-16 reader to an arbitrary byte offset is invalid
        # (only tell()-returned values are legal) and misaligns reads (#101).
        try:
            with open(self._log_path, "rb") as fh:
                fh.seek(self._file_pos)
                raw = fh.read(min(size - self._file_pos, _MAX_READ))
        except OSError:
            return

        encoding = self._detect_encoding()
        # UTF-16 is 2 bytes/char — keep the chunk length even.
        if encoding.startswith("utf-16") and len(raw) % 2:
            raw = raw[:-1]
        text = raw.decode(encoding, errors="replace")

        read_to_eof = (self._file_pos + len(raw)) >= size
        # Process only complete lines; leave any trailing partial line for the
        # next poll so a record is never split mid-line.
        last_nl = text.rfind("\n")
        if last_nl == -1:
            if not read_to_eof:
                return  # wait for a full line
            complete_text = text
        else:
            complete_text = text[: last_nl + 1]
        self._file_pos += len(complete_text.encode(encoding))

        current_scan, current_types, probe_detected, sig_count = self._parse_lines(complete_text)

        # Signature delta (#145): fire callback when new sigs appear
        if read_to_eof and sig_count > self._sig_count:
            old, new = self._sig_count, sig_count
            self._sig_count = new
            try:
                self._on_new_signature(old, new)
            except Exception:
                pass
        elif read_to_eof:
            self._sig_count = sig_count

        if probe_detected:
            try:
                self._on_probe()
            except Exception:
                pass

        # Only compute disappearances when we've consumed the whole file —
        # a truncated 64KB read would otherwise flag everything not in the
        # partial chunk as "disappeared" (#101).
        if read_to_eof:
            disappeared = self._visible - current_scan
            for name in disappeared:
                entry = DscanEntry(
                    name=name,
                    tier=classify_entry(name),
                    threat_class=classify_ship(name),
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
                self._visible_types = current_types
        else:
            # Accumulate visibility across partial reads without evicting.
            self._visible |= current_scan
            self._visible_types |= current_types

    def _parse_lines(self, text: str) -> tuple[set, bool, int]:
        """Parse D-scan lines, classifying by the Type column (index 2) and
        falling back to the object name (index 0).
        Returns (current_scan set, probe_detected, sig_count)."""
        current_scan: set[str] = set()
        current_types: set[str] = set()
        probe_detected = False
        sig_count = 0
        for line in text.splitlines():
            parts = line.strip().split("\t")
            obj_name = parts[0].strip() if parts else ""
            if not obj_name:
                continue
            type_name = parts[2].strip() if len(parts) >= 3 else ""

            # Count cosmic signatures (#145)
            if "cosmic signature" in (type_name or obj_name).lower():
                sig_count += 1

            current_scan.add(obj_name)
            if type_name:
                current_types.add(type_name)
            # Ship type (col 2) is the reliable classification source; the name
            # (col 0) is often a custom ship name that matches no keyword (#101).
            tier = classify_entry(type_name) if type_name else "unknown"
            if tier == "unknown":
                tier = classify_entry(obj_name)

            # Fine-grained class: prefer type column, fall back to name
            threat_class = classify_ship(type_name) if type_name else ShipThreatClass.UNKNOWN
            if threat_class == ShipThreatClass.UNKNOWN:
                threat_class = classify_ship(obj_name)

            entry = DscanEntry(
                name=obj_name, tier=tier, threat_class=threat_class,
                timestamp=time.time(), appeared=True
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
                    self._on_threat(tier, obj_name, threat_class)
                except Exception:
                    pass
        return current_scan, current_types, probe_detected, sig_count

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
