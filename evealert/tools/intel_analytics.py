"""Backend queries for the Intel Analytics UI (#244, v7.4).

Kept separate from evealert/ui/intel_analytics_window.py so the search
and ranking logic here is unit-testable without PySide6 -- the UI module
calls these on a worker thread (never the Qt thread) and renders the
result via a Signal, the same pattern statistics_window.py's threat
heatmap tab already uses.
"""

import time
from collections import Counter
from dataclasses import dataclass

from evealert.tools import combat_activity_store, pilot_history_store

_TOP_HOSTILES_WINDOW_SECONDS = 30 * 86400
_TOP_HOSTILES_DEFAULT_LIMIT = 20
_TOP_HOSTILES_QUERY_LIMIT = 500
# A pilot's "recent" half of the window counting notably more than their
# "older" half (or vice versa) is a real trend, not noise from one extra
# sighting.
_TREND_RATIO_THRESHOLD = 1.2


@dataclass
class TopHostileEntry:
    pilot_name: str
    corp: str | None
    encounters: int  # sightings + combat_activity rows in the window
    top_ship: str | None
    last_seen: float
    score: float  # recency-weighted encounter score, higher = more urgent
    trend: str  # "up" | "down" | "flat"


def search_pilot_names(query: str, limit: int = 50) -> list[str]:
    """Case-insensitive partial-match pilot name search across both the
    sighting store and the combat-activity store, deduplicated and
    alphabetically sorted. Returns [] for an empty/whitespace query."""
    if not query.strip():
        return []
    names = set(pilot_history_store.search_pilot_names(query, limit=limit))
    names |= set(combat_activity_store.search_pilot_names(query, limit=limit))
    return sorted(names, key=str.lower)[:limit]


def top_hostiles(limit: int = _TOP_HOSTILES_DEFAULT_LIMIT) -> list[TopHostileEntry]:
    """Rank recently-encountered pilots by a recency-weighted score over
    sightings + combat_activity rows in the last 30 days -- a pilot seen
    once yesterday outranks one seen five times a month ago."""
    since = time.time() - _TOP_HOSTILES_WINDOW_SECONDS
    sighting_names = {
        name for name, _ in pilot_history_store.get_pilots_with_activity_since(
            since, limit=_TOP_HOSTILES_QUERY_LIMIT
        )
    }
    combat_names = {
        name for name, _ in combat_activity_store.get_pilots_with_activity_since(
            since, limit=_TOP_HOSTILES_QUERY_LIMIT
        )
    }
    entries: list[TopHostileEntry] = []
    for name in sighting_names | combat_names:
        sightings = pilot_history_store.get_sightings(name, since=since, limit=200)
        activity = combat_activity_store.get_activity(name, since=since, limit=200)
        if not sightings and not activity:
            continue

        times = [s.seen_at for s in sightings] + [a.occurred_at for a in activity]
        last_seen = max(times)
        score = sum(_recency_weight(t, since) for t in times)

        ship_counts = Counter(a.ship_name for a in activity if a.ship_name)
        top_ship = ship_counts.most_common(1)[0][0] if ship_counts else None
        corp = pilot_history_store.get_latest_corp_for_pilot(name)

        entries.append(TopHostileEntry(
            pilot_name=name, corp=corp, encounters=len(sightings) + len(activity),
            top_ship=top_ship, last_seen=last_seen, score=score,
            trend=_trend_for(times, since),
        ))

    entries.sort(key=lambda e: e.score, reverse=True)
    return entries[:limit]


def _recency_weight(occurred_at: float, since: float) -> float:
    """Linear decay from 1.0 (right now) to 0.1 (the edge of the window),
    so an encounter's contribution to the score fades with age rather
    than counting every encounter in the window equally."""
    age_frac = max(0.0, min(1.0, (occurred_at - since) / _TOP_HOSTILES_WINDOW_SECONDS))
    return 0.1 + 0.9 * age_frac


def _trend_for(times: list[float], since: float) -> str:
    midpoint = since + (_TOP_HOSTILES_WINDOW_SECONDS / 2)
    recent = sum(1 for t in times if t >= midpoint)
    older = sum(1 for t in times if t < midpoint)
    if older == 0:
        return "up" if recent > 0 else "flat"
    if recent >= older * _TREND_RATIO_THRESHOLD:
        return "up"
    if older >= recent * _TREND_RATIO_THRESHOLD:
        return "down"
    return "flat"
