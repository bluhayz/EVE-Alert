"""Gate-camp detection from R2Z2 live-kill clustering (#170, v7.1).

Fed by R2Z2Consumer's already-filtered rolling kill buffer (see
evealert/tools/r2z2.py, #169) rather than a separate feed -- gate camps
are just a clustering pattern within the same kill stream the live-kill
alarm already consumes.

Heuristic (tunable constants below): kills at the same (system, gate/
station/structure) location within a 30-minute window, with the same
attacker characters appearing across multiple kills (a camping fleet
doesn't turn over every kill, unlike unrelated separate gank squads):

  - >=3 kills, >=2 distinct victim corporations, >=60% attacker overlap
    -> "camp" (high confidence)
  - 2 kills with attacker overlap -> "possible_camp" (early warning)

Kills with no location_id (deep-space kills, not at a gate/station/
structure) are excluded -- there's no "location" to camp.
"""

import logging
import time
from dataclasses import dataclass

from evealert.tools.http_common import DEFAULT_HEADERS

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

logger = logging.getLogger("alert.gatecamp")

_ESI_BASE = "https://esi.evetech.net/latest"
_HTTP_TIMEOUT = 8.0

_CAMP_WINDOW_SECONDS = 1800  # 30 min
_CAMP_MIN_KILLS = 3
_POSSIBLE_CAMP_MIN_KILLS = 2
_MIN_DISTINCT_VICTIM_CORPS = 2
_MIN_ATTACKER_OVERLAP_RATIO = 0.6


@dataclass
class CampInfo:
    system_id: int
    location_id: int
    kill_count: int
    last_kill_age_seconds: float
    confidence: str  # "camp" | "possible_camp"
    system_name: str | None = None
    gate_name: str | None = None


def _attacker_overlap_ratio(kills: list) -> float:
    """Fraction of kills in the cluster that share at least one attacker
    character with another kill in the same cluster -- a proxy for "the
    same characters keep appearing" without assuming a fixed roster size
    (#170: distinguishes a standing camp from a string of unrelated
    one-off ganks by different gangs at the same popular gate)."""
    if len(kills) < 2:
        return 0.0
    char_counts: dict[int, int] = {}
    for km in kills:
        for cid in km.attacker_character_ids:
            char_counts[cid] = char_counts.get(cid, 0) + 1
    repeating = {cid for cid, count in char_counts.items() if count > 1}
    if not repeating:
        return 0.0
    overlapping_kills = sum(1 for km in kills if km.attacker_character_ids & repeating)
    return overlapping_kills / len(kills)


def detect_camps(kills_with_times: list, *, now: float | None = None) -> list[CampInfo]:
    """Cluster (timestamp, LiveKillmail) pairs by (system_id, location_id)
    and classify each cluster as a camp, possible camp, or neither.

    Pure function -- no I/O, no ESI resolution (see resolve_camp_names()
    for that) -- so camp/not-camp/decayed-camp scenarios are directly
    unit-testable with synthetic kill sequences.
    """
    now = now if now is not None else time.time()
    clusters: dict[tuple[int, int], list[tuple[float, object]]] = {}
    for t, km in kills_with_times:
        if km.location_id is None:
            continue
        clusters.setdefault((km.solar_system_id, km.location_id), []).append((t, km))

    camps: list[CampInfo] = []
    for (system_id, location_id), entries in clusters.items():
        kills = [km for _, km in entries]
        times = [t for t, _ in entries]
        kill_count = len(kills)
        if kill_count < _POSSIBLE_CAMP_MIN_KILLS:
            continue

        overlap_ratio = _attacker_overlap_ratio(kills)
        has_overlap = overlap_ratio >= _MIN_ATTACKER_OVERLAP_RATIO
        if not has_overlap:
            continue

        distinct_victim_corps = {
            km.victim_corporation_id for km in kills if km.victim_corporation_id
        }
        if kill_count >= _CAMP_MIN_KILLS and len(distinct_victim_corps) >= _MIN_DISTINCT_VICTIM_CORPS:
            confidence = "camp"
        else:
            confidence = "possible_camp"

        camps.append(CampInfo(
            system_id=system_id,
            location_id=location_id,
            kill_count=kill_count,
            last_kill_age_seconds=max(0.0, now - max(times)),
            confidence=confidence,
        ))
    return camps


def get_active_camps(consumer, *, now: float | None = None) -> list[CampInfo]:
    """Convenience wrapper: pull the live consumer's recent-kill buffer
    and run detect_camps() over it. Returns [] when there's no consumer
    (R2Z2 not enabled/started) rather than raising."""
    if consumer is None:
        return []
    kills_with_times = consumer.get_recent_kills_with_times(_CAMP_WINDOW_SECONDS)
    return detect_camps(kills_with_times, now=now)


async def _fetch_stargate_name(location_id: int) -> str | None:
    """Resolve a stargate's name via public ESI. Citadels/structures at
    the same locationID range require an authenticated call this module
    doesn't have -- those simply fall back to no gate_name (system_name
    alone is still shown)."""
    if not _HTTPX_AVAILABLE:
        return None
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=DEFAULT_HEADERS) as client:
            resp = await client.get(f"{_ESI_BASE}/universe/stargates/{location_id}/")
            resp.raise_for_status()
            return resp.json().get("name")
    except Exception as exc:
        logger.debug("Gate-camp: stargate name resolution failed for %d: %s", location_id, exc)
        return None


async def resolve_camp_names(camps: list[CampInfo]) -> list[CampInfo]:
    """Fill in system_name/gate_name on each CampInfo via ESI. Separated
    from detect_camps() so the detection heuristic stays a pure,
    network-free function -- only the display layer needs names."""
    from evealert.tools.universe import get_universe_cache  # noqa: PLC0415

    cache = get_universe_cache()
    for camp in camps:
        try:
            camp.system_name = await cache.get_system_name(camp.system_id)
        except Exception as exc:
            logger.debug("Gate-camp: system name resolution failed for %d: %s", camp.system_id, exc)
        camp.gate_name = await _fetch_stargate_name(camp.location_id)
    return camps
