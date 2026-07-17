"""Analytics over the persistent pilot-sighting store (#216, v7.0).

Turns raw Sighting rows (evealert.tools.pilot_history_store) into a
human-readable summary of how often a pilot has been seen, where, in what
ship, and roughly when -- surfaced on Enemy alarms so accumulated history
becomes useful in the moment it matters, not just data sitting in a
database.
"""

import time
from collections import Counter
from dataclasses import dataclass

from evealert.tools.pilot_history_store import Sighting, get_sightings

# A single historical row isn't a "pattern," it's noise on someone's first
# sighting -- require at least this many before summarizing.
MIN_SIGHTINGS_FOR_SUMMARY = 3

_ACTIVE_WINDOW_HOURS = 3  # width of the "most active" time-of-day window


@dataclass
class PilotSummary:
    pilot_name: str
    sighting_count: int
    first_seen: float
    last_seen: float
    top_systems: list[tuple[str, int]]  # [(system, count), ...], most common first
    top_ship: str | None
    active_hour_range: str | None  # e.g. "19:00-22:00" (UTC == EVE time), or None


def summarize(
    pilot_name: str, *, sightings: list[Sighting] | None = None
) -> PilotSummary | None:
    """Return a PilotSummary for *pilot_name*, or None when there are
    fewer than MIN_SIGHTINGS_FOR_SUMMARY stored sightings.

    *sightings*, if given, is used directly instead of querying the store
    -- lets callers (and tests) supply synthetic data without touching the
    real DB.
    """
    if sightings is None:
        sightings = get_sightings(pilot_name, limit=1000)
    if len(sightings) < MIN_SIGHTINGS_FOR_SUMMARY:
        return None

    seen_ats = [s.seen_at for s in sightings]
    system_counts = Counter(s.system for s in sightings if s.system)
    ship_counts = Counter(s.ship for s in sightings if s.ship)
    top_ship = ship_counts.most_common(1)[0][0] if ship_counts else None

    return PilotSummary(
        pilot_name=pilot_name,
        sighting_count=len(sightings),
        first_seen=min(seen_ats),
        last_seen=max(seen_ats),
        top_systems=system_counts.most_common(3),
        top_ship=top_ship,
        active_hour_range=_infer_active_hour_range(seen_ats),
    )


def _infer_active_hour_range(seen_ats: list[float]) -> str | None:
    """Bucket sightings by UTC hour (EVE time is UTC) and return the
    _ACTIVE_WINDOW_HOURS-wide contiguous window (wrapping past midnight)
    with the most sightings, as "HH:00-HH:00". None if there's no data.
    """
    if not seen_ats:
        return None
    hour_counts = [0] * 24
    for t in seen_ats:
        hour_counts[time.gmtime(t).tm_hour] += 1

    best_start, best_count = 0, -1
    for start in range(24):
        count = sum(
            hour_counts[(start + i) % 24] for i in range(_ACTIVE_WINDOW_HOURS)
        )
        if count > best_count:
            best_start, best_count = start, count
    if best_count <= 0:
        return None
    end = (best_start + _ACTIVE_WINDOW_HOURS) % 24
    return f"{best_start:02d}:00-{end:02d}:00"


def format_summary(summary: PilotSummary) -> str:
    """Render a PilotSummary as one human-readable line, e.g.:
    "14 sightings over 45d — mostly in J5A-IX (9x), 1DQ1-A (3x); usually
    flies Loki; most active 19:00-22:00"
    """
    days = max(1, round((summary.last_seen - summary.first_seen) / 86400))
    headline = f"{summary.sighting_count} sightings over {days}d"

    details: list[str] = []
    if summary.top_systems:
        systems_str = ", ".join(f"{s} ({n}x)" for s, n in summary.top_systems[:2])
        details.append(f"mostly in {systems_str}")
    if summary.top_ship:
        details.append(f"usually flies {summary.top_ship}")
    if summary.active_hour_range:
        details.append(f"most active {summary.active_hour_range}")

    if not details:
        return headline
    return f"{headline} — " + "; ".join(details)


# ---------------------------------------------------------------------------
# Pathing inference (#217, v7.0)
# ---------------------------------------------------------------------------

# A gap longer than this starts a new "session" -- pathing is inferred from
# consecutive-in-time system changes, not sightings that are actually
# unrelated visits weeks apart.
SESSION_GAP_HOURS = 4

