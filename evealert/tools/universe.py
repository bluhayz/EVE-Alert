"""EVE universe data cache for EVE Alert.

Provides system ID/name resolution, jump-graph BFS for neighbor discovery,
system classification (pipe/pocket/crossroads), and sovereignty data.

All data comes from public ESI endpoints — no authentication required.
Results are cached with appropriate TTLs to avoid hammering the API.
"""

import asyncio
import heapq
import itertools
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import NamedTuple

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

from evealert.tools.http_common import DEFAULT_HEADERS
from evealert.tools.zkillboard import clean_zkb_entries

logger = logging.getLogger("alert.universe")

_ESI_BASE = "https://esi.evetech.net"
_ZKB_BASE = "https://zkillboard.com/api"
_HTTP_TIMEOUT = 8.0

# Cache TTLs
_NAME_CACHE_TTL = 86400  # system names never change — cache for 24 h
_SOV_CACHE_TTL = 300  # sovereignty refreshes every 5 min
# #232: short retry backoff after a failed sov-map fetch -- long enough
# that an ESI outage isn't hammered on every get_sovereignty() call, short
# enough that a transient hiccup doesn't leave stale/empty sov data
# cached for the full 5-minute TTL.
_SOV_FAILURE_RETRY_SECONDS = 30
_KILL_COUNT_CACHE_TTL = 300  # #172: zKB etiquette -- reuse kill counts for 5 min

# #177: soak reliability -- system/neighbor identity never changes, so
# caching it forever is correct, but a long-running session with no size
# guard at all is a latent unbounded-growth risk (e.g. a bug that feeds
# garbage IDs in a loop). EVE has ~8000 solar systems; this ceiling is
# comfortably above normal usage and only protects against a pathological
# case, not normal route-checking/multi-client activity.
_MAX_IDENTITY_CACHE_SIZE = 10_000

# #227: bounded concurrency for per-system stargate-destination fetches --
# replaces the old stargate_ids[:8] truncation (which silently discarded
# gates beyond the 8th instead of capping request concurrency).
_MAX_CONCURRENT_STARGATE_FETCHES = 8

# #172: route-avoidance search tuning
_ROUTE_SEARCH_MAX_HOPS = 30
_ROUTE_SEARCH_MAX_ZKB_CALLS = 50  # cap total zKB probes per suggestion
_KILL_PENALTY_PER_KILL = 0.5
_KILL_PENALTY_CAP_KILLS = 10  # kills beyond this add no further penalty
_LOWSEC_PENALTY = 1.0
_NULLSEC_PENALTY = 2.0
_CAMP_PENALTY = 5.0  # soft dependency on #170 -- caller supplies camped_system_ids


async def resolve_ids(names: list[str]) -> dict:
    """Resolve a list of names to IDs via ``POST /universe/ids/``.

    Returns the raw ESI payload, e.g.
    ``{"systems": [{"id": 30000142, "name": "Jita"}], "characters": [...], ...}``.

    This replaces the public ``GET /search/`` endpoint family, which CCP
    removed (only the authenticated ``/characters/{id}/search/`` remains).
    ``/universe/ids/`` needs no auth and does exact, case-insensitive
    full-name matching. See issue #110.
    """
    if not _HTTPX_AVAILABLE or not names:
        return {}
    url = f"{_ESI_BASE}/latest/universe/ids/"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=DEFAULT_HEADERS) as client:
            resp = await client.post(
                url,
                json=list(names),
                params={"datasource": "tranquility"},
                headers=DEFAULT_HEADERS,
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.debug("ESI /universe/ids/ failed for %r: %s", names, exc)
        return {}


async def resolve_names(ids: list[int]) -> dict[int, str]:
    """Resolve a list of entity IDs to names via ``POST /universe/names/``
    (#173) -- the inverse of resolve_ids(). Returns {id: name}; IDs ESI
    doesn't recognize (or that fail to resolve) are simply absent from the
    result rather than raising.
    """
    if not _HTTPX_AVAILABLE or not ids:
        return {}
    url = f"{_ESI_BASE}/latest/universe/names/"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=DEFAULT_HEADERS) as client:
            resp = await client.post(
                url,
                json=list(ids),
                params={"datasource": "tranquility"},
                headers=DEFAULT_HEADERS,
            )
            resp.raise_for_status()
            entries = resp.json()
            return {e["id"]: e["name"] for e in entries if "id" in e and "name" in e}
    except Exception as exc:
        logger.debug("ESI /universe/names/ failed for %r: %s", ids, exc)
        return {}


