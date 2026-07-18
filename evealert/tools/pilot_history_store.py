"""Persistent pilot-sighting history for EVE Alert (#214, v7.0).

Stores every pilot sighting -- Local enemy detections and intel-channel
mentions -- in a local SQLite database so later work in this milestone can
query a pilot's history: which systems/times they're seen in, their
movement pattern (#217), and a historical signal for the threat score
(#218).

SQLite (stdlib `sqlite3`, no new dependency) rather than the JSON-based
SettingsStore/stats_store used elsewhere in this app: an append-heavy,
potentially large, queryable time series is a different persistence shape
than user settings or lifetime stat counters.
"""

import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
    id INTEGER PRIMARY KEY,
    pilot_name TEXT NOT NULL,
    system TEXT,
    ship TEXT,
    source TEXT NOT NULL CHECK(source IN ('local', 'intel')),
    corp TEXT,
    alliance TEXT,
    seen_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sightings_pilot ON sightings(pilot_name);
CREATE INDEX IF NOT EXISTS idx_sightings_seen_at ON sightings(seen_at);
"""

# #238: schema version 2 -- adds a nullable character_id (populated when
# ESI resolution succeeded, so a later rename/name-collision can still be
# disambiguated) and the composite indexes the analytics rollup layer's
# per-pilot/per-system time-range queries need. Tracked via SQLite's
# built-in PRAGMA user_version so existing v1 databases migrate in place
# without a separate migrations table.
_SCHEMA_VERSION = 2

_VALID_SOURCES = ("local", "intel")


@dataclass
class Sighting:
    pilot_name: str
    system: str | None
    ship: str | None
    source: str
    corp: str | None
    alliance: str | None
    seen_at: float
    character_id: int | None = None


def get_pilot_history_path() -> str:
    """Return the path to the pilot-sighting SQLite database.

    When the EVEALERT_PILOT_HISTORY_PATH environment variable is set (e.g.
    in tests), that path is used instead of the platform config directory
    so tests never read or write the user's real history (mirrors
    EVEALERT_STATS_PATH, #159).
    """
    override = os.environ.get("EVEALERT_PILOT_HISTORY_PATH")
    if override:
        return override
    config_dir = Path(user_config_dir("evealert"))
    config_dir.mkdir(parents=True, exist_ok=True)
    return str(config_dir / "pilot_history.db")


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring *conn*'s database up to _SCHEMA_VERSION.

    #238: a fresh database gets the current schema directly via
    executescript(); an existing v1 database (no character_id column,
    missing the composite indexes) gets ALTER TABLE + CREATE INDEX
    applied in place. PRAGMA user_version tracks which migrations have
    already run so this is idempotent and cheap on every connection.
    """
    conn.executescript(_SCHEMA)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 2:
        try:
            conn.execute("ALTER TABLE sightings ADD COLUMN character_id INTEGER")
        except sqlite3.OperationalError:
            pass  # already has the column (e.g. a fresh DB via executescript above)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sightings_pilot_seen "
            "ON sightings(pilot_name, seen_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sightings_system_seen "
            "ON sightings(system, seen_at)"
        )
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
    conn.commit()


# #239: _migrate()'s executescript()/PRAGMA user_version dance is DDL,
# not a plain read/write -- re-running it on every single connection
# turned out to contend for SQLite's write lock badly enough to raise
# "database is locked" under real concurrent access (verified via the
# analytics rollup layer's concurrency test, which calls through this
# store on every rollup computation). Migration only ever needs to run
# once per DB file per process; PRAGMA user_version already makes it
# idempotent ACROSS processes/restarts, this cache just avoids paying
# the DDL cost EVERY call within one process's lifetime.
_migrated_paths: set = set()


