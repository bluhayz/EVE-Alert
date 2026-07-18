"""Pilot combat dossier engine for EVE Alert (#241, v7.4).

One call answers "who is this pilot operationally": what they fly (with
frequencies), where they hunt, when they're active, how big their gangs
are, and who they fly with. Built on the v7.3 data foundation
(combat_activity_store, pilot_history_store, intel_rollups).

Two-tier read strategy so a dossier lookup on the alarm path never blocks
on a full-history scan:
  - The full-history aggregates (top ships/systems, hour histogram, avg
    gang size, kill/loss counts, last-active) prefer the cached
    intel_rollups.PilotRollup (get_pilot_rollup_nonblocking -- never
    recomputes inline) when one is already stored.
  - Fields the rollup doesn't carry (fleetmates, solo %) -- and the
    rollup-fallback path when no rollup is stored yet -- come from a
    single bounded, time-windowed combat_activity read (recent window,
    capped row count), never an unbounded scan.
"""

import time
from collections import Counter
from dataclasses import dataclass

from evealert.tools import combat_activity_store, intel_rollups
from evealert.tools.pilot_history_analytics import (
    PathingSummary,
    PilotSummary,
    infer_pathing,
    summarize,
)
from evealert.tools.pilot_history_store import get_sightings

# #241: a fleetmate must share at least this many killmails before being
# reported as a regular associate -- one shared kill is coincidence.
_MIN_SHARED_KILLS_FOR_FLEETMATE = 3
_TOP_N_FLEETMATES = 3

# Bounded, time-windowed read used both as the rollup fallback and as the
# source for fields the rollup never carries (fleetmates, solo %) -- keeps
# this alarm-path-safe regardless of how much history a pilot has.
_RECENT_ACTIVITY_WINDOW_SECONDS = 90 * 86400  # 90d
_RECENT_ACTIVITY_LIMIT = 500

_PRIME_WINDOW_HOURS = 3  # width of the "prime time" window, matches #216's ACTIVE_WINDOW_HOURS

_DOSSIER_LINE_MAX_CHARS = 140


@dataclass
class PilotDossier:
    pilot_name: str
    top_ships: list[tuple[str, float]]  # [(name, pct 0-100), ...]
    top_hunt_systems: list[tuple[str, int]]  # [(name, kill_count), ...]
    active_hours: list[int]  # 24-bucket UTC histogram
    prime_window: str | None  # e.g. "19:00-22:00"
    avg_gang_size: float | None
    solo_pct: float | None
    frequent_fleetmates: list[tuple[str, int]]  # [(name, shared_kill_count), ...]
    sighting_summary: PilotSummary | None
    pathing: PathingSummary | None
    kill_loss_ratio: float | None
    last_active: float | None


async def build_dossier(
    pilot_name: str, *, own_character_name: str | None = None
) -> PilotDossier | None:
    """Build a combat dossier for *pilot_name*, or None when there's no
    sighting or combat-activity history at all.

    *own_character_name*, when known, is excluded from the fleetmate list
    -- the user's own character is never a "fleetmate" of a hostile.
    """
    since = time.time() - _RECENT_ACTIVITY_WINDOW_SECONDS
    recent_activity = combat_activity_store.get_activity(
        pilot_name, since=since, limit=_RECENT_ACTIVITY_LIMIT
    )
    sightings = get_sightings(pilot_name, limit=1000)

    rollup = intel_rollups.get_pilot_rollup_nonblocking(pilot_name)
    if rollup is not None:
        top_ships = _ships_with_pct(rollup.top_ships, rollup.kill_count + rollup.loss_count)
        top_hunt_systems = rollup.top_systems
        active_hours = rollup.hour_histogram
        kill_count = rollup.kill_count
        loss_count = rollup.loss_count
        last_active_candidates = [rollup.last_active_at] if rollup.last_active_at else []
    else:
        # Graceful fallback: no rollup stored yet (e.g. first-ever
        # encounter with this pilot) -- derive the same aggregates from
        # the bounded recent-activity sample instead of leaving the
        # dossier empty.
        ship_counts = Counter(a.ship_name for a in recent_activity if a.ship_name)
        system_counts = Counter(a.system_name for a in recent_activity if a.system_name)
        active_hours = [0] * 24
        for a in recent_activity:
            active_hours[time.gmtime(a.occurred_at).tm_hour] += 1
        kill_count = sum(1 for a in recent_activity if a.role == "attacker")
        loss_count = sum(1 for a in recent_activity if a.role == "victim")
        total = kill_count + loss_count
        top_ships = _ships_with_pct(ship_counts.most_common(5), total)
        top_hunt_systems = system_counts.most_common(5)
        last_active_candidates = [a.occurred_at for a in recent_activity]

    last_active_candidates += [s.seen_at for s in sightings]
    last_active = max(last_active_candidates) if last_active_candidates else None

    # rollup is only ever non-None when it's already known non-empty (see
    # intel_rollups._is_empty_pilot_rollup) -- so a dossier is buildable
    # whenever there's a rollup, OR recent combat activity, OR any
    # sighting at all (a pilot seen in Local but never fought still gets
    # a dossier built around their sighting_summary).
    if rollup is None and not recent_activity and not sightings:
        return None

    gang_sizes = [a.gang_size for a in recent_activity if a.gang_size is not None]
    if gang_sizes:
        avg_gang_size = sum(gang_sizes) / len(gang_sizes)
        solo_pct = 100.0 * sum(1 for g in gang_sizes if g <= 1) / len(gang_sizes)
    elif rollup is not None:
        avg_gang_size = rollup.avg_gang_size
        solo_pct = None
    else:
        avg_gang_size = None
        solo_pct = None

    kill_loss_ratio = (kill_count / loss_count) if loss_count > 0 else None

    frequent_fleetmates = _infer_fleetmates(
        pilot_name, recent_activity, own_character_name=own_character_name
    )

    sighting_summary = summarize(pilot_name, sightings=sightings)
    pathing = await infer_pathing(pilot_name, sightings=sightings)

    return PilotDossier(
        pilot_name=pilot_name,
        top_ships=top_ships,
        top_hunt_systems=top_hunt_systems,
        active_hours=active_hours,
        prime_window=_prime_window_from_histogram(active_hours),
        avg_gang_size=avg_gang_size,
        solo_pct=solo_pct,
        frequent_fleetmates=frequent_fleetmates,
        sighting_summary=sighting_summary,
        pathing=pathing,
        kill_loss_ratio=kill_loss_ratio,
        last_active=last_active,
    )


