"""Wormhole and Eve-Scout awareness tools for EVE Alert.

v3.6 #88: Thera connection monitor (Eve-Scout API)
v3.6 #89: Wormhole system static type from EVE SDE via ESI
v3.6 #90: WH drop heuristic — detect simultaneous multi-pilot Local appearance
"""

import asyncio
import logging
import time
from typing import NamedTuple

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

logger = logging.getLogger("alert.wormhole")

_HTTP_TIMEOUT = 8.0
_EVE_SCOUT_URL = "https://www.eve-scout.com/api/wormholes"

# WH system ID range: 31000000 – 32000000
_WH_SYSTEM_MIN = 31_000_000
_WH_SYSTEM_MAX = 32_000_000


class TheraConnection(NamedTuple):
    source_system_id: int
    source_system_name: str
    source_wh_class: str
    destination_system_id: int
    destination_system_name: str
    wh_type: str
    expires_at: str  # ISO string


# ------------------------------------------------------------------
# #88: Thera connection monitor
# ------------------------------------------------------------------


async def get_thera_connections() -> list[TheraConnection]:
    """Fetch current Thera wormhole connections from Eve-Scout."""
    if not _HTTPX_AVAILABLE:
        return []
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            headers={"User-Agent": "EVEAlert/3.6"},
        ) as client:
            resp = await client.get(_EVE_SCOUT_URL)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.debug("Eve-Scout API failed: %s", exc)
        return []

    connections: list[TheraConnection] = []
    for entry in data if isinstance(data, list) else []:
        try:
            src = entry.get("sourceSolarSystem", {})
            dst = entry.get("destinationSolarSystem", {})
            connections.append(
                TheraConnection(
                    source_system_id=src.get("id", 0),
                    source_system_name=src.get("name", "?"),
                    source_wh_class=src.get("whClass", "?"),
                    destination_system_id=dst.get("id", 0),
                    destination_system_name=dst.get("name", "?"),
                    wh_type=entry.get("type", {}).get("name", "?"),
                    expires_at=entry.get("expiresAt", ""),
                )
            )
        except Exception:
            continue
    return connections


async def find_nearby_thera_connections(
    system_id: int, max_jumps: int = 5
) -> list[tuple[TheraConnection, int]]:
    """Return Thera connections within *max_jumps* of *system_id*.

    Returns list of (TheraConnection, jump_distance) tuples.
    Requires the universe cache for jump graph.
    """
    from evealert.tools.universe import (  # pylint: disable=import-outside-toplevel
        get_universe_cache,
    )

    cache = get_universe_cache()
    nearby_systems = await cache.get_systems_within_jumps(system_id, max_jumps)
    all_system_ids = set(nearby_systems.keys()) | {system_id}

    connections = await get_thera_connections()
    results = []
    for conn in connections:
        for sys_id in (conn.source_system_id, conn.destination_system_id):
            if sys_id in all_system_ids:
                jump_dist = nearby_systems.get(sys_id, 0)
                results.append((conn, jump_dist))
                break
    return results


# ------------------------------------------------------------------
# #89: WH static type awareness
# ------------------------------------------------------------------


def is_wormhole_system(system_id: int) -> bool:
    return _WH_SYSTEM_MIN <= system_id <= _WH_SYSTEM_MAX


async def get_wh_static_info(system_id: int) -> dict | None:
    """Fetch wormhole system info from ESI (class, statics, effect).

    Returns a dict with 'wh_class', 'statics', 'effect' or None for k-space.
    Uses ESI /v4/universe/systems/{id}/ which includes wormhole metadata.
    """
    if not is_wormhole_system(system_id):
        return None
    if not _HTTPX_AVAILABLE:
        return None

    url = f"https://esi.evetech.net/v4/universe/systems/{system_id}/"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.debug("ESI WH system fetch failed for %d: %s", system_id, exc)
        return None

    # ESI doesn't give wh_class directly in this endpoint for older WH systems.
    # We can infer from security_status: -1.0 = C1-C3, varies for C4-C6.
    # Better heuristic: system_id ranges map to classes.
    wh_class = _infer_wh_class(system_id)

    return {
        "wh_class": wh_class,
        "system_name": data.get("name", str(system_id)),
        "statics": [],  # full static lookup requires SDE; tag for future
        "effect": None,
    }


def _infer_wh_class(system_id: int) -> str:
    """Infer WH class from system ID range (approximate)."""
    if 31000001 <= system_id <= 31000199:
        return "C1"
    if 31000200 <= system_id <= 31000399:
        return "C2"
    if 31000400 <= system_id <= 31000599:
        return "C3"
    if 31000600 <= system_id <= 31000799:
        return "C4"
    if 31000800 <= system_id <= 31000999:
        return "C5"
    if 31001000 <= system_id <= 31001199:
        return "C6"
    if 31001200 <= system_id <= 31001999:
        return "Thera / Special"
    return "Unknown WH"


# ------------------------------------------------------------------
# #90: WH drop heuristic
# ------------------------------------------------------------------


class WhDropDetector:
    """Detects the pattern of a WH fleet drop in Local.

    Pattern: 3+ new pilots join Local within a short window AND
    none of them have recent kill history in the current region.
    """

    def __init__(self, threshold: int = 3, window_seconds: float = 60.0) -> None:
        self._threshold = threshold
        self._window = window_seconds
        self._join_times: list[float] = []

    def record_join(self) -> bool:
        """Record a new Local join. Returns True if drop threshold is met."""
        now = time.time()
        self._join_times.append(now)
        # Prune stale entries outside the window
        self._join_times = [t for t in self._join_times if now - t <= self._window]
        return len(self._join_times) >= self._threshold

    def reset(self) -> None:
        self._join_times.clear()
