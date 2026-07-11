"""Zkillboard + ESI integration for EVE Alert.

Fetches recent kill activity for a solar system by:
1. Looking up the solar system ID via the ESI universe-search endpoint.
2. Querying the Zkillboard API for the most recent kills in that system.

All network calls are performed via asyncio-friendly httpx (with a timeout).
Results are cached per system name to avoid hammering the API.
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

logger = logging.getLogger("alert.zkillboard")

# ESI base URL
_ESI_BASE = "https://esi.evetech.net/latest"
# Zkillboard RedisQ / API
_ZKB_BASE = "https://zkillboard.com/api"
# How many kills to request per lookup
_ZKB_LIMIT = 5
# Seconds before a cached result expires
_CACHE_TTL = 120
# HTTP timeout in seconds
_HTTP_TIMEOUT = 10.0


class KillSummary(NamedTuple):
    kill_id: int
    kill_time: str  # ISO timestamp from ESI killmail
    victim_ship: str
    victim_name: str
    total_value: float  # ISK


class ZkillboardClient:
    """Async client for ESI + Zkillboard lookups with a simple TTL cache."""

    def __init__(self) -> None:
        # Cache: {system_name: (fetch_time_float, list[KillSummary] | None)}
        self._cache: dict[str, tuple[float, list[KillSummary] | None]] = {}
        self._system_id_cache: dict[str, int | None] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_recent_kills(
        self, system_name: str, limit: int = _ZKB_LIMIT
    ) -> list[KillSummary] | None:
        """Return up to *limit* recent kills for *system_name*, or None on error.

        Results are cached for ``_CACHE_TTL`` seconds so rapid calls (e.g. on
        every alarm trigger) don't spam the public APIs.
        """
        if not _HTTPX_AVAILABLE:
            logger.warning("httpx not installed; Zkillboard integration disabled.")
            return None

        key = system_name.lower()
        cached_at, cached_result = self._cache.get(key, (0.0, None))
        if time.time() - cached_at < _CACHE_TTL:
            return cached_result

        system_id = await self._resolve_system_id(system_name)
        if system_id is None:
            self._cache[key] = (time.time(), None)
            return None

        kills = await self._fetch_kills(system_id, limit)
        self._cache[key] = (time.time(), kills)
        return kills

    def clear_cache(self) -> None:
        self._cache.clear()
        self._system_id_cache.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _resolve_system_id(self, system_name: str) -> int | None:
        """Look up solar system ID via ESI universe-search."""
        key = system_name.lower()
        if key in self._system_id_cache:
            return self._system_id_cache[key]

        url = f"{_ESI_BASE}/search/"
        params = {
            "categories": "solar_system",
            "search": system_name,
            "strict": "true",
            "datasource": "tranquility",
        }
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                ids = data.get("solar_system", [])
                system_id = ids[0] if ids else None
        except Exception as exc:
            logger.debug("ESI system lookup failed for %r: %s", system_name, exc)
            system_id = None

        self._system_id_cache[key] = system_id
        return system_id

    async def _fetch_kills(
        self, system_id: int, limit: int
    ) -> list[KillSummary] | None:
        """Fetch recent kills from Zkillboard for *system_id*."""
        url = f"{_ZKB_BASE}/kills/solarSystemID/{system_id}/limit/{limit}/"
        try:
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT,
                headers={
                    "Accept-Encoding": "gzip",
                    "User-Agent": "EVEAlert/2.5 contact@example.com",
                },
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                entries = resp.json()
        except Exception as exc:
            logger.debug("Zkillboard fetch failed for system %d: %s", system_id, exc)
            return None

        if not isinstance(entries, list):
            return None

        results: list[KillSummary] = []
        # Fetch ESI killmail details for each entry concurrently (max 5)
        tasks = [
            self._fetch_killmail_detail(entry)
            for entry in entries[:limit]
            if "killmail_id" in entry and "zkb" in entry
        ]
        summaries = await asyncio.gather(*tasks, return_exceptions=True)
        for s in summaries:
            if isinstance(s, KillSummary):
                results.append(s)

        return results or None

    async def _fetch_killmail_detail(self, entry: dict) -> KillSummary | None:
        """Fetch ESI killmail detail and return a KillSummary."""
        kill_id = entry.get("killmail_id")
        hash_val = entry.get("zkb", {}).get("hash", "")
        total_value = entry.get("zkb", {}).get("totalValue", 0.0)

        if not kill_id or not hash_val:
            return None

        url = f"{_ESI_BASE}/killmails/{kill_id}/{hash_val}/"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                km = resp.json()
        except Exception as exc:
            logger.debug("ESI killmail fetch failed for %d: %s", kill_id, exc)
            return None

        victim = km.get("victim", {})
        ship_type_id = victim.get("ship_type_id", 0)
        character_id = victim.get("character_id", 0)
        kill_time = km.get("killmail_time", "?")

        # Best-effort name resolution (fire-and-forget, fall back to IDs)
        ship_name = await self._resolve_type_name(ship_type_id)
        char_name = await self._resolve_character_name(character_id)

        return KillSummary(
            kill_id=kill_id,
            kill_time=kill_time,
            victim_ship=ship_name or f"TypeID:{ship_type_id}",
            victim_name=char_name or f"CharID:{character_id}",
            total_value=float(total_value),
        )

    async def _resolve_type_name(self, type_id: int) -> str | None:
        if not type_id:
            return None
        url = f"{_ESI_BASE}/universe/types/{type_id}/"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json().get("name")
        except Exception:
            return None

    async def _resolve_character_name(self, character_id: int) -> str | None:
        if not character_id:
            return None
        url = f"{_ESI_BASE}/characters/{character_id}/"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json().get("name")
        except Exception:
            return None


# Module-level singleton shared across the application
_client: ZkillboardClient | None = None


def get_client() -> ZkillboardClient:
    global _client
    if _client is None:
        _client = ZkillboardClient()
    return _client
