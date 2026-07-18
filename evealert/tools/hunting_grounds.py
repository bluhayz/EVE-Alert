"""Hunting-ground analytics for EVE Alert (#242, v7.4).

Elevates analytics from single pilots (#241's dossier engine) to hostile
GROUPS ("Snuffed Out camps this pipe 1900-2200") and to SYSTEMS ("this
system's danger window is opening now") -- built entirely from our own
recorded combat_activity/pilot_history data, not live zKB pulls (the
existing evealert.tools.threat_heatmap stays as the zKB-backed route-
planning fallback, #148).

Zero network I/O beyond the (already-cached) UniverseCache jump-graph
lookup that system_danger_windows() needs to find neighboring systems --
this module itself never imports httpx or makes an HTTP request.
"""

import logging
import time
from collections import Counter
from dataclasses import dataclass

logger = logging.getLogger("alert.hunting_grounds")

_GROUP_ACTIVITY_WINDOW_SECONDS = 30 * 86400  # 30d, matches #239's system_rollup window
_GROUP_ACTIVITY_TREND_WINDOW_SECONDS = 7 * 86400
_GROUP_ACTIVITY_TOP_N = 5
# A daily kill rate at least this much higher/lower than the 30d baseline
# counts as a real trend rather than day-to-day noise.
_TREND_RATIO_THRESHOLD = 1.2

_DANGER_WINDOW_LOOKBACK_SECONDS = 30 * 86400
_DANGER_WINDOW_NEIGHBOR_JUMPS = 2
# Below this many combined data points, a "top quartile hour" claim is
# just noise from a handful of kills -- withhold danger_now entirely.
_DANGER_WINDOW_MIN_DATA_POINTS = 10
_HOT_WINDOW_HOURS = 3  # width of the descriptive "historically hot" window


@dataclass
class GroupActivity:
    group_name: str
    top_systems: list[tuple[str, int]]  # [(system, kill_count), ...]
    hour_histogram: list[int]  # 24-bucket UTC histogram, attacker-role kills
    top_pilots: list[tuple[str, int]]  # [(pilot_name, kill_count), ...]
    avg_gang_size: float | None
    kills_7d: int
    kills_30d: int
    trend: str  # "rising" | "falling" | "steady" | "insufficient data"


@dataclass
class SystemDanger:
    system_name: str
    hour_histogram: list[int]  # aggregated across system + neighbors within 2 jumps
    current_hour_percentile: float  # 0-100; 100 = the single busiest hour of the day
    danger_now: bool
    hot_window: str | None = None  # e.g. "19:00-22:00" -- busiest _HOT_WINDOW_HOURS window
    hot_window_pct: float | None = None  # % of tracked activity within hot_window


def group_activity(corp_or_alliance_name: str) -> GroupActivity | None:
    """Return hunting-ground analytics for *corp_or_alliance_name*, or
    None when no tracked pilot is known to belong to it (no local
    sighting ever recorded that corp/alliance) or none of them have any
    recorded kills.

    Pilots are resolved via pilot_history_store's sighting corp/alliance
    fields (combat_activity itself doesn't carry corp/alliance -- #237's
    schema is killmail-shaped) -- the same cross-reference #239's
    system_rollup uses for top_hostile_corps.
    """
    from evealert.tools.combat_activity_store import get_activity_for_pilots  # noqa: PLC0415
    from evealert.tools.pilot_history_store import get_pilots_by_corp_or_alliance  # noqa: PLC0415

    pilots = get_pilots_by_corp_or_alliance(corp_or_alliance_name)
    if not pilots:
        return None

    since = time.time() - _GROUP_ACTIVITY_WINDOW_SECONDS
    rows = get_activity_for_pilots(pilots, since=since, limit=5000)
    attacker_rows = [r for r in rows if r.role == "attacker"]
    if not attacker_rows:
        return None

    system_counts = Counter(r.system_name for r in attacker_rows if r.system_name)
    pilot_counts = Counter(r.pilot_name for r in attacker_rows)
    hour_histogram = [0] * 24
    for r in attacker_rows:
        hour_histogram[time.gmtime(r.occurred_at).tm_hour] += 1

    gang_sizes = [r.gang_size for r in attacker_rows if r.gang_size is not None]
    avg_gang_size = (sum(gang_sizes) / len(gang_sizes)) if gang_sizes else None

    trend_since = time.time() - _GROUP_ACTIVITY_TREND_WINDOW_SECONDS
    kills_7d = sum(1 for r in attacker_rows if r.occurred_at >= trend_since)
    kills_30d = len(attacker_rows)

    return GroupActivity(
        group_name=corp_or_alliance_name,
        top_systems=system_counts.most_common(_GROUP_ACTIVITY_TOP_N),
        hour_histogram=hour_histogram,
        top_pilots=pilot_counts.most_common(_GROUP_ACTIVITY_TOP_N),
        avg_gang_size=avg_gang_size,
        kills_7d=kills_7d,
        kills_30d=kills_30d,
        trend=_classify_trend(kills_7d, kills_30d),
    )


