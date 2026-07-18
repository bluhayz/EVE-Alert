"""Constellation kill-activity heatmap for EVE Alert (#148).

Fetches kill history for all systems in a constellation from zKillboard
and builds per-system 24-bucket UTC histograms.  This is a planning tool
for AFK pilots to identify peak hostile hours before starting a session.

Usage
-----
    from evealert.tools.threat_heatmap import get_constellation_heatmap

    heatmap = await get_constellation_heatmap("1DQ1-A", days=7)
    for sys_name, entry in heatmap.items():
        print(f"{sys_name}: {entry.kills_7d} kills, peak at {entry.peak_hour_utc:02d}:00 UTC")

Results are cached per session to avoid hammering the API on repeated calls.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("alert.heatmap")

# Simple in-process cache: key = (system_name, days) → (timestamp, heatmap)
_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_TTL = 3600  # seconds


def purge_expired_cache() -> int:
    """Drop _CACHE entries past _CACHE_TTL (#177 soak reliability).

    The TTL check inside get_constellation_heatmap() only skips a stale
    entry on read -- it never evicts one, so a constellation looked up
    once (e.g. checked before undocking, never revisited) sits in this
    module-level cache indefinitely for the life of the process. Returns
    the number of entries removed.
    """
    now = time.time()
    stale = [key for key, (ts, _) in _CACHE.items() if now - ts >= _CACHE_TTL]
    for key in stale:
        del _CACHE[key]
    return len(stale)


@dataclass
class HeatmapEntry:
    system: str
    kills_24h: int
    kills_7d: int
    peak_hour_utc: int                   # 0–23 UTC hour with most kills
    kill_histogram: list[int] = field(default_factory=lambda: [0] * 24)


async def _esi_get_system(system_id: int) -> dict:
    """Fetch ESI /universe/systems/{id}/ (constellation_id lives here)."""
    from evealert.tools.http_common import DEFAULT_HEADERS  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    url = f"https://esi.evetech.net/latest/universe/systems/{system_id}/"
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=10) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()


async def _esi_get_constellation(constellation_id: int) -> dict:
    """Fetch ESI /universe/constellations/{id}/ → system IDs list."""
    from evealert.tools.http_common import DEFAULT_HEADERS  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    url = f"https://esi.evetech.net/latest/universe/constellations/{constellation_id}/"
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=10) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()


async def _esi_resolve_name(name: str) -> int | None:
    """POST /universe/ids/ to resolve a system name to its ID."""
    from evealert.tools.http_common import DEFAULT_HEADERS  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    url = "https://esi.evetech.net/latest/universe/ids/"
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=10) as client:
        r = await client.post(url, json=[name])
        r.raise_for_status()
        data = r.json()
        systems = data.get("systems", [])
        if systems:
            return systems[0]["id"]
    return None


async def _zkb_kills_for_system(system_id: int, days: int) -> list[dict]:
    """Fetch recent kills from zKillboard for a single system.

    Paginates up to 5 pages (≈ 1000 kills) so busy systems return accurate
    histograms instead of just the most-recent ~200 entries (#163).
    """
    from evealert.tools.http_common import DEFAULT_HEADERS  # noqa: PLC0415

    import asyncio as _asyncio  # noqa: PLC0415
    import httpx  # noqa: PLC0415

    past_seconds = days * 86400
    all_kills: list[dict] = []

    for page in range(1, 6):   # max 5 pages ≈ 1 000 kills
        url = (
            f"https://zkillboard.com/api/kills/solarSystemID/{system_id}/"
            f"pastSeconds/{past_seconds}/page/{page}/"
        )
        try:
            async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=10) as client:
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()
                if data == [None] or data is None:
                    break
                page_kills = [k for k in data if isinstance(k, dict)]
                all_kills.extend(page_kills)
                if len(page_kills) < 200:
                    break   # last page — stop paging
                await _asyncio.sleep(0.5)  # polite rate-limiting between pages
        except Exception as exc:
            logger.debug("zKB system %d page %d fetch failed: %s", system_id, page, exc)
            break

    return all_kills


def _build_entry(system_name: str, kills: list[dict], days: int) -> HeatmapEntry:
    """Convert a raw kill list into a HeatmapEntry."""
    histogram = [0] * 24
    now = time.time()
    cutoff_24h = now - 86400
    kills_24h = 0

    for kill in kills:
        kill_time_str = kill.get("killmail_time", "")
        if not kill_time_str:
            continue
        try:
            # EVE timestamps: "2024-05-01T15:30:22Z"
            dt = datetime.fromisoformat(kill_time_str.replace("Z", "+00:00"))
            epoch = dt.timestamp()
            hour = dt.hour
            histogram[hour] += 1
            if epoch >= cutoff_24h:
                kills_24h += 1
        except Exception:
            continue

    peak_hour = histogram.index(max(histogram)) if any(histogram) else 0
    return HeatmapEntry(
        system=system_name,
        kills_24h=kills_24h,
        kills_7d=len(kills),
        peak_hour_utc=peak_hour,
        kill_histogram=histogram,
    )


async def get_constellation_heatmap(
    system_name: str,
    days: int = 7,
) -> dict[str, HeatmapEntry]:
    """Return kill-activity heatmap for the constellation containing *system_name*.

    Results are cached for 1 hour (configurable via _CACHE_TTL).
    Returns a dict keyed by system name; empty dict on API failure.
    """
    cache_key = (system_name.strip().upper(), days)
    now = time.time()
    if cache_key in _CACHE:
        ts, cached = _CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            return cached

    try:
        from evealert.tools.universe import get_universe_cache  # noqa: PLC0415

        cache = get_universe_cache()

        # 1. Resolve system name → system ID
        system_id = await cache.get_system_id(system_name)
        if not system_id:
            system_id = await _esi_resolve_name(system_name)
        if not system_id:
            logger.warning("Heatmap: could not resolve system %r", system_name)
            return {}

        # 2. Get constellation ID from system
        sys_data = await _esi_get_system(system_id)
        constellation_id = sys_data.get("constellation_id")
        if not constellation_id:
            return {}

        # 3. Get all system IDs in the constellation
        const_data = await _esi_get_constellation(constellation_id)
        system_ids: list[int] = const_data.get("systems", [])
        if not system_ids:
            return {}

        # 4. Resolve system IDs → names via ESI /universe/names/
        import httpx  # noqa: PLC0415
        from evealert.tools.http_common import DEFAULT_HEADERS  # noqa: PLC0415

        name_url = "https://esi.evetech.net/latest/universe/names/"
        id_to_name: dict[int, str] = {}
        # ESI names endpoint accepts up to 1000 IDs per request
        for batch_start in range(0, len(system_ids), 1000):
            batch = system_ids[batch_start : batch_start + 1000]
            async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=10) as client:
                r = await client.post(name_url, json=batch)
                r.raise_for_status()
                for item in r.json():
                    if item.get("category") == "solar_system":
                        id_to_name[item["id"]] = item["name"]

        # 5. Fetch kills per system (sequential — polite to the API)
        import asyncio  # noqa: PLC0415

        heatmap: dict[str, HeatmapEntry] = {}
        for sys_id in system_ids:
            name = id_to_name.get(sys_id, str(sys_id))
            kills = await _zkb_kills_for_system(sys_id, days)
            entry = _build_entry(name, kills, days)
            heatmap[name] = entry
            await asyncio.sleep(0.25)  # polite rate-limiting

        _CACHE[cache_key] = (now, heatmap)
        return heatmap

    except Exception as exc:
        logger.warning("Constellation heatmap failed for %r: %s", system_name, exc)
        return {}