async def resolve_single_id(name: str, category: str) -> int | None:
    """Resolve one *name* within a ``/universe/ids/`` *category* key
    (``"systems"``, ``"characters"``, ``"corporations"``, ``"alliances"``).

    Prefers an exact case-insensitive name match; falls back to the first
    entry in the category if the server-returned names don't line up.
    """
    data = await resolve_ids([name])
    entries = data.get(category, [])
    for entry in entries:
        if entry.get("name", "").lower() == name.lower():
            return entry.get("id")
    return entries[0].get("id") if entries else None


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
    has_camp: bool = False  # #170: active gate camp detected in this system


@dataclass
class RouteSuggestion:
    """#172: shortest vs. threat-weighted route, both fully annotated so
    the UI can render 'Shortest: 8j (2 dangerous) — Suggested: 10j (0
    dangerous)' with per-leg detail for both."""

    shortest: list[RouteLeg]
    suggested: list[RouteLeg]
    detoured: bool  # True when suggested actually differs from shortest


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
        # system_id → security_status (-1.0..1.0), never changes -- cache forever
        self._security_status: dict[int, float] = {}
        # system_id → (fetched_at, kill_count) -- #172: 5-min TTL so route
        # search (which probes many systems) doesn't hammer zKB
        self._kill_count_cache: dict[int, tuple[float, int]] = {}

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
            # #227: don't seed _id_to_name with the caller's (possibly
            # lowercased/mistyped) casing -- get_system_name() fetches and
            # caches the canonical ESI-returned name on first use instead.
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
        if neighbors is None:
            # #227: a transient ESI failure -- don't cache it as a
            # permanent empty neighbor list; let the next call retry.
            return []
        # #177: simple size guard, not an LRU -- system identity never
        # changes so there's no "staleness" to purge by age, only a
        # pathological-growth backstop. Clearing (rather than evicting
        # piecemeal) is safe: this is a pure cache, re-fetching from ESI
        # is the only cost.
        if len(self._neighbors) >= _MAX_IDENTITY_CACHE_SIZE:
            logger.warning(
                "UniverseCache: _neighbors exceeded %d entries -- clearing "
                "(this should not happen in normal use)",
                _MAX_IDENTITY_CACHE_SIZE,
            )
            self._neighbors.clear()
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
            fresh_map = await self._fetch_sov_map()
            if fresh_map is not None:
                sov_map = fresh_map
                self._sov_cache = (sov_map, time.time())
            else:
                # #232: fetch failed -- keep serving the previous map
                # (possibly stale, possibly {} if none has ever succeeded)
                # rather than caching {} as a successful "no sov anywhere"
                # result for the full TTL. Retry again soon, not on every
                # single call, so an ESI outage isn't hammered.
                self._sov_cache = (
                    sov_map, time.time() - _SOV_CACHE_TTL + _SOV_FAILURE_RETRY_SECONDS
                )

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

    async def get_route(
        self, origin_id: int, destination_id: int, max_hops: int = _ROUTE_SEARCH_MAX_HOPS
    ) -> list[int] | None:
        """Return the shortest path from *origin_id* to *destination_id* as a
        list of system IDs (inclusive of both ends), or None if no path is
        found within *max_hops*.

        #226: this public wrapper around _bfs_path() was missing entirely --
        both _lookup_jump_distance() (alertmanager.py) and
        pilot_history_analytics._is_plausible_transition() call
        cache.get_route() and silently no-op/soft-fail via their surrounding
        except-blocks when it raises AttributeError. Tests never caught it
        because they mock get_route() into existence on a stand-in cache.
        """
        return await self._bfs_path(origin_id, destination_id, max_hops)

    # ------------------------------------------------------------------
    # Route threat assessment
    # ------------------------------------------------------------------

    async def route_threat(
        self,
        origin_id: int,
        destination_id: int,
        max_hops: int = 15,
        camped_system_ids: set[int] | None = None,
    ) -> list[RouteLeg] | None:
        """Compute shortest path from origin to destination and check kill activity.

        Returns an ordered list of RouteLeg objects (one per hop, excluding
        the origin), or None if no path is found within max_hops.

        *camped_system_ids* (#170): systems with an active gate camp
        (evealert.tools.gatecamp.get_active_camps()) are always marked
        "danger" and has_camp=True regardless of the raw zKB kill count --
        a fresh camp can outpace the hourly kill-count cache.
        """
        path = await self._bfs_path(origin_id, destination_id, max_hops)
        if not path:
            return None
        return await self._annotate_path(path, camped_system_ids or set())

    async def _annotate_path(
        self,
        path: list[int],
        camped_system_ids: set[int],
        kill_probe=None,
    ) -> list[RouteLeg]:
        """Shared per-leg annotation (name, kill count, threat tier, camp
        flag) used by both route_threat() and suggest_safer_route() so the
        two share one definition of what "danger" means.

        *kill_probe*, when given, replaces the direct _zkb_kills_last_hour
        call -- suggest_safer_route() passes a budget-and-memoization-aware
        probe shared with its own weighted search so the whole call stays
        within the zKB etiquette cap regardless of how many separate
        phases end up asking about the same system. route_threat() leaves
        this None and keeps its original uncapped-per-leg behavior.
        """
        probe = kill_probe or self._zkb_kills_last_hour
        legs: list[RouteLeg] = []
        for depth, sys_id in enumerate(path[1:], start=1):
            name = await self.get_system_name(sys_id) or str(sys_id)
            kills = await probe(sys_id)
            has_camp = sys_id in camped_system_ids
            if has_camp:
                threat = "danger"
            elif kills == 0:
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
                    has_camp=has_camp,
                )
            )
        return legs

    # ------------------------------------------------------------------
    # Route-avoidance advisor (#172)
    # ------------------------------------------------------------------

    async def suggest_safer_route(
        self,
        origin_id: int,
        destination_id: int,
        *,
        max_hops: int = _ROUTE_SEARCH_MAX_HOPS,
        camped_system_ids: set[int] | None = None,
    ) -> RouteSuggestion | None:
        """Weighted-Dijkstra alternative to route_threat()'s plain-BFS
        shortest path: prefers systems with fewer kills, no active gate
        camp, and higher security status, even at the cost of extra jumps.

        Returns both the shortest and suggested routes (identical, with
        detoured=False, when the shortest path is already the safest one
        found) so the UI can show "Shortest: Nj — Suggested: Mj" either way.
        """
        shortest_path = await self._bfs_path(origin_id, destination_id, max_hops)
        if not shortest_path:
            return None

        camped = camped_system_ids or set()
        # Shared across the weighted search AND both annotation passes
        # below, so the zKB etiquette cap (_ROUTE_SEARCH_MAX_ZKB_CALLS)
        # applies to the whole suggestion, not just the search phase --
        # otherwise annotating a long shortest-path route that the search
        # didn't fully explore could re-blow the budget on its own.
        kill_lookup: dict[int, int] = {}
        zkb_calls_remaining = [_ROUTE_SEARCH_MAX_ZKB_CALLS]

        async def probe_kills(system_id: int) -> int:
            if system_id in kill_lookup:
                return kill_lookup[system_id]
            if zkb_calls_remaining[0] > 0:
                count = await self._zkb_kills_last_hour(system_id)
                zkb_calls_remaining[0] -= 1
            else:
                count = 0
            kill_lookup[system_id] = count
            return count

        weighted_path = await self._weighted_path(
            origin_id, destination_id, max_hops, camped, probe_kills
        )
        if not weighted_path:
            weighted_path = shortest_path

        shortest_legs = await self._annotate_path(shortest_path, camped, probe_kills)
        detoured = weighted_path != shortest_path
        suggested_legs = (
            await self._annotate_path(weighted_path, camped, probe_kills)
            if detoured else shortest_legs
        )
        return RouteSuggestion(
            shortest=shortest_legs, suggested=suggested_legs, detoured=detoured
        )

    async def _weighted_path(
        self,
        origin_id: int,
        destination_id: int,
        max_hops: int,
        camped_system_ids: set[int],
        probe_kills,
    ) -> list[int] | None:
        """Dijkstra over the jump graph; edge weight for entering a system
        is 1 + penalty(system), where penalty comes from recent kills (via
        the shared, budget-capped probe_kills callback), an active gate
        camp, and low/null-sec status. Weights are memoized per system for
        this search: Dijkstra relaxes the same neighbor from multiple
        predecessors before it's finalized, and without memoizing that
        would recompute (and re-probe) the same system many times."""
        weight_cache: dict[int, float] = {}

        async def entry_weight(system_id: int) -> float:
            if system_id in weight_cache:
                return weight_cache[system_id]
            penalty = _CAMP_PENALTY if system_id in camped_system_ids else 0.0
            kills = await probe_kills(system_id)
            penalty += min(kills, _KILL_PENALTY_CAP_KILLS) * _KILL_PENALTY_PER_KILL
            sec = await self.get_security_status(system_id)
            if sec is not None:
                if sec < 0.0:
                    penalty += _NULLSEC_PENALTY
                elif sec < 0.5:
                    penalty += _LOWSEC_PENALTY
            weight = 1.0 + penalty
            weight_cache[system_id] = weight
            return weight

        counter = itertools.count()
        # heap items: (cumulative_weight, tie_breaker, hop_count, system_id)
        heap = [(0.0, next(counter), 0, origin_id)]
        best_weight: dict[int, float] = {origin_id: 0.0}
        prev: dict[int, int] = {}
        visited: set[int] = set()

        while heap:
            weight, _, hops, current = heapq.heappop(heap)
            if current in visited:
                continue
            visited.add(current)
            if current == destination_id:
                break
            if hops >= max_hops:
                continue
            for neighbor in await self.get_neighbors(current):
                if neighbor in visited:
                    continue
                new_weight = weight + await entry_weight(neighbor)
                if new_weight < best_weight.get(neighbor, float("inf")):
                    best_weight[neighbor] = new_weight
                    prev[neighbor] = current
                    heapq.heappush(heap, (new_weight, next(counter), hops + 1, neighbor))

        if destination_id != origin_id and destination_id not in prev:
            return None
        path = [destination_id]
        while path[-1] != origin_id:
            path.append(prev[path[-1]])
        path.reverse()
        return path

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
        # POST /universe/ids/ (GET /search/ was removed by CCP — #110)
        return await resolve_single_id(name, "systems")

    async def _fetch_system_name(self, system_id: int) -> str | None:
        if not _HTTPX_AVAILABLE:
            return None
        url = f"{_ESI_BASE}/v4/universe/systems/{system_id}/"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=DEFAULT_HEADERS) as client:
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

    async def _fetch_neighbors(self, system_id: int) -> list[int] | None:
        """Resolve stargates → destination system IDs.

        Returns None on a fetch failure -- get_neighbors() must not cache
        that as a permanent empty neighbor list (#227). Returns [] only
        when the system genuinely has zero stargates.
        """
        if not _HTTPX_AVAILABLE:
            return None
        # Fetch system data to get stargate IDs if not already cached
        if system_id not in self._stargates:
            url = f"{_ESI_BASE}/v4/universe/systems/{system_id}/"
            try:
                async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=DEFAULT_HEADERS) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                    if "name" in data:
                        self._id_to_name[system_id] = data["name"]
                        self._name_to_id[data["name"].lower()] = system_id
                    self._stargates[system_id] = data.get("stargates", [])
            except Exception as exc:
                logger.debug("ESI system fetch failed for %d: %s", system_id, exc)
                return None

        stargate_ids = self._stargates.get(system_id, [])
        if not stargate_ids:
            return []

        # #227: resolve ALL stargates (previously truncated to the first 8,
        # silently discarding the rest and permanently mis-recording the
        # neighbor list for any system with more than 8 gates -- common at
        # trade hubs). Concurrency is bounded via semaphore instead.
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_STARGATE_FETCHES)

        async def _bounded_fetch(sg_id: int) -> int | None:
            async with semaphore:
                return await self._fetch_stargate_dest(sg_id)

        tasks = [_bounded_fetch(sg_id) for sg_id in stargate_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        resolved = [r for r in results if isinstance(r, int)]
        if stargate_ids and not resolved:
            # Every stargate fetch failed -- this is a fetch failure, not
            # a genuinely gateless system. Don't cache it as one.
            return None
        return resolved

    async def _fetch_stargate_dest(self, stargate_id: int) -> int | None:
        url = f"{_ESI_BASE}/v2/universe/stargates/{stargate_id}/"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=DEFAULT_HEADERS) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json().get("destination", {}).get("system_id")
        except Exception as exc:
            logger.debug("ESI stargate fetch failed for %d: %s", stargate_id, exc)
            return None

    async def _fetch_sov_map(self) -> dict[int, dict] | None:
        """Fetch bulk sovereignty map from ESI. Returns {system_id: sov_entry},
        or None on a fetch failure (#232) -- get_sovereignty() must not cache
        that as a genuinely-empty sov map for the full TTL."""
        if not _HTTPX_AVAILABLE:
            return None
        url = f"{_ESI_BASE}/v1/sovereignty/map/"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=DEFAULT_HEADERS) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                entries = resp.json()
                return {e["system_id"]: e for e in entries if "system_id" in e}
        except Exception as exc:
            logger.debug("ESI sov map fetch failed: %s", exc)
            return None

    async def _get_entity_name(self, entity_type: str, entity_id: int) -> str | None:
        if entity_id in self._entity_names:
            return self._entity_names[entity_id]
        url = f"{_ESI_BASE}/v4/{entity_type}/{entity_id}/"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=DEFAULT_HEADERS) as client:
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
        """Return the number of kills in *system_id* in the last 3600 seconds.

        Cached for _KILL_COUNT_CACHE_TTL (#172: suggest_safer_route() probes
        many systems per search -- zKB etiquette requires reusing counts
        rather than re-fetching every candidate on every hop expansion).
        """
        cached = self._kill_count_cache.get(system_id)
        if cached is not None and time.time() - cached[0] < _KILL_COUNT_CACHE_TTL:
            return cached[1]
        if not _HTTPX_AVAILABLE:
            return 0
        url = f"{_ZKB_BASE}/kills/solarSystemID/{system_id}/pastSeconds/3600/"
        try:
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT,
                headers=DEFAULT_HEADERS,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                count = len(clean_zkb_entries(data))
                self._kill_count_cache[system_id] = (time.time(), count)
                return count
        except Exception as exc:
            logger.debug("ZKB kills failed for %d: %s", system_id, exc)
            return 0

    def purge_expired_kill_counts(self) -> int:
        """Drop _kill_count_cache entries past _KILL_COUNT_CACHE_TTL (#177).

        The TTL check inside _zkb_kills_last_hour() only skips a stale
        entry on read -- it never evicts one, so a system probed once
        during a single route search and never queried again sits in the
        cache indefinitely. Returns the number of entries removed.
        """
        now = time.time()
        stale = [
            sid for sid, (ts, _) in self._kill_count_cache.items()
            if now - ts >= _KILL_COUNT_CACHE_TTL
        ]
        for sid in stale:
            del self._kill_count_cache[sid]
        return len(stale)

    async def get_security_status(self, system_id: int) -> float | None:
        """Return the system's ESI security_status (-1.0..1.0), cached
        forever (it never changes) -- used by suggest_safer_route()'s
        low/null-sec penalty (#172)."""
        if system_id in self._security_status:
            return self._security_status[system_id]
        if not _HTTPX_AVAILABLE:
            return None
        url = f"{_ESI_BASE}/v4/universe/systems/{system_id}/"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=DEFAULT_HEADERS) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                sec = resp.json().get("security_status")
                if sec is not None:
                    sec = float(sec)
                    self._security_status[system_id] = sec
                return sec
        except Exception as exc:
            logger.debug("ESI security status fetch failed for %d: %s", system_id, exc)
            return None


# Module-level singleton
_cache: UniverseCache | None = None


def get_universe_cache() -> UniverseCache:
    global _cache
    if _cache is None:
        _cache = UniverseCache()
    return _cache
