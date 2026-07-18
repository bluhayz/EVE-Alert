"""Persistent storage for EVE Alert lifetime statistics and session reports.

Provides atomic read/write helpers so alarm counts survive across restarts
and each detection session is recorded as a named JSON file.
"""

import json
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from platformdirs import user_config_dir

if TYPE_CHECKING:
    from evealert.statistics import AlarmStatistics


def get_stats_path() -> str:
    """Return the path to the lifetime statistics JSON file.

    When the EVEALERT_STATS_PATH environment variable is set (e.g. in tests),
    that path is used instead of the platform config directory so tests never
    read or write the user's real statistics (#159).
    """
    override = os.environ.get("EVEALERT_STATS_PATH")
    if override:
        return override
    config_dir = Path(user_config_dir("evealert"))
    config_dir.mkdir(parents=True, exist_ok=True)
    return str(config_dir / "statistics.json")


def get_sessions_dir() -> Path:
    """Return the sessions/ subdirectory, creating it if needed.

    #236: honors the same EVEALERT_STATS_PATH override as get_stats_path()
    -- without this, every test exercising AlertAgent.stop() (which calls
    save_session_report() unconditionally) wrote a real
    session_YYYYMMDD_HHMMSS.json into the user's actual config directory.
    """
    override = os.environ.get("EVEALERT_STATS_PATH")
    if override:
        base = Path(override).parent
    else:
        base = Path(user_config_dir("evealert"))
    path = base / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Lifetime statistics (total_alarms + total_by_type persist across restarts)
# ---------------------------------------------------------------------------


def load_lifetime_stats() -> dict:
    """Read lifetime stats from disk. Returns {} if the file is missing or corrupt."""
    path = get_stats_path()
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def save_lifetime_stats(stats: "AlarmStatistics") -> None:
    """Atomically write lifetime stats to disk.

    Only the lifetime fields are persisted; session fields reset on each start
    by design.
    """
    data = {
        "total_alarms": stats.total_alarms,
        "total_by_type": dict(stats.total_by_type),
        "last_saved": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = get_stats_path()
    dir_name = os.path.dirname(path)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=dir_name, delete=False, suffix=".tmp"
        ) as tmp:
            json.dump(data, tmp, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp.name, path)
    except OSError:
        pass  # non-fatal — stats will still be correct in memory


# ---------------------------------------------------------------------------
# Session reports (one JSON file per detection session)
# ---------------------------------------------------------------------------


def save_session_report(stats: "AlarmStatistics", session_end: float) -> Path:
    """Write a per-session JSON report and return the file path.

    Reports are saved under the sessions/ directory as
    ``session_YYYYMMDD_HHMMSS.json``.
    """
    start_str = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.localtime(stats.session_start_time)
    )
    end_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(session_end))

    report = {
        "session_start": start_str,
        "session_end": end_str,
        "duration": stats.get_session_duration(),
        "session_alarms": stats.session_alarms,
        "total_enemy": stats.session_by_type.get("Enemy", 0),
        "total_faction": stats.session_by_type.get("Faction", 0),
        "history": [
            {"type": ev.alarm_type, "time": ev.formatted_time()}
            for ev in list(stats.alarm_history)
        ],
    }

    fname = time.strftime(
        "session_%Y%m%d_%H%M%S.json", time.localtime(stats.session_start_time)
    )
    dest = get_sessions_dir() / fname
    try:
        with open(dest, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    except OSError:
        pass
    return dest


def list_session_reports() -> list[Path]:
    """Return all saved session report Paths, sorted newest-first."""
    sessions_dir = get_sessions_dir()
    files = sorted(sessions_dir.glob("session_*.json"), reverse=True)
    return files
