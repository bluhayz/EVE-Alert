"""Intel channel message parser for EVE Alert (#142).

Parses the natural-language messages that appear in regional intel channels
(e.g. Delve Intel, TAPI Intel) and extracts actionable fields.

EVE intel line format
---------------------
[ 2024.05.01 15:30:22 ] Pilot Name > message text here

Typical message patterns (community-standard shorthands):
  "D7-ZAC scimitar 2"       — 2 scimitars in D7-ZAC
  "K-6 clear" / "K-6 clr"  — K-6 system clear of hostiles
  "3V8-LK 1 recon"          — 1 recon in 3V8-LK
  "local 5 Ashab"           — 5 in local

This parser does a best-effort extraction — EVE intel is free-text and there
is no standard format.  False positives are acceptable; false negatives are
not (we prefer over-alerting).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import NamedTuple


# ------------------------------------------------------------------
# Line-level header parsing
# ------------------------------------------------------------------

# Matches the EVE chat log header: [ YYYY.MM.DD HH:MM:SS ] Name > text
_HEADER_RE = re.compile(
    r"^\[\s*\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2}\s*\]\s+"
    r"(?P<pilot>[^>]+?)\s*>\s*(?P<message>.+)$"
)


def _strip_header(line: str) -> tuple[str, str] | None:
    """Return (pilot_name, message) or None if the line is not a chat message."""
    m = _HEADER_RE.match(line.strip())
    if not m:
        return None
    return m.group("pilot"), m.group("message").strip()


# ------------------------------------------------------------------
# System-name detection
# ------------------------------------------------------------------

# EVE system names are 3–8 chars: letters, digits, hyphens.
# Region-specific patterns: null-sec look like "3V8-LK", "D7-ZAC", "1DQ1-A".
# We accept any token that looks like a plausible system name
# (all-caps + hyphen, or standard null-sec format).
_SYSTEM_RE = re.compile(
    r"\b([A-Z0-9]{2,5}-[A-Z0-9]{1,5}|[A-Z][A-Z0-9]{2,7})\b"
)


def _find_system(message: str) -> str | None:
    """Return the first token that looks like an EVE system name, or None."""
    # Skip common false-positives
    skip = {"WH", "WH1", "WH2", "T3", "CSP", "ESI", "KOS", "KMS", "GMT",
            "BLOPS", "HIC", "SABRE", "VNI", "ORCA"}
    for m in _SYSTEM_RE.finditer(message):
        token = m.group(1)
        if token not in skip and len(token) >= 4:
            return token
    return None


# ------------------------------------------------------------------
# Clear-signal detection
# ------------------------------------------------------------------

_CLEAR_WORDS = {"clear", "clr", "cleared", "no contact", "all clear"}


def _is_clear(message: str) -> bool:
    lower = message.lower()
    return any(word in lower for word in _CLEAR_WORDS)


# ------------------------------------------------------------------
# Hostile count detection
# ------------------------------------------------------------------

_COUNT_RE = re.compile(r"\b([1-9][0-9]*)\b")


def _hostile_count(message: str) -> int:
    """Return the first explicit number in the message, or 1 if none found."""
    # Ignore "clear" messages
    if _is_clear(message):
        return 0
    m = _COUNT_RE.search(message)
    return int(m.group(1)) if m else 1


# ------------------------------------------------------------------
# Ship mention detection
# ------------------------------------------------------------------

_KNOWN_SHIPS = [
    "titan", "supercarrier", "super carrier", "carrier", "dreadnought",
    "force auxiliary", "fax", "battleship", "battlecruiser", "cruiser",
    "destroyer", "frigate", "recon", "hic", "interdictor", "sabre",
    "blops", "black ops", "bomber", "covert ops", "interceptor",
    "rorqual", "orca", "vni", "ishtar", "tengu", "stratios",
]


def _find_ships(message: str) -> list[str]:
    lower = message.lower()
    return [s for s in _KNOWN_SHIPS if s in lower]


# ------------------------------------------------------------------
# IntelReport
# ------------------------------------------------------------------

@dataclass
class IntelReport:
    pilot: str
    raw_line: str
    system: str | None        # best-guess system name (may be None)
    hostile_count: int         # 0 = clear, ≥1 = hostile count
    is_clear: bool
    ships: list[str] = field(default_factory=list)
    jump_distance: int | None = None   # filled in asynchronously


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def parse_line(line: str) -> IntelReport | None:
    """Parse an EVE chat log line and return an IntelReport, or None.

    Returns None for non-chat lines (system messages, empty lines, etc.).
    """
    result = _strip_header(line)
    if result is None:
        return None

    pilot, message = result

    # Skip EVE system messages (italics in client, plain in log)
    if pilot.lower() in {"eve system", "evemail"}:
        return None

    clear = _is_clear(message)
    return IntelReport(
        pilot=pilot,
        raw_line=line,
        system=_find_system(message.upper()),
        hostile_count=0 if clear else _hostile_count(message),
        is_clear=clear,
        ships=_find_ships(message),
    )