# A transition must repeat at least this many times before it's reported --
# per this milestone's design, a wrong pathing guess is worse than none.
MIN_TRANSITION_COUNT = 3

# A transition between systems farther apart than this (by in-game jumps)
# in a single session gap is more likely two unrelated sightings than a
# real, continuous path.
MAX_PLAUSIBLE_JUMPS = 5


@dataclass
class PathingSummary:
    pilot_name: str
    home_system: str
    top_transitions: list[tuple[tuple[str, str], int]]  # [((from, to), count), ...]


async def infer_pathing(
    pilot_name: str,
    *,
    sightings: list[Sighting] | None = None,
    cache=None,
) -> PathingSummary | None:
    """Infer a pilot's home system and most common system-to-system
    transitions from their sighting history.

    Returns None when there isn't enough repeated-transition evidence to
    make a confident claim, rather than a low-confidence guess.

    *sightings*, if given, is used instead of querying the store.
    *cache*, if given, is used instead of the real UniverseCache singleton
    for the jump-plausibility cross-check -- both let callers (and tests)
    supply synthetic/fake data without touching the real DB or network.
    """
    if sightings is None:
        sightings = get_sightings(pilot_name, limit=1000)
    if not sightings:
        return None

    ordered = sorted(sightings, key=lambda s: s.seen_at)
    sessions = _group_into_sessions(ordered)

    transitions: Counter = Counter()
    for session in sessions:
        systems_in_session = [s.system for s in session if s.system]
        for a, b in zip(systems_in_session, systems_in_session[1:]):
            if a != b:
                transitions[(a, b)] += 1

    candidates = [
        (pair, count)
        for pair, count in transitions.most_common()
        if count >= MIN_TRANSITION_COUNT
    ]
    if not candidates:
        return None

    if cache is None:
        try:
            from evealert.tools.universe import get_universe_cache  # noqa: PLC0415

            cache = get_universe_cache()
        except Exception:
            cache = None

    plausible: list[tuple[tuple[str, str], int]] = []
    for pair, count in candidates:
        if cache is None or await _is_plausible_transition(cache, *pair):
            plausible.append((pair, count))
    if not plausible:
        return None

    system_counts = Counter(s.system for s in sightings if s.system)
    if not system_counts:
        return None
    home_system = system_counts.most_common(1)[0][0]

    return PathingSummary(
        pilot_name=pilot_name,
        home_system=home_system,
        top_transitions=plausible[:2],
    )


def _group_into_sessions(ordered_sightings: list[Sighting]) -> list[list[Sighting]]:
    """Split chronologically-ordered sightings into sessions, where a gap
    of more than SESSION_GAP_HOURS starts a new session."""
    if not ordered_sightings:
        return []
    sessions = [[ordered_sightings[0]]]
    for prev, curr in zip(ordered_sightings, ordered_sightings[1:]):
        gap_hours = (curr.seen_at - prev.seen_at) / 3600.0
        if gap_hours > SESSION_GAP_HOURS:
            sessions.append([curr])
        else:
            sessions[-1].append(curr)
    return sessions


async def _is_plausible_transition(cache, system_a: str, system_b: str) -> bool:
    """Best-effort cross-check against the jump graph: a transition
    farther than MAX_PLAUSIBLE_JUMPS is more likely two unrelated
    sightings than a real path.

    Any lookup failure defaults to "plausible" -- this is a soft filter,
    not a hard requirement, and shouldn't discard a real signal over an
    ESI hiccup or an unresolvable system name.
    """
    try:
        id_a = await cache.get_system_id(system_a)
        id_b = await cache.get_system_id(system_b)
        if not id_a or not id_b:
            return True
        route = await cache.get_route(id_a, id_b)
        if route is None:
            return True
        return (len(route) - 1) <= MAX_PLAUSIBLE_JUMPS
    except Exception:
        return True


def format_pathing(pathing: PathingSummary) -> str:
    """Render a PathingSummary as a trailing segment for the History line,
    e.g. "home J5A-IX; often J5A-IX -> 1DQ1-A"."""
    parts = [f"home {pathing.home_system}"]
    transition_strs = [f"{a} -> {b}" for (a, b), _count in pathing.top_transitions]
    if transition_strs:
        parts.append(f"often moves {', '.join(transition_strs)}")
    return "; ".join(parts)
