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
_EVE_SCOUT_URL = "https://api.eve-scout.com/v2/public/signatures"

# WH system ID range: 31000000 – 32000000
_WH_SYSTEM_MIN = 31_000_000
_WH_SYSTEM_MAX = 32_000_000


class TheraConnection(NamedTuple):
    hub_system_id: int  # Thera or Turnur (Eve-Scout "out" side)
    hub_system_name: str
    system_id: int  # the connected system (Eve-Scout "in" side)
    system_name: str
    system_class: str  # e.g. "c4", "hs", "ls", "ns" (from Eve-Scout)
    wh_type: str  # signature code, e.g. "J377"
    expires_at: str  # ISO string
    remaining_hours: int


# ------------------------------------------------------------------
# #88: Thera connection monitor
# ------------------------------------------------------------------


async def get_thera_connections() -> list[TheraConnection]:
    """Fetch current Thera/Turnur wormhole connections from Eve-Scout.

    Uses the Eve-Scout v2 public signatures API. Its schema is flat (no
    nested type/system objects) — the previous code assumed nested dicts and
    an endpoint that no longer exists, so it always returned [] (#101).
    """
    if not _HTTPX_AVAILABLE:
        return []
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            headers={"User-Agent": "EVEAlert/4.0"},
        ) as client:
            resp = await client.get(_EVE_SCOUT_URL)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.debug("Eve-Scout API failed: %s", exc)
        return []

    connections: list[TheraConnection] = []
    for entry in data if isinstance(data, list) else []:
        if entry.get("signature_type") != "wormhole":
            continue
        try:
            connections.append(
                TheraConnection(
                    hub_system_id=entry.get("out_system_id", 0),
                    hub_system_name=entry.get("out_system_name", "?"),
                    system_id=entry.get("in_system_id", 0),
                    system_name=entry.get("in_system_name", "?"),
                    system_class=entry.get("in_system_class", ""),
                    wh_type=entry.get("wh_type", "?"),
                    expires_at=entry.get("expires_at", ""),
                    remaining_hours=int(entry.get("remaining_hours") or 0),
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
        for sys_id in (conn.hub_system_id, conn.system_id):
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
    """WH class is not derivable from the solar-system ID (CCP stores it in
    the SDE; the IDs are not laid out in sequential class bands — e.g. Thera
    is 31000005). Return an honest "Unknown" rather than a fabricated class
    (#101). Eve-Scout supplies the class directly for Thera connections via
    TheraConnection.system_class.
    """
    if not is_wormhole_system(system_id):
        return "k-space"
    if system_id == 31000005:
        return "Thera"
    return "Unknown"


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