def _classify_trend(kills_7d: int, kills_30d: int) -> str:
    if kills_30d == 0:
        return "insufficient data"
    rate_7d = kills_7d / 7.0
    rate_30d = kills_30d / 30.0
    if rate_30d == 0:
        return "rising" if rate_7d > 0 else "insufficient data"
    if rate_7d >= rate_30d * _TREND_RATIO_THRESHOLD:
        return "rising"
    if rate_7d <= rate_30d / _TREND_RATIO_THRESHOLD:
        return "falling"
    return "steady"


async def system_danger_windows(system_name: str, *, cache=None) -> SystemDanger:
    """Return danger-window analytics for *system_name*, aggregating its
    own combat_activity plus that of systems within
    _DANGER_WINDOW_NEIGHBOR_JUMPS jumps (the fixed jump graph, #227).

    *cache*, if given, replaces the real UniverseCache singleton for the
    neighbor lookup -- lets callers (and tests) supply a fake without
    touching ESI. Any lookup failure just falls back to *system_name*
    alone rather than raising -- this is best-effort context, not a hard
    requirement.

    Always returns a SystemDanger (never None) -- even with zero data the
    caller may still want the shape (e.g. the analytics UI showing an
    empty state); danger_now is simply False below the confidence floor.
    """
    from evealert.tools.combat_activity_store import get_activity_by_system  # noqa: PLC0415

    if cache is None:
        try:
            from evealert.tools.universe import get_universe_cache  # noqa: PLC0415

            cache = get_universe_cache()
        except Exception:
            cache = None

    system_names = {system_name}
    if cache is not None:
        try:
            origin_id = await cache.get_system_id(system_name)
            if origin_id is not None:
                neighbor_ids = await cache.get_systems_within_jumps(
                    origin_id, _DANGER_WINDOW_NEIGHBOR_JUMPS
                )
                for neighbor_id in neighbor_ids:
                    name = await cache.get_system_name(neighbor_id)
                    if name:
                        system_names.add(name)
        except Exception as exc:
            logger.debug(
                "system_danger_windows: neighbor lookup failed for %s: %s", system_name, exc
            )

    since = time.time() - _DANGER_WINDOW_LOOKBACK_SECONDS
    hour_histogram = [0] * 24
    total = 0
    for name in system_names:
        for row in get_activity_by_system(name, since=since, limit=2000):
            hour_histogram[time.gmtime(row.occurred_at).tm_hour] += 1
            total += 1

    window_str, window_pct = hot_window(hour_histogram, total)

    if total < _DANGER_WINDOW_MIN_DATA_POINTS:
        return SystemDanger(
            system_name=system_name, hour_histogram=hour_histogram,
            current_hour_percentile=0.0, danger_now=False,
            hot_window=window_str, hot_window_pct=window_pct,
        )

    current_hour = time.gmtime().tm_hour
    current_count = hour_histogram[current_hour]
    busier_hours = sum(1 for v in hour_histogram if v > current_count)
    current_hour_percentile = 100.0 * (24 - busier_hours) / 24
    danger_now = current_count > 0 and current_hour_percentile >= 75.0

    return SystemDanger(
        system_name=system_name, hour_histogram=hour_histogram,
        current_hour_percentile=current_hour_percentile, danger_now=danger_now,
        hot_window=window_str, hot_window_pct=window_pct,
    )


def hot_window(histogram: list[int], total: int) -> tuple[str | None, float | None]:
    """Same sliding-window approach as pilot_dossier's
    _prime_window_from_histogram: the contiguous _HOT_WINDOW_HOURS-wide
    window (wrapping past midnight) with the most activity, plus what
    percent of total activity falls within it."""
    if not histogram or total <= 0:
        return None, None
    best_start, best_count = 0, -1
    for start in range(24):
        count = sum(histogram[(start + i) % 24] for i in range(_HOT_WINDOW_HOURS))
        if count > best_count:
            best_start, best_count = start, count
    if best_count <= 0:
        return None, None
    end = (best_start + _HOT_WINDOW_HOURS) % 24
    pct = round(100.0 * best_count / total, 1)
    return f"{best_start:02d}:00-{end:02d}:00", pct
