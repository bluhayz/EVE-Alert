"""Kill-on-Sight (KOS) checker for EVE Alert.

Queries configurable KOS API endpoints to determine whether a pilot,
their corporation, or their alliance is flagged as Kill-on-Sight.

Supports:
  - CVA KOS API (https://kos.cva-eve.com/api/) — DISABLED by default
    (service is offline as of 2026-07; #135)
  - Any additional KOS API URL configured by the user
  - Local hostile list (pilot/corp/alliance names in settings.json)

Results are cached per character name for 10 minutes.
"""

import asyncio
import logging
import time

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

logger = logging.getLogger("alert.kos")

_HTTP_TIMEOUT = 6.0
_CACHE_TTL = 600  # 10 minutes

_CVA_KOS_URL = "https://kos.cva-eve.com/api/"


class KosResult:
    __slots__ = ("pilot", "is_kos", "source", "label")

    def __init__(self, pilot: str, is_kos: bool, source: str, label: str = "") -> None:
        self.pilot = pilot
        self.is_kos = is_kos
        self.source = source  # e.g. "CVA KOS", "local list", "custom API"
        self.label = label  # e.g. "KOS" or corp name from API


class KosChecker:
    """Check pilots against KOS APIs and a local hostile list."""

    def __init__(
        self,
        api_urls: list[str] | None = None,
        local_hostile_list: dict | None = None,
        cva_enabled: bool = False,  # disabled by default — CVA KOS domain offline (#135)
    ) -> None:
        self._api_urls: list[str] = api_urls or []
        self._local: dict = local_hostile_list or {}  # {name_lower: tier}
        self._cva_enabled = cva_enabled
        # Keyed by (pilot_lower, corp_lower, alliance_lower) -- see check().
        self._cache: dict[tuple[str, str, str], tuple[float, KosResult | None]] = {}
        self._dead_sources: set[str] = set()  # URLs disabled this session

    def update_local_list(self, hostile_list: dict) -> None:
        self._local = {k.lower(): v for k, v in hostile_list.items()}

    def reconfigure(
        self,
        api_urls: list[str] | None = None,
        local_hostile_list: dict | None = None,
        cva_enabled: bool | None = None,
    ) -> None:
        """Update settings in place without discarding the result cache."""
        if api_urls is not None:
            self._api_urls = list(api_urls)
        if local_hostile_list is not None:
            # #229/#235: lower-case keys like update_local_list() does --
            # _do_check() matches fragments against pilot.lower(), so a
            # capitalized entry set via reconfigure() previously never
            # matched anything.
            self._local = {k.lower(): v for k, v in local_hostile_list.items()}
        if cva_enabled is not None:
            self._cva_enabled = cva_enabled

    async def check(
        self, pilot_name: str, corp_name: str = "", alliance_name: str = ""
    ) -> KosResult | None:
        """Check if *pilot_name* (or their corp/alliance) is KOS.

        Returns a KosResult if KOS, or None if clean / unknown.
        """
        key = (pilot_name.lower(), corp_name.lower(), alliance_name.lower())
        cached_at, cached_result = self._cache.get(key, (0.0, None))
        if time.time() - cached_at < _CACHE_TTL:
            return cached_result

        result = await self._do_check(pilot_name, corp_name, alliance_name)
        self._cache[key] = (time.time(), result)
        return result

    async def check_many(self, pilots: list[tuple[str, str, str]]) -> list[KosResult]:
        """Check multiple (pilot, corp, alliance) tuples concurrently."""
        tasks = [self.check(p, c, a) for p, c, a in pilots]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, KosResult)]

    def purge_expired(self) -> int:
        """Drop _cache entries past _CACHE_TTL (#229, follow-up to #177:
        this is the second largest per-pilot cache -- one entry per
        distinct (pilot, corp, alliance) KOS-checked -- and the TTL check
        on read only skips a stale entry, it never evicts it). Returns the
        number of entries removed."""
        now = time.time()
        stale = [k for k, (ts, _) in self._cache.items() if now - ts >= _CACHE_TTL]
        for k in stale:
            del self._cache[k]
        return len(stale)

    async def _do_check(self, pilot: str, corp: str, alliance: str) -> KosResult | None:
        # 1. Local hostile list — instant, no network call
        for name_fragment, tier in self._local.items():
            if (
                name_fragment in pilot.lower()
                or name_fragment in corp.lower()
                or name_fragment in alliance.lower()
            ):
                return KosResult(pilot, True, "local list", f"tier:{tier}")

        if not _HTTPX_AVAILABLE:
            return None

        # 2. CVA KOS API — check pilot, then corp, then alliance (a pilot in
        #    a KOS corp/alliance is KOS even if personally clean, #101).
        #    NOTE: the CVA KOS service (kos.cva-eve.com) is frequently offline;
        #    failures degrade gracefully to None.
        if self._cva_enabled:
            for entity in (pilot, corp, alliance):
                if not entity:
                    continue
                result = await self._query_cva(entity)
                if result:
                    return result

        # 3. Custom API URLs — same pilot/corp/alliance sweep
        for url in self._api_urls:
            for entity in (pilot, corp, alliance):
                if not entity:
                    continue
                result = await self._query_custom(url, entity)
                if result:
                    return result

        return None

    async def _query_cva(self, pilot: str) -> KosResult | None:
        """Query the CVA KOS API for *pilot*."""
        if _CVA_KOS_URL in self._dead_sources:
            return None
        params = {"c": "json", "q": pilot, "type": "pilot", "details": "1"}
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(_CVA_KOS_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning(
                "CVA KOS source unreachable, disabling for this session: %s (%s)",
                _CVA_KOS_URL, exc,
            )
            self._dead_sources.add(_CVA_KOS_URL)
            return None
        except Exception as exc:
            logger.debug("CVA KOS query failed for %r: %s", pilot, exc)
            return None

        results = data.get("results", [])
        for entry in results:
            if entry.get("kos") is True:
                label = entry.get("label") or "KOS"
                return KosResult(pilot, True, "CVA KOS", label)
        return None

    async def _query_custom(self, base_url: str, pilot: str) -> KosResult | None:
        """Query a custom KOS API (same JSON format as CVA KOS expected)."""
        # Vet the user-supplied URL to avoid SSRF to loopback/metadata/private
        # hosts (#105).
        from evealert.tools.net_safety import (  # pylint: disable=import-outside-toplevel
            is_safe_public_url,
        )

        if not is_safe_public_url(base_url):
            logger.warning("Skipping unsafe KOS URL (must be https + public host).")
            return None
        params = {"c": "json", "q": pilot, "type": "pilot"}
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(base_url, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.debug(
                "Custom KOS query failed for %r at %r: %s", pilot, base_url, exc
            )
            return None

        results = data.get("results", [])
        for entry in results:
            if entry.get("kos") is True:
                return KosResult(
                    pilot, True, f"KOS:{base_url}", entry.get("label", "KOS")
                )
        return None


# Module-level singleton
_checker: KosChecker | None = None


def get_kos_checker(**kwargs) -> KosChecker:
    """Return the shared KosChecker singleton, creating it on first use.

    Previously any call passing kwargs rebuilt the singleton and discarded the
    10-minute cache; now the instance is created once and reused. Use
    :meth:`KosChecker.reconfigure` to change settings without losing the cache.
    """
    global _checker
    if _checker is None:
        _checker = KosChecker(**kwargs)
    elif kwargs:
        _checker.reconfigure(**kwargs)
    return _checker
