"""Fleet and combat context analysis for EVE Alert.

v3.7 #91: Hostile fleet composition — aggregate ship types when 3+ hostiles appear
v3.7 #92: Timezone activity profiling — kill histogram for nearby systems
v3.7 #93: Killmail notification — post tracked character kills/losses
"""

import asyncio
import collections
import logging
import time
from typing import Callable, NamedTuple

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

logger = logging.getLogger("alert.fleet")

_HTTP_TIMEOUT = 8.0
_ZKB_BASE = "https://zkillboard.com/api"


# ------------------------------------------------------------------
# #91: Fleet composition analysis
# ------------------------------------------------------------------


class FleetComposition(NamedTuple):
    pilot_count: int
    ship_types: dict  # {ship_name: count}
    top_category: str  # e.g. "Interceptors", "Bombers", "Capitals"
    threat_summary: str


async def analyze_fleet_composition(
    character_ids: list[int],
) -> FleetComposition | None:
    """Look up recent ships for each character and summarise the fleet."""
    if not _HTTPX_AVAILABLE or not character_ids:
        return None

    # Concurrently fetch last 5 kills for each pilot
    tasks = [_recent_ships(cid) for cid in character_ids[:10]]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    ship_counter: collections.Counter = collections.Counter()
    for ships in results:
        if isinstance(ships, list):
            ship_counter.update(ships)

    if not ship_counter:
        return FleetComposition(
            pilot_count=len(character_ids),
            ship_types={},
            top_category="Unknown",
            threat_summary=f"{len(character_ids)} pilot(s) — ship types unknown",
        )

    # Classify top ships
    top_category = _classify_fleet(ship_counter)
    top_ships = dict(ship_counter.most_common(5))
    ship_str = ", ".join(f"{c}× {s}" for s, c in ship_counter.most_common(3))
    summary = f"{len(character_ids)} pilot(s) — {top_category}: {ship_str}"

    return FleetComposition(
        pilot_count=len(character_ids),
        ship_types=top_ships,
        top_category=top_category,
        threat_summary=summary,
    )


def _classify_fleet(counter: collections.Counter) -> str:
    """Classify fleet archetype from ship types."""
    names = " ".join(counter.keys()).lower()
    if any(w in names for w in ["titan", "supercarrier", "dreadnought", "carrier"]):
        return "Capital fleet"
    if any(w in names for w in ["stealth bomber", "bomber"]):
        return "Bomber fleet"
    if any(w in names for w in ["interceptor", "sabre", "flycatcher"]):
        return "Interceptor gang"
    if any(w in names for w in ["battleship", "battlecruiser"]):
        return "Battleship fleet"
    if any(w in names for w in ["cruiser", "assault frigate"]):
        return "Cruiser gang"
    return "Mixed composition"


async def _recent_ships(character_id: int) -> list[str]:
    """Fetch up to 5 recent kill ship types for *character_id*."""
    url = f"{_ZKB_BASE}/kills/characterID/{character_id}/limit/5/"
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT, headers={"User-Agent": "EVEAlert/3.7"}
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.debug("ZKB recent ships failed for %d: %s", character_id, exc)
        return []

    ship_ids = []
    for entry in data if isinstance(data, list) else []:
        zkb = entry.get("zkb", {})
        # Attacker ship: walk attackers to find this character
        for att in entry.get("attackers", []):
            if att.get("character_id") == character_id:
                ship_ids.append(att.get("ship_type_id", 0))
                break

    # Resolve ship names concurrently
    name_tasks = [_resolve_type_name(sid) for sid in ship_ids if sid]
    names = await asyncio.gather(*name_tasks, return_exceptions=True)
    return [n for n in names if isinstance(n, str)]


async def _resolve_type_name(type_id: int) -> str | None:
    url = f"https://esi.evetech.net/v4/universe/types/{type_id}/"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json().get("name")
    except Exception:
        return None


# ------------------------------------------------------------------
# #92: Timezone activity profiling
# ------------------------------------------------------------------


class ActivityProfile:
    """Records kill timestamps per system to build a 24-hour histogram."""

    def __init__(self) -> None:
        # {system_id: [hour_0_count, ..., hour_23_count]}
        self._hourly: dict[int, list[int]] = {}
        self._total: dict[int, int] = {}

    def record_kills(self, system_id: int, kills: list[dict]) -> None:
        if system_id not in self._hourly:
            self._hourly[system_id] = [0] * 24
            self._total[system_id] = 0

        for kill in kills:
            kill_time = kill.get("killmail_time", "")
            if kill_time and len(kill_time) >= 13:
                try:
                    hour = int(kill_time[11:13])
                    self._hourly[system_id][hour] += 1
                    self._total[system_id] += 1
                except ValueError:
                    pass

    def peak_hours(self, system_id: int) -> list[tuple[int, int]]:
        """Return top 3 (hour, count) pairs for *system_id*."""
        hourly = self._hourly.get(system_id, [0] * 24)
        ranked = sorted(enumerate(hourly), key=lambda x: x[1], reverse=True)
        return [(h, c) for h, c in ranked[:3] if c > 0]

    def summary(self, system_id: int) -> str:
        peaks = self.peak_hours(system_id)
        if not peaks:
            return "No activity data yet."
        parts = [f"{h:02d}:00 ({c} kills)" for h, c in peaks]
        total = self._total.get(system_id, 0)
        return f"Peak hours: {', '.join(parts)} | Total recorded: {total}"


# ------------------------------------------------------------------
# #93: Killmail notification
# ------------------------------------------------------------------


class KillmailMonitor:
    """Poll Zkillboard for kills/losses of tracked characters."""

    def __init__(
        self,
        character_ids: list[int],
        callback: Callable[[str], None],
        poll_interval: int = 60,
    ) -> None:
        self._character_ids = character_ids
        self._callback = callback
        self._poll_interval = max(30, poll_interval)
        self._running = False
        self._seen_ids: set[int] = set()

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            for char_id in self._character_ids:
                try:
                    await self._check_character(char_id)
                except Exception as exc:
                    logger.debug("Killmail check failed for %d: %s", char_id, exc)
            await asyncio.sleep(self._poll_interval)

    async def _check_character(self, character_id: int) -> None:
        url = f"{_ZKB_BASE}/kills/characterID/{character_id}/pastSeconds/120/"
        try:
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT, headers={"User-Agent": "EVEAlert/3.7"}
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                entries = resp.json()
        except Exception:
            return

        for entry in entries if isinstance(entries, list) else []:
            km_id = entry.get("killmail_id", 0)
            if not km_id or km_id in self._seen_ids:
                continue
            self._seen_ids.add(km_id)

            # Determine if kill or loss
            victim_id = entry.get("victim", {}).get("character_id")
            is_loss = victim_id == character_id
            ship = entry.get("victim", {}).get("ship_type_id", 0)
            system_id = entry.get("solar_system_id", 0)
            zkb = entry.get("zkb", {})
            isk_m = (zkb.get("totalValue", 0) or 0) / 1_000_000

            label = "LOSS" if is_loss else "KILL"
            self._callback(
                f"Killmail {label}: ship#{ship} in system#{system_id} — {isk_m:.0f}M ISK"
            )
