"""Intel channel message parser for EVE Alert (#142).

Parses the natural-language messages that appear in regional intel channels
(e.g. Delve Intel, TAPI Intel) and extracts actionable fields.

EVE intel line format
---------------------
[ 2024.05.01 15:30:22 ] Pilot Name > message text here

Typical message patterns (community-standard shorthands):
  "D7-ZAC scimitar 2"       -- 2 scimitars in D7-ZAC
  "K-6 clear" / "K-6 clr"  -- K-6 system clear of hostiles
  "3V8-LK 1 recon"          -- 1 recon in 3V8-LK
  "local 5 Ashab"           -- 5 in local

This parser does a best-effort extraction -- EVE intel is free-text and there
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

# EVE system names are 3-8 chars: letters, digits, hyphens.
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
    # Capital ships
    "titan", "supercarrier", "super carrier", "carrier", "dreadnought",
    "force auxiliary", "fax", "rorqual", "orca",
    # Combat ships
    "battleship", "battlecruiser", "cruiser", "destroyer", "frigate",
    "recon", "hic", "interdictor", "sabre",
    "blops", "black ops", "bomber", "covert ops", "interceptor",
    # T3 cruisers
    "proteus", "tengu", "legion", "loki",
    # Common named ships
    "vni", "ishtar", "stratios", "cerberus", "orthrus", "vigilant",
    "cynabal", "vagabond", "hurricane", "muninn", "ferox", "caracal",
    "vedmak", "kikimora", "nergal", "ikitursa",
    "lachesis", "curse", "arazu", "rapier", "huginn",
    "falcon", "rook", "pilgrim", "sentinel",
    "broadsword", "devoter", "phobos", "onyx",
    "flycatcher", "heretic", "eris",
    "machariel", "nightmare", "bhaalgorn", "vindicator",
]


def _find_ships(message: str) -> list[str]:
    lower = message.lower()
    return [s for s in _KNOWN_SHIPS if s in lower]


# ------------------------------------------------------------------
# Hostile pilot name extraction (#197)
# ------------------------------------------------------------------

# Common intel shorthand tokens to ignore when extracting pilot names
_INTEL_SKIP_WORDS = frozenset({
    "nv", "clr", "clrn", "clear", "cleared", "all", "no", "contact",
    "wt", "pos", "xx", "xxx", "local", "camp", "camping", "gate",
    "roam", "warp", "in", "out", "jump", "jumped", "down", "up", "on",
    "plus", "more", "intel", "spotted", "seen", "sighted", "gg", "o7",
    "and", "the", "is", "was", "at", "by", "of", "to", "be",
})

# Token that looks like a pilot name component: starts Capital, min 2 chars
_PILOT_TOKEN_RE = re.compile(r"^[A-Z][A-Za-z0-9'\-]{1,}$")


def _find_mentioned_pilots(message: str, system: str | None, ships: list[str]) -> list[str]:
    """Return candidate hostile pilot names found in *message* body.

    Best-effort: excludes the detected system name, known ship types, and
    common intel shorthands. Consecutive TitleCase tokens are grouped.
    """
    ship_lower = {s.lower() for s in ships}
    skip: set[str] = set(_INTEL_SKIP_WORDS) | ship_lower | {s.lower() for s in _KNOWN_SHIPS}
    if system:
        skip.add(system.lower())

    name_run: list[str] = []
    results: list[str] = []

    for raw in message.split():
        tok = raw.strip(".,!?;:[]()\"'")
        tl = tok.lower()

        # Reject: skip-word, empty, pure-number, digit-led, all-caps, too short
        if (
            not tok
            or tl in skip
            or tok.isdigit()
            or tok[0].isdigit()
            or tok.isupper()
            or len(tok) < 2
        ):
            if name_run:
                results.append(" ".join(name_run))
                name_run = []
            continue

        if _PILOT_TOKEN_RE.match(tok):
            name_run.append(tok)
        else:
            if name_run:
                results.append(" ".join(name_run))
                name_run = []

    if name_run:
        results.append(" ".join(name_run))

    # Deduplicate and enforce minimum length (3 chars)
    seen: set[str] = set()
    out: list[str] = []
    for name in results:
        if len(name) >= 3 and name not in seen:
            seen.add(name)
            out.append(name)
    return out


# ------------------------------------------------------------------
# IntelReport
# ------------------------------------------------------------------

@dataclass
class IntelReport:
    pilot: str
    raw_line: str
    system: str | None         # best-guess system name (may be None)
    hostile_count: int          # 0 = clear, >=1 = hostile count
    is_clear: bool
    ships: list[str] = field(default_factory=list)
    jump_distance: int | None = None   # filled in asynchronously
    mentioned_pilots: list[str] = field(default_factory=list)  # hostile names in body (#197)


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
    system = _find_system(message.upper())
    ships = _find_ships(message)
    mentioned = [] if clear else _find_mentioned_pilots(message, system, ships)
    return IntelReport(
        pilot=pilot,
        raw_line=line,
        system=system,
        hostile_count=0 if clear else _hostile_count(message),
        is_clear=clear,
        ships=ships,
        mentioned_pilots=mentioned,
    )