def _connect() -> sqlite3.Connection:
    """Open a fresh connection with the schema ensured and migrated.

    One connection per call (no shared/global connection) -- this module
    is called occasionally (once per resolved pilot per alarm/intel
    mention), not in a hot loop, so per-call connection overhead is
    negligible and avoids any cross-thread connection-sharing concerns.
    WAL mode lets the engine thread write while a future UI-side read
    doesn't block behind it. A generous busy-timeout (default is 5s)
    lets a connection wait out a competing writer instead of raising.
    """
    path = get_pilot_history_path()
    conn = sqlite3.connect(path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    if path not in _migrated_paths:
        _migrate(conn)
        _migrated_paths.add(path)
    return conn


def record_sighting(
    pilot_name: str,
    *,
    source: str,
    system: str | None = None,
    ship: str | None = None,
    corp: str | None = None,
    alliance: str | None = None,
    seen_at: float | None = None,
    character_id: int | None = None,
) -> None:
    """Record one pilot sighting.

    *source* must be "local" (Enemy-alarm detection) or "intel"
    (intel-channel mention). *seen_at* defaults to now. *character_id*
    (#238) is populated when ESI resolution succeeded, so a later rename
    or a name shared by two different pilots can still be disambiguated;
    None when unresolved (e.g. intel-channel mentions never resolve one).
    """
    if source not in _VALID_SOURCES:
        raise ValueError(f"source must be one of {_VALID_SOURCES}, got {source!r}")
    if seen_at is None:
        seen_at = time.time()
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO sightings "
            "(pilot_name, system, ship, source, corp, alliance, seen_at, character_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pilot_name, system, ship, source, corp, alliance, seen_at, character_id),
        )
        conn.commit()


def get_sightings(
    pilot_name: str, *, since: float | None = None, limit: int = 200
) -> list[Sighting]:
    """Return sightings for *pilot_name*, newest-first.

    *since*, if given, restricts to sightings at or after that unix epoch.
    """
    query = (
        "SELECT pilot_name, system, ship, source, corp, alliance, seen_at, character_id "
        "FROM sightings WHERE pilot_name = ?"
    )
    params: list = [pilot_name]
    if since is not None:
        query += " AND seen_at >= ?"
        params.append(since)
    query += " ORDER BY seen_at DESC LIMIT ?"
    params.append(limit)
    with closing(_connect()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [Sighting(*row) for row in rows]


def get_pilots_with_activity_since(since: float, limit: int = 500) -> list[tuple[str, float]]:
    """Return [(pilot_name, max_seen_at), ...] for pilots with at least
    one sighting at or after *since*, most-recently-active first.

    Used by #239's rollup-maintenance sweep to find pilots whose data
    changed since their last rollup, without re-scanning every pilot ever
    recorded.
    """
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT pilot_name, MAX(seen_at) FROM sightings "
            "WHERE seen_at >= ? GROUP BY pilot_name "
            "ORDER BY MAX(seen_at) DESC LIMIT ?",
            (since, limit),
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def get_latest_corp_for_pilot(pilot_name: str) -> str | None:
    """Return the most recently recorded corp for *pilot_name*, or None
    if never resolved. Best-effort helper for #239's system_rollup
    top_hostile_corps -- combat_activity rows don't carry corp/alliance
    (#237's schema), so the rollup cross-references the pilot's most
    recent known corp here instead."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT corp FROM sightings WHERE pilot_name = ? AND corp IS NOT NULL "
            "ORDER BY seen_at DESC LIMIT 1",
            (pilot_name,),
        ).fetchone()
    return row[0] if row else None


def search_pilot_names(query: str, limit: int = 50) -> list[str]:
    """Return distinct pilot names with at least one sighting whose name
    contains *query* (case-insensitive substring match), alphabetically.

    Used by #244's Intel Analytics pilot search.
    """
    if not query.strip():
        return []
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT DISTINCT pilot_name FROM sightings "
            "WHERE LOWER(pilot_name) LIKE LOWER(?) "
            "ORDER BY pilot_name LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
    return [r[0] for r in rows]


def get_pilots_by_corp_or_alliance(name: str, limit: int = 500) -> list[str]:
    """Return distinct pilot names with at least one sighting whose corp
    OR alliance matches *name* (case-insensitive exact match).

    Used by #242's group_activity to find which tracked pilots belong to
    a given corp/alliance -- combat_activity itself doesn't carry
    corp/alliance (#237's schema is killmail-shaped), so group membership
    is cross-referenced from sighting data here, the same pattern
    get_latest_corp_for_pilot() uses for #239's system_rollup.
    """
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT DISTINCT pilot_name FROM sightings "
            "WHERE LOWER(corp) = LOWER(?) OR LOWER(alliance) = LOWER(?) "
            "LIMIT ?",
            (name, name, limit),
        ).fetchall()
    return [r[0] for r in rows]


def prune_older_than(days: int) -> int:
    """Delete sightings older than *days* days old. Returns rows deleted.

    days <= 0 means "keep forever" -- no rows are deleted. (A literal
    days=0 cutoff would compute to "now," deleting nearly everything,
    which is the opposite of what a caller passing 0 almost certainly
    means.)
    """
    if days <= 0:
        return 0
    cutoff = time.time() - (days * 86400)
    with closing(_connect()) as conn:
        cur = conn.execute("DELETE FROM sightings WHERE seen_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount
