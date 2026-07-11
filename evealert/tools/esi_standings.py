"""ESI (Eve Swagger Interface) public-endpoint lookups for EVE Alert.

Fetches character → corporation → alliance information using only
unauthenticated public ESI endpoints. No OAuth is required.

Results are cached per character name with a configurable TTL so rapid
alarm bursts don't hammer the API.
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

logger = logging.getLogger("alert.esi")

_ESI_BASE = "https://esi.evetech.net"
_HTTP_TIMEOUT = 8.0
_CACHE_TTL = 600  # 10 minutes — standings/corps rarely change mid-session

# EVE chat log string that marks a character joining a channel
_JOIN_MARKER = "joined the channel"


class CharacterInfo(NamedTuple):
    character_id: int
    name: str
    corporation_id: int
    corporation_name: str
    alliance_id: int | None
    alliance_name: str | None


class EsiLookup:
    """Async ESI client with TTL cache for character → corp → alliance lookups."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, CharacterInfo | None]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def lookup_character(self, name: str) -> CharacterInfo | None:
        """Return cached-or-fresh CharacterInfo for *name*, or None on error."""
        if not _HTTPX_AVAILABLE:
            logger.debug("httpx not available; ESI lookup disabled.")
            return None

        key = name.lower().strip()
        cached_at, cached_result = self._cache.get(key, (0.0, None))
        if time.time() - cached_at < _CACHE_TTL:
            return cached_result

        result = await self._fetch_character(name)
        self._cache[key] = (time.time(), result)
        return result

    async def lookup_many(self, names: list[str]) -> list[CharacterInfo]:
        """Concurrently look up multiple character names and return hits."""
        tasks = [self.lookup_character(n) for n in names]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, CharacterInfo)]

    def clear_cache(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_character(self, name: str) -> CharacterInfo | None:
        char_id = await self._search_character_id(name)
        if char_id is None:
            return None

        char_data = await self._get_character(char_id)
        if char_data is None:
            return None

        corp_id: int = char_data.get("corporation_id", 0)
        alliance_id: int | None = char_data.get("alliance_id")

        corp_name = (
            await self._get_name("/v5/corporations/{id}/", corp_id)
            if corp_id
            else "Unknown"
        )
        alliance_name = (
            await self._get_name("/v5/alliances/{id}/", alliance_id)
            if alliance_id
            else None
        )

        return CharacterInfo(
            character_id=char_id,
            name=name,
            corporation_id=corp_id,
            corporation_name=corp_name or "Unknown",
            alliance_id=alliance_id,
            alliance_name=alliance_name,
        )

    async def _search_character_id(self, name: str) -> int | None:
        url = f"{_ESI_BASE}/v2/characters/search/"
        params = {
            "categories": "character",
            "search": name,
            "strict": "true",
            "datasource": "tranquility",
        }
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                ids = resp.json().get("character", [])
                return ids[0] if ids else None
        except Exception as exc:
            logger.debug("ESI character search failed for %r: %s", name, exc)
            return None

    async def _get_character(self, char_id: int) -> dict | None:
        url = f"{_ESI_BASE}/v5/characters/{char_id}/"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.debug("ESI character fetch failed for %d: %s", char_id, exc)
            return None

    async def _get_name(self, path_template: str, entity_id: int) -> str | None:
        url = f"{_ESI_BASE}{path_template.replace('{id}', str(entity_id))}"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json().get("name")
        except Exception as exc:
            logger.debug("ESI name lookup failed for %d: %s", entity_id, exc)
            return None


def extract_joining_characters(log_lines: list[str]) -> list[str]:
    """Parse EVE chat log lines and return names of characters who just joined.

    EVE log format for joins:
        ``EVE System > Channel MOTD: ...``  (skipped)
        ``Pilot Name > joined the channel``  (captured)
    """
    names: list[str] = []
    for line in log_lines:
        stripped = line.strip()
        if _JOIN_MARKER in stripped:
            # Format: "[ 2024.05.01 15:30:22 ] Pilot Name > joined the channel"
            # Strip timestamp prefix if present
            if "] " in stripped:
                stripped = stripped.split("] ", 1)[-1]
            if " > " in stripped:
                name = stripped.split(" > ")[0].strip()
                if name and name != "EVE System":
                    names.append(name)
    return names


# Module-level singleton
_client: EsiLookup | None = None


def get_esi_client() -> EsiLookup:
    global _client
    if _client is None:
        _client = EsiLookup()
    return _client
