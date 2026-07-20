"""Persistent combat-activity history for EVE Alert (#237, v7.3).

Killmail-derived per-pilot activity -- what a tracked hostile flies, where
they hunt, and who they fly with -- fed by two ingest paths:

  1. The R2Z2 live-kill stream (#169): AlertAgent records a row for any
     killmail attacker/victim who is a "tracked" pilot (current OCR
     identity, or present in pilot_history_store).
  2. A one-time zKillboard backfill when a pilot first triggers an Enemy
     alarm, via backfill_from_zkillboard(), so a dossier isn't empty on
     first encounter.

SQLite (stdlib sqlite3), a separate DB file from pilot_history.db: this is
a higher-volume, killmail-shaped table with a different write pattern
(many small inserts from the live stream) than the sighting/session data
in pilot_history_store.py.
"""

import asyncio
import logging
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

from evealert.tools.http_common import DEFAULT_HEADERS

logger = logging.getLogger("alert.combat_activity")

_ESI_BASE = "https://esi.evetech.net/latest"
_ZKB_BASE = "https://zkillboard.com/api"
_HTTP_TIMEOUT = 10.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS combat_activity (
    id INTEGER PRIMARY KEY,
    killmail_id INTEGER NOT NULL,
    pilot_name TEXT NOT NULL,
    character_id INTEGER,
    role TEXT NOT NULL CHECK(role IN ('attacker', 'victim')),
    ship_type_id INTEGER,
    ship_name TEXT,
    solar_system_id INTEGER,
    system_name TEXT,
    gang_size INTEGER,
    victim_ship_name TEXT,
    occurred_at REAL NOT NULL,
    UNIQUE(killmail_id, pilot_name, role)
);
CREATE INDEX IF NOT EXISTS idx_combat_activity_pilot ON combat_activity(pilot_name);
CREATE INDEX IF NOT EXISTS idx_combat_activity_occurred_at ON combat_activity(occurred_at);
"""

_VALID_ROLES = ("attacker", "victim")

# #237: backfill is capped at ~50 rows per pilot -- enough for a useful
# first-encounter dossier without turning one alarm into an unbounded zKB
# crawl. zKB's characterID feed returns up to 200 entries/page; take the
# most recent slice.
_BACKFILL_MAX_ROWS = 50
# Bounded concurrency for the per-killmail ESI detail fetches during backfill.
_BACKFILL_MAX_CONCURRENT_FETCHES = 5


@dataclass
class CombatActivityRow:
    killmail_id: int
    pilot_name: str
    character_id: int | None
    role: str
    ship_type_id: int | None
    ship_name: str | None
    solar_system_id: int | None
    system_name: str | None
    gang_size: int | None
    victim_ship_name: str | None
    occurred_at: float


def get_combat_activity_path() -> str:
    """Return the path to the combat-activity SQLite database.

    When the EVEALERT_COMBAT_ACTIVITY_PATH environment variable is set
    (e.g. in tests), that path is used instead of the platform config
    directory so tests never read or write the user's real data (mirrors
    EVEALERT_STATS_PATH/EVEALERT_PILOT_HISTORY_PATH, #159/#214).
    """
    override = os.environ.get("EVEALERT_COMBAT_ACTIVITY_PATH")
    if override:
        return override
    config_dir = Path(user_config_dir("evealert"))
    config_dir.mkdir(parents=True, exist_ok=True)
    return str(config_dir / "combat_activity.db")


# #239: re-running executescript() (DDL) on every connection contended
# for SQLite's write lock badly enough under real concurrent access to
# raise "database is locked" -- see the identical fix/comment in
# pilot_history_store._connect(). CREATE TABLE/INDEX IF NOT EXISTS only
# needs to run once per DB file per process.
_schema_initialized_paths: set = set()


def _connect() -> sqlite3.Connection:
    """Open a fresh connection with the schema ensured.

    One connection per call, same reasoning as pilot_history_store._connect():
    this module is called occasionally (once per matched kill, or a small
    backfill burst on first alarm), not in a hot loop. A generous
    busy-timeout (default is 5s) lets a connection wait out a competing
    writer instead of raising.
    """
    path = get_combat_activity_path()
    conn = sqlite3.connect(path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    if path not in _schema_initialized_paths:
        conn.executescript(_SCHEMA)
        _schema_initialized_paths.add(path)
    return conn


def record_activity(
    killmail_id: int,
    pilot_name: str,
    *,
    role: str,
    character_id: int | None = None,
    ship_type_id: int | None = None,
    ship_name: str | None = None,
    solar_system_id: int | None = None,
    system_name: str | None = None,
    gang_size: int | None = None,
    victim_ship_name: str | None = None,
    occurred_at: float | None = None,
) -> None:
    """Record one pilot's involvement (attacker or victim) in one killmail.

    Idempotent on (killmail_id, pilot_name, role) -- re-recording the same
    kill for the same pilot/role (e.g. a backfill overlapping with a
    live-stream row) is a silent no-op, not a duplicate row or an error.
    """
    if role not in _VALID_ROLES:
        raise ValueError(f"role must be one of {_VALID_ROLES}, got {role!r}")
    if occurred_at is None:
        occurred_at = time.time()
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO combat_activity "
            "(killmail_id, pilot_name, character_id, role, ship_type_id, "
            "ship_name, solar_system_id, system_name, gang_size, "
            "victim_ship_name, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                killmail_id, pilot_name, character_id, role, ship_type_id,
                ship_name, solar_system_id, system_name, gang_size,
                victim_ship_name, occurred_at,
            ),
        )
        conn.commit()


def get_activity(
    pilot_name: str, *, since: float | None = None, limit: int = 200
) -> list[CombatActivityRow]:
    """Return combat-activity rows for *pilot_name*, newest-first.

    *since*, if given, restricts to rows at or after that unix epoch.
    """
    query = (
        "SELECT killmail_id, pilot_name, character_id, role, ship_type_id, "
        "ship_name, solar_system_id, system_name, gang_size, "
        "victim_ship_name, occurred_at "
        "FROM combat_activity WHERE pilot_name = ?"
    )
    params: list = [pilot_name]
    if since is not None:
        query += " AND occurred_at >= ?"
        params.append(since)
    query += " ORDER BY occurred_at DESC LIMIT ?"
    params.append(limit)
    with closing(_connect()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [CombatActivityRow(*row) for row in rows]


def get_activity_by_system(
    system_name: str, *, since: float | None = None, limit: int = 1000
) -> list[CombatActivityRow]:
    """Return combat_activity rows for *system_name* (any tracked pilot),
    newest-first. Used by #239's system_rollup -- "who's been getting
    kills in this system" rather than one pilot's history."""
    query = (
        "SELECT killmail_id, pilot_name, character_id, role, ship_type_id, "
        "ship_name, solar_system_id, system_name, gang_size, "
        "victim_ship_name, occurred_at "
        "FROM combat_activity WHERE system_name = ?"
    )
    params: list = [system_name]
    if since is not None:
        query += " AND occurred_at >= ?"
        params.append(since)
    query += " ORDER BY occurred_at DESC LIMIT ?"
    params.append(limit)
    with closing(_connect()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [CombatActivityRow(*row) for row in rows]


def get_pilots_with_activity_since(since: float, limit: int = 500) -> list[tuple[str, float]]:
    """Return [(pilot_name, max_occurred_at), ...] for pilots with at
    least one combat_activity row at or after *since*, most-recently-
    active first. Used by #239's rollup-maintenance sweep."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT pilot_name, MAX(occurred_at) FROM combat_activity "
            "WHERE occurred_at >= ? GROUP BY pilot_name "
            "ORDER BY MAX(occurred_at) DESC LIMIT ?",
            (since, limit),
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def get_activity_for_pilots(
    pilot_names: list[str], *, since: float | None = None, limit: int = 2000
) -> list[CombatActivityRow]:
    """Return combat_activity rows for any of *pilot_names*, newest-first.

    Used by #242's group_activity to fetch one corp/alliance's worth of
    tracked pilots' activity in a single query rather than one round-trip
    per pilot.
    """
    if not pilot_names:
        return []
    placeholders = ",".join("?" * len(pilot_names))
    query = (
        "SELECT killmail_id, pilot_name, character_id, role, ship_type_id, "
        "ship_name, solar_system_id, system_name, gang_size, "
        f"victim_ship_name, occurred_at FROM combat_activity WHERE pilot_name IN ({placeholders})"
    )
    params: list = list(pilot_names)
    if since is not None:
        query += " AND occurred_at >= ?"
        params.append(since)
    query += " ORDER BY occurred_at DESC LIMIT ?"
    params.append(limit)
    with closing(_connect()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [CombatActivityRow(*row) for row in rows]


def search_pilot_names(query: str, limit: int = 50) -> list[str]:
    """Return distinct pilot names with at least one combat_activity row
    whose name contains *query* (case-insensitive substring match),
    alphabetically.

    Used by #244's Intel Analytics pilot search -- combined with
    pilot_history_store's own search, this also surfaces pilots only
    ever recorded via the R2Z2/watchlist path (#240), never through a
    Local/Enemy-alarm sighting.
    """
    if not query.strip():
        return []
    # #249: escape LIKE's own wildcard characters -- see the identical
    # fix/comment in pilot_history_store.search_pilot_names().
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT DISTINCT pilot_name FROM combat_activity "
            "WHERE LOWER(pilot_name) LIKE LOWER(?) ESCAPE '\\' "
            "ORDER BY pilot_name LIMIT ?",
            (f"%{escaped}%", limit),
        ).fetchall()
    return [r[0] for r in rows]


def get_co_attackers(killmail_ids: list[int]) -> list[tuple[int, str]]:
    """Return (killmail_id, pilot_name) for every attacker-role
    combat_activity row on any of *killmail_ids*.

    Used by #241's fleetmate inference: given one tracked pilot's own
    attacker-role killmail IDs, this finds which OTHER tracked pilots
    also attacked those same killmails (each (killmail_id, pilot_name)
    pair is unique per the schema's UNIQUE constraint, so counting
    occurrences per pilot_name directly counts shared killmails).
    """
    if not killmail_ids:
        return []
    placeholders = ",".join("?" * len(killmail_ids))
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"SELECT killmail_id, pilot_name FROM combat_activity "
            f"WHERE role = 'attacker' AND killmail_id IN ({placeholders})",
            killmail_ids,
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def prune_older_than(days: int) -> int:
    """Delete combat_activity rows older than *days* days old. Returns
    rows deleted.

    days <= 0 means "keep forever" -- same convention as
    pilot_history_store.prune_older_than().
    """
    if days <= 0:
        return 0
    cutoff = time.time() - (days * 86400)
    with closing(_connect()) as conn:
        cur = conn.execute("DELETE FROM combat_activity WHERE occurred_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount


# ---------------------------------------------------------------------------
# zKillboard backfill (#237, ingest path 2)
# ---------------------------------------------------------------------------


async def backfill_from_zkillboard(character_id: int, pilot_name: str) -> int:
    """Fetch this character's recent zKillboard activity and record it as
    combat_activity rows. Returns the number of rows inserted.

    Best-effort: returns 0 (never raises) when httpx is unavailable, the
    zKB fetch fails, or the character has no zKB history.
    """
    if not _HTTPX_AVAILABLE or not character_id:
        return 0

    url = f"{_ZKB_BASE}/characterID/{character_id}/"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=DEFAULT_HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            entries = resp.json()
    except Exception as exc:
        logger.debug("Combat activity backfill: zKB fetch failed for %d: %s", character_id, exc)
        return 0

    if not isinstance(entries, list):
        return 0
    from evealert.tools.zkillboard import clean_zkb_entries  # noqa: PLC0415

    entries = clean_zkb_entries(entries)[:_BACKFILL_MAX_ROWS]
    if not entries:
        return 0

    semaphore = asyncio.Semaphore(_BACKFILL_MAX_CONCURRENT_FETCHES)

    async def _fetch_and_record(entry: dict) -> bool:
        async with semaphore:
            return await _backfill_one_killmail(entry, character_id, pilot_name)

    results = await asyncio.gather(
        *(_fetch_and_record(e) for e in entries), return_exceptions=True
    )
    return sum(1 for r in results if r is True)


async def _backfill_one_killmail(entry: dict, character_id: int, pilot_name: str) -> bool:
    """Fetch one ESI killmail detail and record a combat_activity row for
    *character_id* (as attacker or victim, whichever role they had).
    Returns True on a successful insert, False otherwise (never raises)."""
    kill_id = entry.get("killmail_id")
    hash_val = (entry.get("zkb") or {}).get("hash", "")
    if not kill_id or not hash_val:
        return False

    url = f"{_ESI_BASE}/killmails/{kill_id}/{hash_val}/"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=DEFAULT_HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            km = resp.json()
    except Exception as exc:
        logger.debug("Combat activity backfill: ESI killmail fetch failed for %s: %s", kill_id, exc)
        return False

    victim = km.get("victim") or {}
    attackers = km.get("attackers") or []
    solar_system_id = km.get("solar_system_id")
    occurred_at = _parse_killmail_time(km.get("killmail_time"))

    if victim.get("character_id") == character_id:
        role = "victim"
        ship_type_id = victim.get("ship_type_id")
    else:
        role = "attacker"
        ship_type_id = None
        for a in attackers:
            if isinstance(a, dict) and a.get("character_id") == character_id:
                ship_type_id = a.get("ship_type_id")
                break
        else:
            # This character isn't actually on the killmail (shouldn't
            # normally happen -- zKB's own character feed) -- skip rather
            # than record a wrong/empty row.
            return False

    ship_name = await _resolve_type_name(ship_type_id)
    victim_ship_name = await _resolve_type_name(victim.get("ship_type_id"))
    system_name = await _resolve_system_name(solar_system_id)

    try:
        record_activity(
            kill_id,
            pilot_name,
            role=role,
            character_id=character_id,
            ship_type_id=ship_type_id,
            ship_name=ship_name,
            solar_system_id=solar_system_id,
            system_name=system_name,
            gang_size=len(attackers),
            victim_ship_name=victim_ship_name,
            occurred_at=occurred_at,
        )
        return True
    except Exception as exc:
        logger.debug("Combat activity backfill: record_activity failed for %s: %s", kill_id, exc)
        return False


def _parse_killmail_time(time_str: str | None) -> float:
    if not time_str:
        return time.time()
    try:
        from datetime import datetime  # noqa: PLC0415

        return datetime.fromisoformat(time_str.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return time.time()


async def _resolve_type_name(type_id: int | None) -> str | None:
    if not type_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=DEFAULT_HEADERS) as client:
            resp = await client.get(f"{_ESI_BASE}/universe/types/{type_id}/")
            resp.raise_for_status()
            return resp.json().get("name")
    except Exception:
        return None


async def _resolve_system_name(system_id: int | None) -> str | None:
    if not system_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=DEFAULT_HEADERS) as client:
            resp = await client.get(f"{_ESI_BASE}/universe/systems/{system_id}/")
            resp.raise_for_status()
            return resp.json().get("name")
    except Exception:
        return None
