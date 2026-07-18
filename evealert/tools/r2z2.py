"""Live killmail stream via zKillboard's R2Z2 service (#169, v7.1).

**Naming note**: #169 was scoped against zKillboard's RedisQ long-poll
service. Verified at implementation time (per the issue's own
instruction) against https://github.com/zKillboard/RedisQ: RedisQ was
sunset 2026-05-31, before this issue was implemented. Its documented
replacement, R2Z2 (https://github.com/zKillboard/zKillboard/wiki/API-(R2Z2)),
is what this module actually talks to. The module/settings are named
`r2z2` rather than `redisq` so the code doesn't reference a dead service.

R2Z2 protocol (sequence-based HTTP polling, NOT RedisQ's queueID/ttw
long-poll queue):
  1. GET /ephemeral/sequence.json once for a starting sequence number.
  2. GET /ephemeral/{sequence}.json repeatedly:
     - 200 -> a killmail; process it, increment sequence, poll again
       almost immediately (rate limit: 15 req/s per IP -- this consumer
       polls at ~10 req/s on success to stay well under it).
     - 404 -> nothing at that sequence yet; wait _ERROR_BACKOFF_SECONDS
       (R2Z2's own documented guidance) and retry the SAME sequence --
       never skip ahead on a 404, the next killmail will still land there.
     - other errors -> exponential backoff, capped.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from evealert.tools.http_common import DEFAULT_HEADERS

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

logger = logging.getLogger("alert.r2z2")

_BASE_URL = "https://r2z2.zkillboard.com/ephemeral"
_ESI_BASE = "https://esi.evetech.net/latest"
_HTTP_TIMEOUT = 10.0

# R2Z2's own documented guidance for a 404 (nothing new yet).
_ERROR_BACKOFF_SECONDS = 6.0
_MAX_BACKOFF_SECONDS = 60.0
# ~10 req/s on success, comfortably under R2Z2's 15 req/s/IP limit.
_SUCCESS_POLL_DELAY = 0.1
# EVE Online's galaxy-wide kill rate means a live tail position 404s for
# at most a few seconds/minutes between kills, never longer. A resumed
# sequence (from a persisted last_sequence after being offline) that's
# more than 24h old is past R2Z2's own file-retention window and will
# 404 FOREVER (expired, not "not yet happened"). If the same sequence
# number keeps 404ing past this threshold, assume it's expired rather
# than quiet, and resync to the live tail instead of waiting forever.
_STALE_SEQUENCE_THRESHOLD_SECONDS = 300

# How long a processed kill stays in the rolling buffer -- generous
# enough to cover both the alarm-jumps live-kill use case and #170's
# 30-minute gate-camp clustering window.
_KILL_BUFFER_WINDOW_SECONDS = 1800
_KILL_BUFFER_MAX_ENTRIES = 2000


@dataclass
class LiveKillmail:
    killmail_id: int
    solar_system_id: int
    victim_ship_type_id: int | None
    attacker_count: int
    location_id: int | None  # gate/station/structure ID (zkb field), for #170
    # Victim's corp alone (distinct from corporation_ids below, which mixes
    # victim + attacker corps) -- #170's camp heuristic needs to count
    # distinct VICTIM corporations specifically.
    victim_corporation_id: int | None = None
    alliance_ids: set = field(default_factory=set)  # victim + attacker alliance IDs
    corporation_ids: set = field(default_factory=set)
    attacker_character_ids: set = field(default_factory=set)


def _parse_package(package: dict) -> LiveKillmail | None:
    """Parse one R2Z2 killmail package into a LiveKillmail, or None when
    the package is missing required fields (malformed/unexpected shape --
    never raises, per the "malformed entries" acceptance criterion)."""
    try:
        killmail = package["killmail"]
        killmail_id = package["killmail_id"]
        solar_system_id = killmail["solar_system_id"]
    except (KeyError, TypeError):
        return None

    victim = killmail.get("victim") or {}
    attackers = killmail.get("attackers") or []
    zkb = package.get("zkb") or {}

    alliance_ids = set()
    corporation_ids = set()
    attacker_character_ids = set()
    if victim.get("alliance_id"):
        alliance_ids.add(victim["alliance_id"])
    if victim.get("corporation_id"):
        corporation_ids.add(victim["corporation_id"])
    for a in attackers:
        if not isinstance(a, dict):
            continue
        if a.get("alliance_id"):
            alliance_ids.add(a["alliance_id"])
        if a.get("corporation_id"):
            corporation_ids.add(a["corporation_id"])
        if a.get("character_id"):
            attacker_character_ids.add(a["character_id"])

    return LiveKillmail(
        killmail_id=killmail_id,
        solar_system_id=solar_system_id,
        victim_ship_type_id=victim.get("ship_type_id"),
        attacker_count=len(attackers),
        location_id=zkb.get("locationID"),
        victim_corporation_id=victim.get("corporation_id"),
        alliance_ids=alliance_ids,
        corporation_ids=corporation_ids,
        attacker_character_ids=attacker_character_ids,
    )


_ship_name_cache: dict[int, str] = {}


async def resolve_ship_name(client, type_id: int | None) -> str | None:
    """Resolve a ship type_id to its name, cached across calls -- kills
    in a live stream repeat common ship types constantly."""
    if not type_id:
        return None
    if type_id in _ship_name_cache:
        return _ship_name_cache[type_id]
    try:
        resp = await client.get(f"{_ESI_BASE}/universe/types/{type_id}/")
        resp.raise_for_status()
        name = resp.json().get("name")
        if name:
            _ship_name_cache[type_id] = name
        return name
    except Exception as exc:
        logger.debug("R2Z2: ship name resolution failed for %d: %s", type_id, exc)
        return None


class R2Z2Consumer:
    """Long-running sequence-polling consumer for R2Z2 live killmails.

    Filtering (jump-radius + alliance watchlist) happens inside run(),
    not in the on_kill callback, so on_kill only ever fires for kills
    the caller actually cares about -- and so the internal rolling
    buffer (get_recent_kills(), for #170's gate-camp detector) never
    grows with every killmail in New Eden, only the relevant ones.

    Usage::

        consumer = R2Z2Consumer(on_kill=handle_kill)
        asyncio.ensure_future(consumer.run())
        ...
        consumer.stop()
    """

    def __init__(
        self,
        origin_system_id: int | None = None,
        watch_jumps: int = 5,
        alliance_watchlist: set | None = None,
        on_kill: Callable[["LiveKillmail", int | None], None] | None = None,
        sequence: int | None = None,
    ) -> None:
        self._origin_system_id = origin_system_id
        self._watch_jumps = max(0, watch_jumps)
        self._watchlist = alliance_watchlist or set()
        self.on_kill = on_kill
        self._sequence = sequence
        self._running = False
        self._backoff = _ERROR_BACKOFF_SECONDS
        self._nearby_systems: dict[int, int] = {}  # system_id -> jump_distance
        # (received_at, killmail) -- bounded both by time and count so a
        # busy stream can't grow this without limit (soak-tested concern).
        self._recent_kills: deque = deque(maxlen=_KILL_BUFFER_MAX_ENTRIES)

    def stop(self) -> None:
        self._running = False

    @property
    def last_sequence(self) -> int | None:
        """The most recently processed (or about-to-be-processed)
        sequence number, for persisting across restarts."""
        return self._sequence

    def get_recent_kills(self, within_seconds: float = _KILL_BUFFER_WINDOW_SECONDS) -> list:
        """Return matched (already-filtered) kills seen in the last
        *within_seconds*, newest last. Used for #170's gate-camp
        clustering and the threat-score adjacent_kills signal."""
        cutoff = time.time() - within_seconds
        return [km for t, km in self._recent_kills if t >= cutoff]

    def get_recent_kills_with_times(
        self, within_seconds: float = _KILL_BUFFER_WINDOW_SECONDS
    ) -> list[tuple[float, "LiveKillmail"]]:
        """Same as get_recent_kills() but keeps each kill's received-at
        timestamp -- #170's gate-camp clustering needs per-kill age to
        compute last_kill_age_seconds and to age decayed clusters out."""
        cutoff = time.time() - within_seconds
        return [(t, km) for t, km in self._recent_kills if t >= cutoff]

    def kill_count_since(self, within_seconds: float) -> int:
        return len(self.get_recent_kills(within_seconds))

    async def run(self) -> None:
        self._running = True
        if not _HTTPX_AVAILABLE:
            logger.warning("R2Z2 consumer disabled: httpx not available")
            return

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=DEFAULT_HEADERS) as client:
            if self._origin_system_id is not None and self._watch_jumps > 0:
                await self._refresh_nearby_systems()

            if self._sequence is None:
                self._sequence = await self._fetch_starting_sequence(client)
                if self._sequence is None:
                    logger.warning(
                        "R2Z2: could not determine a starting sequence -- consumer not started"
                    )
                    return

            logger.info("R2Z2 consumer started at sequence %d", self._sequence)
            stuck_since: float | None = None

            while self._running:
                try:
                    resp = await client.get(f"{_BASE_URL}/{self._sequence}.json")
                except Exception as exc:
                    logger.debug("R2Z2 request failed: %s", exc)
                    await self._sleep_backoff()
                    continue

                if resp.status_code == 200:
                    self._backoff = _ERROR_BACKOFF_SECONDS  # reset after any success
                    stuck_since = None
                    try:
                        package = resp.json()
                    except ValueError:
                        package = None
                    if package:
                        self._handle_package(package)
                    self._sequence += 1
                    await asyncio.sleep(_SUCCESS_POLL_DELAY)
                elif resp.status_code == 404:
                    # Nothing new yet -- retry the SAME sequence, never skip
                    # ahead, UNLESS we've been stuck here long enough that
                    # it's more likely an expired (resumed-from-stale)
                    # sequence than genuine quiet -- then resync to live.
                    if stuck_since is None:
                        stuck_since = time.time()
                    elif time.time() - stuck_since > _STALE_SEQUENCE_THRESHOLD_SECONDS:
                        logger.info(
                            "R2Z2: sequence %d stale for >%ds, resyncing to live tail",
                            self._sequence, _STALE_SEQUENCE_THRESHOLD_SECONDS,
                        )
                        fresh = await self._fetch_starting_sequence(client)
                        if fresh is not None:
                            self._sequence = fresh
                        stuck_since = None
                    await asyncio.sleep(_ERROR_BACKOFF_SECONDS)
                else:
                    await self._sleep_backoff()

    def _handle_package(self, package: dict) -> None:
        killmail = _parse_package(package)
        if killmail is None:
            return

        jump_dist = self._nearby_systems.get(killmail.solar_system_id)
        watchlist_hit = bool(self._watchlist & killmail.alliance_ids)
        if jump_dist is None and not watchlist_hit:
            return  # not relevant to this install -- discard, don't buffer

        self._recent_kills.append((time.time(), killmail))
        if self.on_kill is not None:
            try:
                self.on_kill(killmail, jump_dist)
            except Exception as exc:
                logger.debug("R2Z2 on_kill callback failed: %s", exc)

    async def _refresh_nearby_systems(self) -> None:
        try:
            from evealert.tools.universe import get_universe_cache  # noqa: PLC0415

            cache = get_universe_cache()
            self._nearby_systems = await cache.get_systems_within_jumps(
                self._origin_system_id, self._watch_jumps
            )
        except Exception as exc:
            logger.debug("R2Z2: nearby-systems lookup failed: %s", exc)
            self._nearby_systems = {}

    async def _fetch_starting_sequence(self, client) -> int | None:
        try:
            resp = await client.get(f"{_BASE_URL}/sequence.json")
            resp.raise_for_status()
            return int(resp.json()["sequence"])
        except Exception as exc:
            logger.debug("R2Z2: failed to fetch starting sequence: %s", exc)
            return None

    async def _sleep_backoff(self) -> None:
        await asyncio.sleep(self._backoff)
        self._backoff = min(self._backoff * 2, _MAX_BACKOFF_SECONDS)
