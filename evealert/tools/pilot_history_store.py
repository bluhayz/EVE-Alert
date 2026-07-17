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


def _connect() -> sqlite3.Connection:
    """Open a fresh connection with the schema ensured.

    One connection per call (no shared/global connection) -- this module
    is called occasionally (once per resolved pilot per alarm/intel
    mention), not in a hot loop, so per-call connection overhead is
    negligible and avoids any cross-thread connection-sharing concerns.
    WAL mode lets the engine thread write while a future UI-side read
    doesn't block behind it.
    """
    conn = sqlite3.connect(get_pilot_history_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
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
) -> None:
    """Record one pilot sighting.

    *source* must be "local" (Enemy-alarm detection) or "intel"
    (intel-channel mention). *seen_at* defaults to now.
    """
    if source not in _VALID_SOURCES:
        raise ValueError(f"source must be one of {_VALID_SOURCES}, got {source!r}")
    if seen_at is None:
        seen_at = time.time()
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO sightings "
            "(pilot_name, system, ship, source, corp, alliance, seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pilot_name, system, ship, source, corp, alliance, seen_at),
        )
        conn.commit()


def get_sightings(
    pilot_name: str, *, since: float | None = None, limit: int = 200
) -> list[Sighting]:
    """Return sightings for *pilot_name*, newest-first.

    *since*, if given, restricts to sightings at or after that unix epoch.
    """
    query = (
        "SELECT pilot_name, system, ship, source, corp, alliance, seen_at "
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
