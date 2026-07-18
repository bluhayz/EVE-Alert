"""Analytics rollup layer for EVE Alert (#239, v7.3).

Dossier/hunting-ground queries (top ships, top systems, hourly
histograms, gang-size averages) over the raw pilot_history_store
sightings + combat_activity_store killmail rows are O(all history) per
query. This module precomputes and caches per-pilot and per-system
rollups in their own SQLite database so a dossier read on the alarm path
is a single small-row lookup, not a full history scan.

Two read paths, matching the "never block the alarm path" requirement:
  - get_pilot_rollup() -- synchronous, refreshes inline if stale/missing.
    For callers that want the ground truth now (analytics UI, tests).
  - get_pilot_rollup_nonblocking() -- always returns whatever's currently
    stored (possibly stale or None) immediately, and schedules a
    background recompute if it's stale/missing rather than computing
    inline. For the alarm path (v7.4/#243), which must never stall
    waiting on a full history scan.
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir

logger = logging.getLogger("alert.intel_rollups")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pilot_rollup (
    pilot_name TEXT PRIMARY KEY,
    sighting_count INTEGER NOT NULL,
    kill_count INTEGER NOT NULL,
    loss_count INTEGER NOT NULL,
    top_ships TEXT NOT NULL,
    top_systems TEXT NOT NULL,
    hour_histogram TEXT NOT NULL,
    avg_gang_size REAL,
    last_active_at REAL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS system_rollup (
    system_name TEXT PRIMARY KEY,
    hostile_kill_count_30d INTEGER NOT NULL,
    hour_histogram TEXT NOT NULL,
    top_hostile_corps TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""

# #239: how old a stored rollup can be before a read is considered stale
# and due for a refresh.
_ROLLUP_STALE_SECONDS = 15 * 60
# #239: the maintenance-task sweep only looks at pilots/systems with
# activity in this recent a window -- a full-history rescan every sweep
# would defeat the point of caching. A rollup for someone who hasn't
# shown up in 24h can't have gone stale (no new rows to reflect).
_SWEEP_LOOKBACK_SECONDS = 24 * 3600
_SWEEP_MAX_PILOTS = 50
_SYSTEM_ROLLUP_WINDOW_SECONDS = 30 * 86400  # "hostile_kill_count_30d"
_TOP_N = 5


def get_intel_rollups_path() -> str:
    """Return the path to the analytics-rollup SQLite database.

    When the EVEALERT_INTEL_ROLLUPS_PATH environment variable is set
    (e.g. in tests), that path is used instead of the platform config
    directory so tests never read or write the user's real data (mirrors
    EVEALERT_STATS_PATH/EVEALERT_PILOT_HISTORY_PATH).
    """
    override = os.environ.get("EVEALERT_INTEL_ROLLUPS_PATH")
    if override:
        return override
    config_dir = Path(user_config_dir("evealert"))
    config_dir.mkdir(parents=True, exist_ok=True)
    return str(config_dir / "intel_rollups.db")


# #239: executescript() implicitly commits and re-issues the schema DDL
# on every single connection -- under real concurrent access (engine loop
# + UI thread both opening connections in a tight window) that turned out
# to contend for the write lock badly enough to raise "database is
# locked" almost immediately despite the generous busy-timeout below
# (verified: the DDL re-run, not the actual row read/write, was the
# source of the contention). Track which DB paths have already had the
# schema applied so it only runs once per path per process.
_schema_initialized_paths: set = set()


def _connect() -> sqlite3.Connection:
    # A longer busy-timeout than sqlite3's 5s default -- rollups can be
    # written from both the engine loop (background sweep/refresh) and a
    # UI-triggered dossier read concurrently; WAL mode lets readers
    # proceed without blocking, but two near-simultaneous writers still
    # briefly contend for the single writer lock and should retry rather
    # than surface "database is locked" to the caller.
    path = get_intel_rollups_path()
    conn = sqlite3.connect(path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    if path not in _schema_initialized_paths:
        conn.executescript(_SCHEMA)
        _schema_initialized_paths.add(path)
    return conn


@dataclass
class PilotRollup:
    pilot_name: str
    sighting_count: int
    kill_count: int
    loss_count: int
    top_ships: list[tuple[str, int]]
    top_systems: list[tuple[str, int]]
    hour_histogram: list[int]
    avg_gang_size: float | None
    last_active_at: float | None
    updated_at: float


@dataclass
class SystemRollup:
    system_name: str
    hostile_kill_count_30d: int
    hour_histogram: list[int]
    top_hostile_corps: list[tuple[str, int]]
    updated_at: float


# ---------------------------------------------------------------------------
# Pure computation (the "ground truth" -- no caching, no stored state)
# ---------------------------------------------------------------------------


def compute_pilot_rollup(pilot_name: str) -> PilotRollup:
    """Brute-force recompute of *pilot_name*'s rollup from the raw
    sighting/combat-activity stores. Pure function (no I/O beyond the
    reads) so it's directly comparable against a stored rollup for
    correctness testing.

    combat_activity's ship_name always reflects what the TRACKED pilot
    themselves flew in that killmail (both attacker and victim rows --
    see #237), so top_ships/top_systems/hour_histogram/avg_gang_size are
    all killmail-derived: more meaningful for "what do they fly / where
    do they hunt" than Local sightings, which only say "was here."
    """
    from evealert.tools.combat_activity_store import get_activity  # noqa: PLC0415
    from evealert.tools.pilot_history_store import get_sightings  # noqa: PLC0415

    sightings = get_sightings(pilot_name, limit=1_000_000)
    activity = get_activity(pilot_name, limit=1_000_000)

    kill_count = sum(1 for a in activity if a.role == "attacker")
    loss_count = sum(1 for a in activity if a.role == "victim")

    ship_counts = Counter(a.ship_name for a in activity if a.ship_name)
    system_counts = Counter(a.system_name for a in activity if a.system_name)

    hour_histogram = [0] * 24
    for a in activity:
        hour_histogram[time.gmtime(a.occurred_at).tm_hour] += 1

    gang_sizes = [a.gang_size for a in activity if a.gang_size is not None]
    avg_gang_size = (sum(gang_sizes) / len(gang_sizes)) if gang_sizes else None

    last_active_candidates = [a.occurred_at for a in activity] + [s.seen_at for s in sightings]
    last_active_at = max(last_active_candidates) if last_active_candidates else None

    return PilotRollup(
        pilot_name=pilot_name,
        sighting_count=len(sightings),
        kill_count=kill_count,
        loss_count=loss_count,
        top_ships=ship_counts.most_common(_TOP_N),
        top_systems=system_counts.most_common(_TOP_N),
        hour_histogram=hour_histogram,
        avg_gang_size=avg_gang_size,
        last_active_at=last_active_at,
        updated_at=time.time(),
    )


def compute_system_rollup(system_name: str) -> SystemRollup:
    """Brute-force recompute of *system_name*'s rollup: hostile kill
    activity (any tracked pilot) in the last 30 days.

    top_hostile_corps is best-effort: combat_activity doesn't itself
    store corp/alliance (#237's schema is killmail-shaped, not
    standings-shaped), so each distinct pilot's corp is cross-referenced
    from their most recent pilot_history_store sighting -- None when
    that pilot's corp was never ESI-resolved.
    """
    from evealert.tools.combat_activity_store import get_activity_by_system  # noqa: PLC0415
    from evealert.tools.pilot_history_store import get_latest_corp_for_pilot  # noqa: PLC0415

    since = time.time() - _SYSTEM_ROLLUP_WINDOW_SECONDS
    activity = get_activity_by_system(system_name, since=since, limit=1_000_000)

    hour_histogram = [0] * 24
    for a in activity:
        hour_histogram[time.gmtime(a.occurred_at).tm_hour] += 1

    corp_counts: Counter = Counter()
    for pilot_name in {a.pilot_name for a in activity}:
        corp = get_latest_corp_for_pilot(pilot_name)
        if corp:
            corp_counts[corp] += 1

    return SystemRollup(
        system_name=system_name,
        hostile_kill_count_30d=len(activity),
        hour_histogram=hour_histogram,
        top_hostile_corps=corp_counts.most_common(_TOP_N),
        updated_at=time.time(),
    )


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _store_pilot_rollup(rollup: PilotRollup) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO pilot_rollup "
            "(pilot_name, sighting_count, kill_count, loss_count, top_ships, "
            "top_systems, hour_histogram, avg_gang_size, last_active_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(pilot_name) DO UPDATE SET "
            "sighting_count=excluded.sighting_count, kill_count=excluded.kill_count, "
            "loss_count=excluded.loss_count, top_ships=excluded.top_ships, "
            "top_systems=excluded.top_systems, hour_histogram=excluded.hour_histogram, "
            "avg_gang_size=excluded.avg_gang_size, last_active_at=excluded.last_active_at, "
            "updated_at=excluded.updated_at",
            (
                rollup.pilot_name, rollup.sighting_count, rollup.kill_count,
                rollup.loss_count, json.dumps(rollup.top_ships),
                json.dumps(rollup.top_systems), json.dumps(rollup.hour_histogram),
                rollup.avg_gang_size, rollup.last_active_at, rollup.updated_at,
            ),
        )
        conn.commit()


def _store_system_rollup(rollup: SystemRollup) -> None:
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO system_rollup "
            "(system_name, hostile_kill_count_30d, hour_histogram, top_hostile_corps, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(system_name) DO UPDATE SET "
            "hostile_kill_count_30d=excluded.hostile_kill_count_30d, "
            "hour_histogram=excluded.hour_histogram, "
            "top_hostile_corps=excluded.top_hostile_corps, updated_at=excluded.updated_at",
            (
                rollup.system_name, rollup.hostile_kill_count_30d,
                json.dumps(rollup.hour_histogram), json.dumps(rollup.top_hostile_corps),
                rollup.updated_at,
            ),
        )
        conn.commit()


def _load_stored_pilot_rollup(pilot_name: str) -> PilotRollup | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT pilot_name, sighting_count, kill_count, loss_count, top_ships, "
            "top_systems, hour_histogram, avg_gang_size, last_active_at, updated_at "
            "FROM pilot_rollup WHERE pilot_name = ?",
            (pilot_name,),
        ).fetchone()
    if row is None:
        return None
    return PilotRollup(
        pilot_name=row[0], sighting_count=row[1], kill_count=row[2], loss_count=row[3],
        top_ships=[tuple(x) for x in json.loads(row[4])],
        top_systems=[tuple(x) for x in json.loads(row[5])],
        hour_histogram=json.loads(row[6]), avg_gang_size=row[7],
        last_active_at=row[8], updated_at=row[9],
    )


def _load_stored_system_rollup(system_name: str) -> SystemRollup | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT system_name, hostile_kill_count_30d, hour_histogram, "
            "top_hostile_corps, updated_at FROM system_rollup WHERE system_name = ?",
            (system_name,),
        ).fetchone()
    if row is None:
        return None
    return SystemRollup(
        system_name=row[0], hostile_kill_count_30d=row[1],
        hour_histogram=json.loads(row[2]),
        top_hostile_corps=[tuple(x) for x in json.loads(row[3])],
        updated_at=row[4],
    )


def _is_empty_pilot_rollup(rollup: PilotRollup) -> bool:
    return rollup.sighting_count == 0 and rollup.kill_count == 0 and rollup.loss_count == 0


# ---------------------------------------------------------------------------
# Public read API
# ---------------------------------------------------------------------------


def get_pilot_rollup(
    pilot_name: str, *, max_age_seconds: float = _ROLLUP_STALE_SECONDS
) -> PilotRollup | None:
    """Return *pilot_name*'s rollup, recomputing synchronously if the
    stored one is missing or older than *max_age_seconds*.

    Returns None only when the pilot genuinely has no sighting/combat
    history at all -- callers (e.g. the future dossier UI) fall back to
    #216's summarize() in that case. Not for the alarm path -- see
    get_pilot_rollup_nonblocking().
    """
    stored = _load_stored_pilot_rollup(pilot_name)
    if stored is not None and time.time() - stored.updated_at < max_age_seconds:
        return None if _is_empty_pilot_rollup(stored) else stored

    fresh = compute_pilot_rollup(pilot_name)
    _store_pilot_rollup(fresh)
    return None if _is_empty_pilot_rollup(fresh) else fresh


def get_pilot_rollup_nonblocking(
    pilot_name: str, *, max_age_seconds: float = _ROLLUP_STALE_SECONDS, loop=None
) -> PilotRollup | None:
    """Alarm-path-safe read: returns whatever rollup is CURRENTLY stored
    immediately (possibly stale, possibly None) and never performs a
    synchronous recompute itself. If the stored rollup is missing or
    stale, schedules a background refresh via *loop* (or the running
    event loop) rather than blocking this call on it.

    *loop* is not strictly a running-loop requirement here since this
    function itself is synchronous -- pass it explicitly (e.g.
    AlertAgent.loop) when calling from a context where
    asyncio.get_event_loop() might not resolve to the right loop.
    """
    stored = _load_stored_pilot_rollup(pilot_name)
    is_stale = stored is None or (time.time() - stored.updated_at >= max_age_seconds)
    if is_stale:
        target_loop = loop
        if target_loop is None:
            try:
                # get_running_loop() (not get_event_loop()) deliberately --
                # the latter's legacy auto-create behavior would hand back
                # a loop that's never actually run, silently leaking the
                # scheduled refresh task instead of skipping it.
                target_loop = asyncio.get_running_loop()
            except RuntimeError:
                target_loop = None
        if target_loop is not None:
            target_loop.create_task(_refresh_pilot_rollup_background(pilot_name))
    if stored is None or _is_empty_pilot_rollup(stored):
        return None
    return stored


async def _refresh_pilot_rollup_background(pilot_name: str) -> None:
    try:
        fresh = compute_pilot_rollup(pilot_name)
        _store_pilot_rollup(fresh)
    except Exception as exc:
        logger.debug("Pilot rollup background refresh failed for %s: %s", pilot_name, exc)


def get_system_rollup(
    system_name: str, *, max_age_seconds: float = _ROLLUP_STALE_SECONDS
) -> SystemRollup | None:
    """Return *system_name*'s rollup, recomputing synchronously if the
    stored one is missing or stale. Returns None when the system has no
    recorded hostile activity at all."""
    stored = _load_stored_system_rollup(system_name)
    if stored is not None and time.time() - stored.updated_at < max_age_seconds:
        return None if stored.hostile_kill_count_30d == 0 else stored

    fresh = compute_system_rollup(system_name)
    _store_system_rollup(fresh)
    return None if fresh.hostile_kill_count_30d == 0 else fresh


# ---------------------------------------------------------------------------
# Maintenance-task sweep (#239, wired into AlertAgent._cache_maintenance_task)
# ---------------------------------------------------------------------------


def sweep_stale_rollups(
    *, lookback_seconds: float = _SWEEP_LOOKBACK_SECONDS, limit: int = _SWEEP_MAX_PILOTS
) -> int:
    """Recompute rollups only for pilots with sighting/combat-activity
    rows newer than their current rollup's updated_at (or with no rollup
    yet). A pilot with no new data since their last rollup is skipped --
    the rollup already reflects everything known about them. Bounded to
    *limit* pilots per call so one sweep can't block on an unbounded scan
    during a busy session. Returns the number of rollups refreshed.
    """
    from evealert.tools.combat_activity_store import (  # noqa: PLC0415
        get_pilots_with_activity_since as _combat_since,
    )
    from evealert.tools.pilot_history_store import (  # noqa: PLC0415
        get_pilots_with_activity_since as _history_since,
    )

    since = time.time() - lookback_seconds
    latest_activity: dict[str, float] = {}
    for name, ts in _history_since(since, limit):
        latest_activity[name] = max(latest_activity.get(name, 0.0), ts)
    for name, ts in _combat_since(since, limit):
        latest_activity[name] = max(latest_activity.get(name, 0.0), ts)

    refreshed = 0
    for pilot_name, latest_ts in list(latest_activity.items())[:limit]:
        existing = _load_stored_pilot_rollup(pilot_name)
        if existing is not None and existing.updated_at >= latest_ts:
            continue  # rollup already reflects all known activity
        fresh = compute_pilot_rollup(pilot_name)
        _store_pilot_rollup(fresh)
        refreshed += 1
    return refreshed