def _ships_with_pct(
    counts: list[tuple[str, int]], total: int
) -> list[tuple[str, float]]:
    if total <= 0:
        return []
    return [(name, round(100.0 * count / total, 1)) for name, count in counts]


def _infer_fleetmates(
    pilot_name: str,
    activity: list[combat_activity_store.CombatActivityRow],
    *,
    own_character_name: str | None = None,
) -> list[tuple[str, int]]:
    """Pilots sharing >= _MIN_SHARED_KILLS_FOR_FLEETMATE killmails as a
    fellow attacker with *pilot_name*, within the same recent-activity
    window already fetched by build_dossier."""
    attacker_kill_ids = [a.killmail_id for a in activity if a.role == "attacker"]
    if not attacker_kill_ids:
        return []
    co_rows = combat_activity_store.get_co_attackers(attacker_kill_ids)
    shared: Counter = Counter()
    exclude = {pilot_name}
    if own_character_name:
        exclude.add(own_character_name)
    for _kill_id, other_name in co_rows:
        if other_name in exclude:
            continue
        shared[other_name] += 1
    return [
        (name, count)
        for name, count in shared.most_common()
        if count >= _MIN_SHARED_KILLS_FOR_FLEETMATE
    ][:_TOP_N_FLEETMATES]


def _prime_window_from_histogram(histogram: list[int]) -> str | None:
    """Same sliding-window approach as pilot_history_analytics's
    _infer_active_hour_range, but over an already-computed hourly
    histogram rather than raw sighting timestamps."""
    if not histogram or not any(histogram):
        return None
    best_start, best_count = 0, -1
    for start in range(24):
        count = sum(histogram[(start + i) % 24] for i in range(_PRIME_WINDOW_HOURS))
        if count > best_count:
            best_start, best_count = start, count
    if best_count <= 0:
        return None
    end = (best_start + _PRIME_WINDOW_HOURS) % 24
    return f"{best_start:02d}:00-{end:02d}:00"


def format_dossier_line(dossier: PilotDossier) -> str:
    """Render one alarm-ready line, e.g.:
    "Sabre 58%/Loki 21% | hunts D7-ZAC (14 kills) | prime 19:00-22:00 EVE
    | gang ~4 (solo 12%)"

    Capped at ~_DOSSIER_LINE_MAX_CHARS; segments with no data are omitted
    entirely rather than shown empty.
    """
    segments: list[str] = []

    if dossier.top_ships:
        ships_str = "/".join(f"{name} {pct:.0f}%" for name, pct in dossier.top_ships[:2])
        segments.append(ships_str)

    if dossier.top_hunt_systems:
        system, count = dossier.top_hunt_systems[0]
        segments.append(f"hunts {system} ({count} kills)")

    if dossier.prime_window:
        segments.append(f"prime {dossier.prime_window} EVE")

    if dossier.avg_gang_size is not None:
        gang_str = f"gang ~{dossier.avg_gang_size:.0f}"
        if dossier.solo_pct is not None:
            gang_str += f" (solo {dossier.solo_pct:.0f}%)"
        segments.append(gang_str)

    line = " | ".join(segments)
    while len(line) > _DOSSIER_LINE_MAX_CHARS and segments:
        segments.pop()
        line = " | ".join(segments)
    return line
