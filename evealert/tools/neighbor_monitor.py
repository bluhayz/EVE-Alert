"""Neighboring system kill activity monitor for EVE Alert.

Polls Zkillboard every N seconds for kill activity in systems within
a configurable jump radius of the player's configured system.

When activity is detected, posts a warning to the GUI log pane:
  "Activity: 3 kill(s) in Uedama (2 jumps)"

Architecture mirrors IntelWatcher: an async run() loop started as an
asyncio task by AlertAgent, stopped via stop().
"""

import asyncio
import logging
import time
from typing import Callable

from evealert.tools.http_common import DEFAULT_HEADERS
from evealert.tools.zkillboard import clean_zkb_entries

logger = logging.getLogger("alert.neighbors")

# Default poll interval — 120 s keeps well within Zkillboard rate limits
_DEFAULT_POLL_INTERVAL = 120
# Minimum time between alerts for the same system (avoid spam)
_SYSTEM_COOLDOWN = 600  # 10 min


class NeighborMonitor:
    """Async task that polls adjacent systems for kill activity.

    Parameters
    ----------
    system_name:
        Name of the player's current system (used to resolve the origin ID).
    max_jumps:
        How many jumps out to monitor (1–5, default 3).
    min_kills:
        Minimum kills in the last 15 min to trigger an alert (default 1).
    poll_interval:
        Seconds between poll cycles (default 120).
    callback:
        Called with (message: str) for each activity warning; runs on
        the caller's thread — use self.main.after(0, ...) when bridging
        to Tkinter.
    """

    def __init__(
        self,
        system_name: str,
        max_jumps: int = 3,
        min_kills: int = 1,
        poll_interval: int = _DEFAULT_POLL_INTERVAL,
        callback: Callable[[str], None] = lambda msg: None,
    ) -> None:
        self._system_name = system_name
        self._max_jumps = max(1, min(max_jumps, 5))
        self._min_kills = max(1, min_kills)
        self._poll_interval = max(60, poll_interval)
        self._callback = callback
        self._running = False
        # {system_id: last_alert_timestamp} — prevents repeat alerts
        self._alerted: dict[int, float] = {}

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """Main poll loop — runs until stop() is called."""
        self._running = True
        logger.info(
            "NeighborMonitor started (system=%r, max_jumps=%d, interval=%ds)",
            self._system_name,
            self._max_jumps,
            self._poll_interval,
        )

        # Import inside run() so the module is available at test time without ESI
        from evealert.tools.universe import (  # pylint: disable=import-outside-toplevel
            get_universe_cache,
        )

        cache = get_universe_cache()

        # Resolve origin system ID once at start
        origin_id = await cache.get_system_id(self._system_name)
        if origin_id is None:
            logger.warning(
                "NeighborMonitor: could not resolve system %r — monitor inactive.",
                self._system_name,
            )
            self._callback(
                f"Adjacent monitor: could not resolve system '{self._system_name}'"
            )
            return

        self._callback(
            f"Adjacent monitor: watching {self._max_jumps} jump(s) from {self._system_name}"
        )

        while self._running:
            try:
                await self._poll_once(cache, origin_id)
            except Exception as exc:
                logger.debug("NeighborMonitor poll error: %s", exc)
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self, cache, origin_id: int) -> None:
        """One poll cycle: check all systems within max_jumps for kills."""
        nearby = await cache.get_systems_within_jumps(origin_id, self._max_jumps)
        now = time.time()

        # Concurrently fetch 15-minute kill counts for all nearby systems
        tasks = {
            sys_id: asyncio.create_task(self._kills_15min(sys_id)) for sys_id in nearby
        }
        for sys_id, task in tasks.items():
            try:
                kills = await task
            except Exception:
                continue

            if kills < self._min_kills:
                continue

            # Per-system cooldown — don't alert again within 10 minutes
            last_alert = self._alerted.get(sys_id, 0)
            if now - last_alert < _SYSTEM_COOLDOWN:
                continue

            self._alerted[sys_id] = now
            jump_dist = nearby[sys_id]
            system_name = await cache.get_system_name(sys_id) or str(sys_id)
            jump_word = "jump" if jump_dist == 1 else "jumps"
            self._callback(
                f"Adjacent: {kills} kill(s) in {system_name} ({jump_dist} {jump_word} away)"
            )

    @staticmethod
    async def _kills_15min(system_id: int) -> int:
        """Return kill count in *system_id* over the last 15 minutes."""
        try:
            import httpx  # pylint: disable=import-outside-toplevel

            url = f"https://zkillboard.com/api/kills/solarSystemID/{system_id}/pastSeconds/900/"
            async with httpx.AsyncClient(
                timeout=8.0, headers=DEFAULT_HEADERS
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                return len(clean_zkb_entries(data))
        except Exception as exc:
            logger.debug("ZKB 15min kills failed for %d: %s", system_id, exc)
            return 0
