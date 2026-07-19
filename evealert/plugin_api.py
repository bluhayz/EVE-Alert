"""Public Plugin API v2 for EVE Alert (#181, v8.0).

A plugin is a single ``.py`` file in the user plugins directory (see
``evealert.settings.helper.get_user_plugins_path()``) defining any of
the module-level hook functions below.

v2 hook signatures::

    def on_start(ctx: PluginContext) -> None: ...
    def on_stop(ctx: PluginContext) -> None: ...
    def on_enemy(ctx: PluginContext, event: AlarmEvent) -> None: ...
    def on_faction(ctx: PluginContext, event: AlarmEvent) -> None: ...
    def on_intel(ctx: PluginContext, report: IntelReport) -> None: ...
    def on_killmail(ctx: PluginContext, km: KillmailEvent) -> None: ...
    def on_threat_score(ctx: PluginContext, assessment: ThreatScoreEvent) -> None: ...

v1 plugins (the pre-#181 kwargs-based signatures: ``on_start()``,
``on_stop()``, ``on_enemy(system, timestamp)``,
``on_faction(system, timestamp)``, ``on_intel(line)``) keep working
unchanged -- ``evealert.tools.plugin_loader`` tells the two apart by
inspecting each hook's declared parameter names, not just their count
(``on_enemy``'s v1 and v2 forms both take exactly two parameters).

A module-level ``__version__`` string (your plugin's own version, any
format) is shown in Settings > Plugins when present.

This module is semver-documented: within API_VERSION's major version,
existing dataclass fields and PluginContext methods will not be removed
or change meaning. New fields/methods may be added.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger("alert.plugins")

API_VERSION = "2.0"


@dataclass(frozen=True)
class AlarmEvent:
    """Passed to on_enemy / on_faction."""

    alarm_type: str  # "Enemy" | "Faction"
    system: str
    timestamp: str
    client_name: str | None = None  # set for extra multi-client (#174) alarms


@dataclass(frozen=True)
class IntelReport:
    """Passed to on_intel -- one parsed intel-channel line (#171)."""

    line: str
    system: str | None = None
    pilot: str | None = None


@dataclass(frozen=True)
class KillmailEvent:
    """Passed to on_killmail -- one R2Z2-matched live killmail (#169)."""

    killmail_id: int
    system_id: int | None
    system_name: str | None
    victim_ship_type_id: int | None
    attacker_character_ids: tuple[int, ...] = field(default_factory=tuple)
    jump_distance: int | None = None  # jumps from the user's current system


@dataclass(frozen=True)
class ThreatScoreEvent:
    """Passed to on_threat_score -- the composite threat score (#141)
    computed for the current Enemy alarm's local population."""

    score: int
    label: str  # "CAUTION" | "HIGH" | "CRITICAL"
    reasons: tuple[str, ...] = field(default_factory=tuple)
    behavioral_label: str | None = None


class PluginContext:
    """Passed as the first argument to every v2 hook.

    version:  this API's version string (see API_VERSION) -- check it
        if your plugin depends on a dataclass field added in a later
        minor release.
    settings: a read-only snapshot (plain dict) of settings.json at the
        moment the hook was called. Mutating it has no effect on the
        app -- it's your copy.
    """

    version = API_VERSION

    def __init__(self, *, settings: dict, log_fn: Callable[[str], None]) -> None:
        self.settings = settings
        self._log_fn = log_fn

    def log(self, text: str) -> None:
        """Write *text* to the EVE Alert log pane (cyan)."""
        self._log_fn(text)

    def speak(self, text: str) -> None:
        """Speak *text* via the app's configured TTS engine/rate
        (Settings > Notifications). No-op if TTS is disabled or the
        optional TTS dependency isn't installed -- never raises."""
        try:
            notif = self.settings.get("notifications", {}) or {}
            if not notif.get("tts_enabled", False):
                return
            from evealert.tools.tts import speak as _speak  # noqa: PLC0415

            _speak(text, int(notif.get("tts_rate", 175)))
        except Exception as exc:
            logger.debug("Plugin speak() failed: %s", exc)

    def fire_webhook(self, url: str, payload: dict) -> None:
        """POST *payload* as JSON to *url*. Best-effort, fire-and-forget
        -- a plugin picks and owns its own webhook URL (e.g. a Discord
        channel it wants alerts mirrored to); this is a convenience so
        the plugin doesn't need its own HTTP dependency. Swallows any
        error (bad URL, network failure, non-2xx) so a broken webhook
        can never crash a hook."""
        try:
            import httpx  # noqa: PLC0415

            with httpx.Client(timeout=5.0) as client:
                client.post(url, json=payload)
        except Exception as exc:
            logger.debug("Plugin webhook POST failed: %s", exc)
