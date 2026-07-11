"""EVE universe data cache for EVE Alert.

Provides system ID/name resolution, jump-graph BFS for neighbor discovery,
system classification (pipe/pocket/crossroads), and sovereignty data.

All data comes from public ESI endpoints — no authentication required.
Results are cached with appropriate TTLs to avoid hammering the API.
"""

import asyncio
import logging
import time
from collections import deque
from typing import NamedTuple

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

logger = logging.getLogger("alert.universe")

_ESI_BASE = "https://esi.evetech.net"
_ZKB_BASE = "https://zkillboard.com/api"
_HTTP_TIMEOUT = 8.0

# Cache TTLs
_NAME_CACHE_TTL = 86400  # system names never change — cache for 24 h
_SOV_CACHE_TTL = 300  # sovereignty refreshes every 5 min


class SovInfo(NamedTuple):
    system_id: int
    alliance_id: int | None
    alliance_name: str | None
    corporation_id: int | None
    corporation_name: str | None
    has_ihub: bool
    has_tcu: bool
    faction_id: int | None  # populated for NPC sov space


class RouteLeg(NamedTuple):
    system_id: int
    system_name: str
    jumps_from_origin: int
    kills_last_hour: int
    threat_level: str  # "safe" | "caution" | "danger"


class UniverseCache:
    """Async cache for ESI universe data: system IDs, neighbors, sovereignty."""

    def __init__(self) -> None:
        # name (lower) → system_id
        self._name_to_id: dict[str, int] = {}
        # system_id → name
        self._id_to_name: dict[int, str] = {}
        # system_id → list[neighbor_system_id]
        self._neighbors: dict[int, list[int]] = {}
        # system_id → list[stargate_id]
        self._stargates: dict[int, list[int]] = {}
        # (sov_data, fetch_time)
        self._sov_cache: tuple[dict, float] = ({}, 0.0)
        # alliance/corp name cache
        self._entity_names: dict[int, str] = {}

    # ------------------------------------------------------------------
    # System ID / name resolution
    # ------------------------------------------------------------------

    async def get_system_id(self, name: str) -> int | None:
        """Return the system ID for *name*, or None if not found."""
        key = name.lower().strip()
        if key in self._name_to_id:
            return self._name_to_id[key]
        system_id = await self._search_system(name)
        if system_id:
            self._name_to_id[key] = system_id
            self._id_to_name[system_id] = name
        return system_id

    async def get_system_name(self, system_id: int) -> str | None:
        """Return the system name for *system_id*, or None."""
        if system_id in self._id_to_name:
            return self._id_to_name[system_id]
        name = await self._fetch_system_name(system_id)
        if name:
            self._id_to_name[system_id] = name
            self._name_to_id[name.lower()] = system_id
        return name

    # ------------------------------------------------------------------
    # Jump graph
    # ------------------------------------------------------------------

    async def get_neighbors(self, system_id: int) -> list[int]:
        """Return the list of directly-adjacent system IDs via stargates."""
        if system_id in self._neighbors:
            return self._neighbors[system_id]
        neighbors = await self._fetch_neighbors(system_id)
        self._neighbors[system_id] = neighbors
        return neighbors

    async def get_gate_count(self, system_id: int) -> int:
        """Return the number of stargates in *system_id*."""
        return len(await self.get_neighbors(system_id))

    async def get_systems_within_jumps(
        self, origin_id: int, max_jumps: int
    ) -> dict[int, int]:
        """BFS from *origin_id* — returns {system_id: jump_distance}.

        The origin itself is not included in the result.
        """
        visited: dict[int, int] = {}
        queue: deque[tuple[int, int]] = deque([(origin_id, 0)])
        seen: set[int] = {origin_id}

        while queue:
            current, depth = queue.popleft()
            if depth > 0:
                visited[current] = depth
            if depth >= max_jumps:
                continue
            for neighbor in await self.get_neighbors(current):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, depth + 1))

        return visited

    async def classify_system(self, system_id: int) -> str:
        """Return "dead-end", "pipe", or "crossroads" based on gate count."""
        gate_count = await self.get_gate_count(system_id)
        if gate_count <= 1:
            return "dead-end"
        if gate_count == 2:
            return "pipe"
        return "crossroads"

    # ------------------------------------------------------------------
    # Sovereignty
    # ------------------------------------------------------------------

    async def get_sovereignty(self, system_id: int) -> SovInfo | None:
        """Return sovereignty info for *system_id*.

        Pulls the bulk sov map (one ESI call covering all null/low/WH),
        caches it for _SOV_CACHE_TTL seconds.
        """
        sov_map, fetched_at = self._sov_cache
        if time.time() - fetched_at > _SOV_CACHE_TTL:
            sov_map = await self._fetch_sov_map()
            self._sov_cache = (sov_map, time.time())

        entry = sov_map.get(system_id)
        if entry is None:
            # No sov entry = high-sec / NPC null
            return SovInfo(
                system_id=system_id,
                alliance_id=None,
                alliance_name=None,
                corporation_id=None,
                corporation_name=None,
                has_ihub=False,
                has_tcu=False,
                faction_id=None,
            )

        alliance_id: int | None = entry.get("alliance_id")
        corp_id: int | None = entry.get("corporation_id")
        faction_id: int | None = entry.get("faction_id")

        alliance_name = (
            await self._get_entity_name("alliances", alliance_id)
            if alliance_id
            else None
        )
        corp_name = (
            await self._get_entity_name("corporations", corp_id) if corp_id else None
        )

        return SovInfo(
            system_id=system_id,
            alliance_id=alliance_id,
            alliance_name=alliance_name,
            corporation_id=corp_id,
            corporation_name=corp_name,
            has_ihub=bool(entry.get("ihub_id")),
            has_tcu=bool(entry.get("structures_plex")),
            faction_id=faction_id,
        )

    # ------------------------------------------------------------------
    # Route threat assessment
    # ------------------------------------------------------------------

    async def route_threat(
        self, origin_id: int, destination_id: int, max_hops: int = 15
    ) -> list[RouteLeg] | None:
        """Compute shortest path from origin to destination and check kill activity.

        Returns an ordered list of RouteLeg objects (one per hop, excluding
        the origin), or None if no path is found within max_hops.
        """
        path = await self._bfs_path(origin_id, destination_id, max_hops)
        if not path:
            return None

        legs: list[RouteLeg] = []
        for depth, sys_id in enumerate(path[1:], start=1):
            name = await self.get_system_name(sys_id) or str(sys_id)
            kills = await self._zkb_kills_last_hour(sys_id)
            if kills == 0:
                threat = "safe"
            elif kills <= 2:
                threat = "caution"
            else:
                threat = "danger"
            legs.append(
                RouteLeg(
                    system_id=sys_id,
                    system_name=name,
                    jumps_from_origin=depth,
                    kills_last_hour=kills,
                    threat_level=threat,
                )
            )
        return legs

    async def _bfs_path(
        self, origin: int, destination: int, max_hops: int
    ) -> list[int] | None:
        """Return the shortest path as a list of system IDs, or None."""
        if origin == destination:
            return [origin]
        queue: deque[list[int]] = deque([[origin]])
        visited: set[int] = {origin}
        while queue:
            path = queue.popleft()
            if len(path) > max_hops + 1:
                return None
            current = path[-1]
            for neighbor in await self.get_neighbors(current):
                if neighbor == destination:
                    return path + [neighbor]
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])
        return None

    # ------------------------------------------------------------------
    # Internal ESI helpers
    # ------------------------------------------------------------------

    async def _search_system(self, name: str) -> int | None:
        if not _HTTPX_AVAILABLE:
            return None
        url = f"{_ESI_BASE}/v2/search/"
        params = {
            "categories": "solar_system",
            "search": name,
            "strict": "true",
            "datasource": "tranquility",
        }
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                ids = resp.json().get("solar_system", [])
                return ids[0] if ids else None
        except Exception as exc:
            logger.debug("ESI system search failed for %r: %s", name, exc)
            return None

    async def _fetch_system_name(self, system_id: int) -> str | None:
        if not _HTTPX_AVAILABLE:
            return None
        url = f"{_ESI_BASE}/v4/universe/systems/{system_id}/"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                # Also cache stargates list for later gate-count use
                if "stargates" in data and system_id not in self._stargates:
                    self._stargates[system_id] = data["stargates"]
                return data.get("name")
        except Exception as exc:
            logger.debug("ESI system fetch failed for %d: %s", system_id, exc)
            return None

    async def _fetch_neighbors(self, system_id: int) -> list[int]:
        """Resolve stargates → destination system IDs."""
        if not _HTTPX_AVAILABLE:
            return []
        # Fetch system data to get stargate IDs if not already cached
        if system_id not in self._stargates:
            url = f"{_ESI_BASE}/v4/universe/systems/{system_id}/"
            try:
                async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                    if "name" in data:
                        self._id_to_name[system_id] = data["name"]
                        self._name_to_id[data["name"].lower()] = system_id
                    self._stargates[system_id] = data.get("stargates", [])
            except Exception as exc:
                logger.debug("ESI system fetch failed for %d: %s", system_id, exc)
                return []

        stargate_ids = self._stargates.get(system_id, [])
        if not stargate_ids:
            return []

        # Resolve stargates concurrently (cap at 8 to avoid hammering API)
        tasks = [self._fetch_stargate_dest(sg_id) for sg_id in stargate_ids[:8]]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, int)]

    async def _fetch_stargate_dest(self, stargate_id: int) -> int | None:
        url = f"{_ESI_BASE}/v2/universe/stargates/{stargate_id}/"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json().get("destination", {}).get("system_id")
        except Exception as exc:
            logger.debug("ESI stargate fetch failed for %d: %s", stargate_id, exc)
            return None

    async def _fetch_sov_map(self) -> dict[int, dict]:
        """Fetch bulk sovereignty map from ESI. Returns {system_id: sov_entry}."""
        if not _HTTPX_AVAILABLE:
            return {}
        url = f"{_ESI_BASE}/v1/sovereignty/map/"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                entries = resp.json()
                return {e["system_id"]: e for e in entries if "system_id" in e}
        except Exception as exc:
            logger.debug("ESI sov map fetch failed: %s", exc)
            return {}

    async def _get_entity_name(self, entity_type: str, entity_id: int) -> str | None:
        if entity_id in self._entity_names:
            return self._entity_names[entity_id]
        url = f"{_ESI_BASE}/v4/{entity_type}/{entity_id}/"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                name = resp.json().get("name")
                if name:
                    self._entity_names[entity_id] = name
                return name
        except Exception as exc:
            logger.debug("ESI entity name fetch failed (%d): %s", entity_id, exc)
            return None

    async def _zkb_kills_last_hour(self, system_id: int) -> int:
        """Return the number of kills in *system_id* in the last 3600 seconds."""
        if not _HTTPX_AVAILABLE:
            return 0
        url = f"{_ZKB_BASE}/kills/solarSystemID/{system_id}/pastSeconds/3600/"
        try:
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT,
                headers={"User-Agent": "EVEAlert/3.2"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                return len(data) if isinstance(data, list) else 0
        except Exception as exc:
            logger.debug("ZKB kills failed for %d: %s", system_id, exc)
            return 0


# Module-level singleton
_cache: UniverseCache | None = None


def get_universe_cache() -> UniverseCache:
    global _cache
    if _cache is None:
        _cache = UniverseCache()
    return _cache
