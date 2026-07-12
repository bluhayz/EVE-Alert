"""ESI (Eve Swagger Interface) public-endpoint lookups for EVE Alert.

Fetches character → corporation → alliance information using only
unauthenticated public ESI endpoints. No OAuth is required.

Results are cached per character name with a configurable TTL so rapid
alarm bursts don't hammer the API.

v3.1 additions:
- CharacterInfo extended with age_days, security_status, corp_history_count
- KillProfile namedtuple with 30-day kill/loss summary and top ship
- get_zkillboard_profile() for kill history lookup
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import NamedTuple

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

logger = logging.getLogger("alert.esi")

_ESI_BASE = "https://esi.evetech.net"
_ZKB_BASE = "https://zkillboard.com/api"
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
    # v3.1: background check fields
    age_days: int  # days since character creation
    security_status: float  # EVE security status (< -5 = flashy in low-sec)
    corp_history_count: int  # total number of corporations held


class KillProfile(NamedTuple):
    kills_30d: int
    losses_30d: int
    top_ship: str | None  # most-used ship in recent kills
    danger_ratio: float  # kills / (kills + losses), 0.0–1.0


class EsiLookup:
    """Async ESI client with TTL cache for character → corp → alliance lookups."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, CharacterInfo | None]] = {}
        self._zkb_cache: dict[int, tuple[float, KillProfile | None]] = {}

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

    async def get_zkillboard_profile(self, character_id: int) -> KillProfile | None:
        """Return Zkillboard kill/loss summary for *character_id* (cached, TTL 10 min)."""
        if not _HTTPX_AVAILABLE:
            return None

        cached_at, cached_result = self._zkb_cache.get(character_id, (0.0, None))
        if time.time() - cached_at < _CACHE_TTL:
            return cached_result

        result = await self._fetch_zkb_profile(character_id)
        self._zkb_cache[character_id] = (time.time(), result)
        return result

    def clear_cache(self) -> None:
        self._cache.clear()
        self._zkb_cache.clear()

    # ------------------------------------------------------------------
    # Internal helpers — character / corp / alliance
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

        # Background check fields (all from the same _get_character() response)
        birthday_str: str = char_data.get("birthday", "")
        age_days = _compute_age_days(birthday_str)
        security_status: float = float(char_data.get("security_status", 0.0))

        # Corp history requires a separate call
        corp_history_count = await self._get_corp_history_count(char_id)

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
            age_days=age_days,
            security_status=security_status,
            corp_history_count=corp_history_count,
        )

    async def _search_character_id(self, name: str) -> int | None:
        # GET /characters/search/ was removed by CCP; use the shared
        # POST /universe/ids/ resolver in universe.py (#110).
        from evealert.tools.universe import (  # pylint: disable=import-outside-toplevel
            resolve_single_id,
        )

        return await resolve_single_id(name, "characters")

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

    async def _get_corp_history_count(self, char_id: int) -> int:
        """Return the number of corporations a character has been in."""
        url = f"{_ESI_BASE}/v2/characters/{char_id}/corporationhistory/"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                history = resp.json()
                return len(history) if isinstance(history, list) else 0
        except Exception as exc:
            logger.debug("ESI corp history failed for %d: %s", char_id, exc)
            return 0

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

    # ------------------------------------------------------------------
    # Internal helpers — Zkillboard kill profile
    # ------------------------------------------------------------------

    async def _fetch_zkb_profile(self, character_id: int) -> KillProfile | None:
        """Fetch kill/loss stats and top ship from Zkillboard stats endpoint."""
        url = f"{_ZKB_BASE}/stats/characterID/{character_id}/"
        try:
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT,
                headers={"User-Agent": "EVEAlert/3.1 contact@example.com"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.debug("Zkillboard stats failed for %d: %s", character_id, exc)
            return None

        if not isinstance(data, dict):
            return None

        kills = int(data.get("shipsDestroyed", 0) or 0)
        losses = int(data.get("shipsLost", 0) or 0)
        total = kills + losses
        danger_ratio = kills / total if total > 0 else 0.0

        # top ship from topLists: look for the "ship" category
        top_ship: str | None = None
        top_lists = data.get("topLists", [])
        for entry in top_lists:
            if entry.get("type") == "ship" and entry.get("values"):
                top_ship = entry["values"][0].get("shipName") or entry["values"][0].get(
                    "name"
                )
                break

        return KillProfile(
            kills_30d=kills,
            losses_30d=losses,
            top_ship=top_ship,
            danger_ratio=round(danger_ratio, 2),
        )


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _compute_age_days(birthday_str: str) -> int:
    """Compute character age in days from an ISO-8601 birthday string."""
    if not birthday_str:
        return 0
    try:
        # ESI returns e.g. "2020-03-15T10:22:00Z"
        birthday = datetime.fromisoformat(birthday_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - birthday
        return max(0, delta.days)
    except (ValueError, TypeError):
        return 0


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
