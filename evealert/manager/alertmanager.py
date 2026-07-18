import asyncio
import logging
import os
import random
import time
from collections import deque
from typing import Callable, NamedTuple

import numpy as np
import soundfile as sf

try:
    import sounddevice as sd

    _SOUNDDEVICE_AVAILABLE = True
except OSError:
    # PortAudio library not found (common on macOS without `brew install portaudio`)
    sd = None  # type: ignore[assignment]
    _SOUNDDEVICE_AVAILABLE = False

from evealert.constants import (
    ALARM_SOUND_FILE,
    ALERT_IMAGE_PREFIX,
    AUDIO_CHANNELS,
    DEFAULT_COOLDOWN_TIMER,
    FACTION_IMAGE_PREFIX,
    FACTION_SOUND_FILE,
    IMG_FOLDER,
    MAIN_CHECK_SLEEP_MAX,
    MAIN_CHECK_SLEEP_MIN,
    MAX_SOUND_TRIGGERS,
    SOUND_FOLDER,
    VISION_SLEEP_INTERVAL,
    WEBHOOK_COOLDOWN,
)
from evealert.settings.helper import get_resource_path, get_user_img_path
from evealert.settings.stats_store import (
    load_lifetime_stats,
    save_lifetime_stats,
    save_session_report,
)
from evealert.bridge import UIBridge  # noqa: F401
from evealert.settings.store import get_settings_store
from evealert.settings.validator import ConfigValidator
from evealert.statistics import AlarmStatistics
from evealert.tools.link_markers import make_link
from evealert.tools.vision import Vision
from evealert.tools.windowscapture import WindowCapture


class _EnemySighting(NamedTuple):
    """Per-enemy dedup / re-arm record (#100, #144)."""
    first_seen: float   # epoch when first alarmed
    last_alarm: float   # epoch of most-recent alarm trigger
    rearm_at: float     # epoch when re-alert is due (0 = never)


class _ExtraClient:
    """Runtime state for one ADDITIONAL EVE client beyond the primary
    (#174 multi-client support, MVP scope).

    The primary/first client continues to use AlertAgent's existing
    singular self.x1/self.enemy/self.wincap/self.alert_vision/
    self._seen_enemies attributes completely unchanged -- every existing
    single-client install has zero behavior change. Additional clients
    (settings["clients"][1:]) get their own independent WindowCapture +
    Vision pair and dedup state here, so one client's cooldown/rearm
    tracking can never suppress or interfere with another's.

    Cooldown/trigger-count/currently-playing-sound state for extra
    clients lives in AlertAgent's existing dicts, keyed by
    (client_name, alarm_type) instead of a bare alarm_type string --
    see play_sound()/alarm_detection()/reset_alarm().
    """

    def __init__(
        self,
        name: str,
        character: str,
        x1: int, y1: int, x2: int, y2: int,
        x1_faction: int, y1_faction: int, x2_faction: int, y2_faction: int,
        enabled: bool,
        needle_paths: list,
        needle_faction_paths: list,
    ) -> None:
        self.name = name
        self.character = character
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.x1_faction, self.y1_faction = x1_faction, y1_faction
        self.x2_faction, self.y2_faction = x2_faction, y2_faction
        self.enabled = enabled
        self.wincap = WindowCapture()
        self.vision = Vision(needle_paths)
        self.vision_faction = Vision(needle_faction_paths)
        self.enemy = False
        self.faction = False
        self.enemy_points: list = []
        self.seen_enemies: dict = {}

    def region_key(self) -> tuple:
        """Identity+region fingerprint used to decide whether a settings
        reload can reuse this client's runtime state (preserving dedup/
        cooldown history) or must rebuild it from scratch."""
        return (
            self.name, self.x1, self.y1, self.x2, self.y2,
            self.x1_faction, self.y1_faction, self.x2_faction, self.y2_faction,
        )

    def close(self) -> None:
        self.wincap.close()
        self.vision.clean_up()
        self.vision_faction.clean_up()

# Sound file paths (safe to resolve at import time — no directory listing)
ALARM_SOUND = get_resource_path(f"{SOUND_FOLDER}/{ALARM_SOUND_FILE}")
FACTION_SOUND = get_resource_path(f"{SOUND_FOLDER}/{FACTION_SOUND_FILE}")

logger = logging.getLogger("alert")


def _load_image_files() -> tuple[list[str], list[str]]:
    """Resolve template image paths from both the bundled and user img/ directories.

    Scans the bundled evealert/img/ (or _MEIPASS/img/ when frozen) AND the
    user-writable img/ directory alongside settings.json so users can add
    custom templates without modifying the application install.
    """
    locations = [get_resource_path(IMG_FOLDER), str(get_user_img_path())]
    alert_files: list[str] = []
    faction_files: list[str] = []

    for folder in locations:
        if not os.path.isdir(folder):
            continue
        for f in sorted(os.listdir(folder)):
            path = os.path.join(folder, f)
            if f.startswith(ALERT_IMAGE_PREFIX):
                alert_files.append(path)
            elif f.startswith(FACTION_IMAGE_PREFIX):
                faction_files.append(path)

    return alert_files, faction_files


def _format_intel_age(seconds: float) -> str:
    """Render a report age as a short human string ("45s", "2m", "1h") for
    the #212 intel-correlation line."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h"


def _normalize_intel_line(line: str) -> str:
    """#171: normalize a raw intel-channel line for cross-channel dedup.

    Strips the EVE chat-log timestamp (varies even for a genuinely
    copy-pasted duplicate posted a moment later in another channel) and
    compares on (reporting pilot, message body) instead. Falls back to
    the raw stripped/lowercased line when it doesn't match the standard
    chat-log header format.
    """
    from evealert.tools.intel_parser import _strip_header  # noqa: PLC0415

    parsed = _strip_header(line)
    if parsed is None:
        return line.strip().lower()
    pilot, message = parsed
    return f"{pilot.strip().lower()}|{message.strip().lower()}"


class AlertAgent:
    """Alert Agent for EVE Online local chat monitoring.

    Runs three asyncio tasks in a background daemon thread:
      - vision_thread: enemy detection loop
      - vision_faction_thread: faction detection loop
      - run: alarm trigger, cooldown, webhook dispatch

    THREAD SAFETY: Engine-to-GUI communication goes exclusively through
    self._bridge (UIBridge).  self.main is kept only for WindowCapture
    compatibility and must not be accessed for any GUI operation.
    """

    # #213: minimum seconds between OCR-based enemy-identity re-resolves
    # while the SET of detected icon positions is unchanged. A capture+
    # recognize pass costs ~0.3-1s; running it on every 0.1-0.2s poll cycle
    # would be prohibitively expensive. A genuinely new icon position always
    # triggers an immediate re-resolve regardless of this interval (see
    # _resolve_enemy_identities).
    _IDENTITY_RESOLVE_MIN_INTERVAL = 1.5

    # #212: how far back an intel-channel report can be and still be
    # considered "current" enough to surface alongside an Enemy alarm for
    # the same pilot — ships change and reports age, so this is a hard
    # cutoff rather than a decaying weight.
    _INTEL_CORRELATION_WINDOW_SECONDS = 600  # 10 minutes

    # #171: a paste posted in multiple watched intel channels within this
    # many seconds of itself is treated as one duplicated event, not N
    # separate hostile reports.
    _INTEL_DEDUP_WINDOW_SECONDS = 30

    # #177: how often the cache-maintenance task purges expired TTL-cache
    # entries (zKB kill lookups, universe kill-count/heatmap caches).
    _CACHE_MAINTENANCE_INTERVAL_SECONDS = 900  # 15 minutes

    def __init__(self, main: "MainMenu"):
        self.main = main
        # Bridge + store must be created first so the rest of __init__ can use them
        self._bridge: UIBridge = main  # main is a UIBridge-compatible object (_MainProxy in Qt path)
        self._settings_store = get_settings_store()
        self._webhook = None  # dhooks_lite.Webhook; populated by load_settings()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.wincap = WindowCapture(self.main)

        # Coordinate sentinels — will be overwritten by load_settings()
        self.x1 = self.y1 = self.x2 = self.y2 = 0
        self.x1_faction = self.y1_faction = self.x2_faction = self.y2_faction = 0

        # Detection settings
        self.detection = 90
        self.detection_faction = 90
        # #175: vision pipeline downscale factor (1.0 = off)
        self._detection_downscale = 1.0

        # Load template images with a user-facing error on failure
        try:
            alert_files, faction_files = _load_image_files()
        except OSError as e:
            err_msg = f"Error: Cannot load images: {e}"
            logger.error("Failed to load template images: %s", e)
            self._bridge.log(err_msg, "red")
            alert_files, faction_files = [], []

        self.alert_vision = Vision(alert_files)
        self.alert_vision_faction = Vision(faction_files)
        self._alert_files = alert_files
        self._faction_files = faction_files

        # Main state
        self.running = False
        self.check = False

        # Vision flags (written by vision tasks, read by run task)
        self.enemy = False
        self.faction = False
        # Match centers from the last enemy scan (for per-enemy dedup, #100)
        self._enemy_points: list = []
        # Dedup identity (resolved pilot name, or quantized position as a
        # fallback when OCR can't identify a given icon) -> sighting record
        self._seen_enemies: dict = {}
        self._rearm_minutes: int = 0  # 0 = disabled

        # #174: additional EVE clients beyond the primary (empty by
        # default -- every existing single-client install stays exactly
        # as it was). Populated by load_settings() from settings["clients"].
        self._extra_clients: list = []
        self._extra_client_tasks: list = []

        # #213: per-icon name resolution, throttled independently of the
        # main 0.1-0.2s poll loop so OCR doesn't run on every single cycle.
        self._last_identity_keys: frozenset = frozenset()
        self._last_identity_resolve_time: float = 0.0
        self._last_enemy_identities: dict = {}  # quantized position -> name
        # Last OCR [alarm] diagnostic line actually logged, so an unchanged
        # result on a later fresh resolve doesn't re-print the same line.
        self._last_ocr_log_message: str = ""

        # Long-running asyncio task handles (cancelled in stop(), #102)
        self.vision_t = None
        self.vision_faction_t = None
        self.alert_t = None
        self._thera_task = None
        self._sov_task = None
        self._esi_standings_task = None
        self._gatecamp_task = None
        self._cache_maintenance_task_handle = None

        # Alarm settings
        self.cooldown_timers: dict = {}
        self.cooldowntimer = DEFAULT_COOLDOWN_TIMER
        self.alarm_detected = False
        self.mute = False
        self.volume = 1.0

        # Webhook — Discord Webhook object; managed by load_settings()
        self.webhook_cooldown_timer = 0
        self.webhook_sent = False

        # Sound
        self.alarm_trigger_counts: dict = {}
        self.max_sound_triggers = MAX_SOUND_TRIGGERS
        self.currently_playing_sounds: dict = {}

        # Statistics
        self.statistics = AlarmStatistics()
        lifetime_data = load_lifetime_stats()
        if lifetime_data:
            self.statistics.load_lifetime(lifetime_data)

        # Sound paths (set by load_settings; default to bundled files)
        self._alarm_sound = ALARM_SOUND
        self._faction_sound = FACTION_SOUND

        # Per-image threshold overrides (set by load_settings)
        self.image_thresholds: dict = {}

        # Intelligence settings
        self._zkillboard_enabled = False
        self._zkillboard_cooldown = 300
        self._zkillboard_next_lookup: float = 0.0
        self._intel_log_enabled = False
        self._intel_log_channel = ""  # legacy single-channel setting, read-only (#171)
        self._intel_channels: list[str] = []  # #171: one IntelWatcher per entry
        self._intel_log_dir: str = ""  # #191: empty = auto-detect via get_eve_chatlog_dir()
        self._intel_watchers: list = []  # IntelWatcher instances, replaces the old singular _intel_watcher
        # #171: cross-channel duplicate-paste dedup (normalized line -> last-seen epoch)
        self._recent_intel_lines: dict = {}
        self._intel_threat_check_enabled = False  # #198
        self._intel_threat_radius = 5             # #198
        # #212: rolling buffer of recent intel-channel reports so a
        # resolved Enemy-alarm pilot can be cross-referenced against what
        # intel just said about them (e.g. current ship) without re-reading
        # the chat log. (timestamp, IntelReport) tuples, oldest first.
        self._intel_reports_recent: deque = deque(maxlen=50)
        self._correlate_intel_enabled = True
        # #214: persistent pilot-sighting history retention window (days)
        self._pilot_history_retention_days = 180
        # #215: gate for recording sightings from Local alarms/intel mentions
        self._pilot_history_enabled = True

        # ESI augmentation
        self._esi_enabled = False
        self._esi_show_corp = True
        self._esi_show_alliance = True
        self._esi_alert_flashy = False
        self._threat_tiers: dict = {}

        # Per-type sound cooldown (seconds)
        self._cooldown_enemy = DEFAULT_COOLDOWN_TIMER
        self._cooldown_faction = DEFAULT_COOLDOWN_TIMER

        # Webhook template and per-type webhook targets
        self._webhook_template = (
            "{alarm_type} detected in {system} at {time} (session #{count})"
        )
        self._webhook_enemy_url = ""
        self._webhook_enemy_min = 0
        self._webhook_faction_url = ""
        self._webhook_faction_min = 0

        # Plugin manager — load plugins after settings so dir is known
        self._plugins_enabled = True

        # Web status server
        self._web_ui_enabled = False
        self._web_ui_port = 8765
        self._web_server = None

        # v3.2: adjacent system awareness
        self._adjacent_enabled = False
        self._adjacent_max_jumps = 3
        self._adjacent_poll_interval = 120
        self._adjacent_min_kills = 1
        self._adjacent_destination = ""
        self._neighbor_monitor = None

        # v7.1 (#169): live killmail stream (R2Z2, RedisQ's replacement) --
        # preferred over the adjacent-system poller when enabled.
        self._r2z2_enabled = False
        self._r2z2_alarm_jumps = 2
        self._r2z2_watch_jumps = 5
        self._r2z2_alliance_watchlist: set = set()
        self._r2z2_last_sequence: int | None = None
        self._r2z2_consumer = None

        # v7.1 (#170): gate-camp detection from the R2Z2 kill buffer.
        # (system_id, location_id) -> last-warned timestamp, so a
        # standing camp doesn't re-alarm every monitor cycle.
        self._gatecamp_last_warned: dict = {}

        # v3.3: D-scan monitor
        self._dscan_enabled = False
        self._dscan_alert_red = True
        self._dscan_alert_orange = False
        self._dscan_alert_probes = True
        self._dscan_alert_new_sig = True  # (#145)
        self._dscan_watcher = None
        self._dscan_last_classes: set = set()  # ShipThreatClass values seen this cycle

        # v3.4: KOS checker
        self._kos_cva_enabled = True
        self._kos_custom_urls: list = []

        # v3.5: push notifications + alarm options
        self._push_config: dict = {}
        self._auto_screenshot = False
        self._escalation_threshold = 0
        self._local_hostile_count = 0  # track for escalation
        self._tts_enabled = False
        self._tts_rate = 175
        # Automation bridge (#153)
        self._automation_enabled = False
        self._automation_webhook_url = ""

        # v3.6: wormhole awareness
        self._thera_enabled = False
        self._thera_max_jumps = 5
        self._wh_drop_enabled = False
        self._wh_drop_threshold = 3
        self._wh_drop_detector = None
        # Names already counted toward the current WH-drop window (#106)
        self._wh_drop_seen_names: set = set()

        # v3.7: fleet context
        self._fleet_composition_enabled = False
        self._fleet_killmail_enabled = False
        self._fleet_tracked_ids: list = []
        self._killmail_monitor = None

        # v4.0: ESI OAuth deep integration
        self._esi_standings_classify = False
        self._standings_filter_blues = False  # (#147)
        self._esi_fleet_monitor = False
        self._esi_structure_alerts = False
        self._esi_standings_cache: dict = {}
        # Peak hours monitor (#151)
        self._peak_hours_warning = True
        self._peak_threshold_multiplier = 1.5  # character_id → standing float

        # v4.1: OCR pilot-name detection (#98)
        self._ocr_enabled = False
        self._ocr_region = (0, 0, 0, 0)
        self._last_ocr_names: list[str] = []   # names from last _build_enemy_alarm_text OCR

        # v4.2: diagnostic verbose-logging mode
        self._diagnostics_enabled = False

        self.load_settings()
        self._load_plugins()
        self._validate_audio_files()
        self._prune_pilot_history()

    # ------------------------------------------------------------------
    # Thread safety helper
    # ------------------------------------------------------------------

    def _ui(self, fn: Callable, *args, **kwargs) -> None:
        """Call a _MainProxy method from any thread — thread-safe.

        In the Qt path, all _MainProxy methods emit signals on QtBridge.
        Qt signals use queued connections across threads, so calling
        _MainProxy methods directly from the alert daemon thread is safe:
        the message is delivered to the Qt main thread by the signal system.

        The previous implementation used `fn is self.main.write_message`
        (an identity check on a bound method) which is *always False* in
        Python 3 — bound methods are not cached, so each attribute access
        creates a new object.  That caused all calls to fall through to the
        QTimer.singleShot fallback, which may not fire reliably from a
        non-Qt thread (#190-followup).

        The fix: just call fn(*args) directly.  All callers pass _MainProxy
        methods that route through thread-safe signals.
        """
        try:
            fn(*args, **kwargs)
        except Exception as exc:
            logger.debug("_ui dispatch error (%s): %s", getattr(fn, "__name__", fn), exc)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self.running

    @property
    def is_alarm(self) -> bool:
        return self.alarm_detected

    @property
    def is_enemy(self) -> bool:
        return self.enemy

    @property
    def is_faction(self) -> bool:
        return self.faction

    def get_statistics(self) -> AlarmStatistics:
        return self.statistics

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _load_plugins(self) -> None:
        """Discover and load user plugins from the plugins directory."""
        if not self._plugins_enabled:
            return
        try:
            from evealert.settings.helper import (  # pylint: disable=import-outside-toplevel
                get_user_plugins_path,
            )
            from evealert.tools.plugin_loader import (  # pylint: disable=import-outside-toplevel
                get_plugin_manager,
            )

            pm = get_plugin_manager()
            plugin_dir = get_user_plugins_path()
            count = pm.load_plugins(plugin_dir)
            if count:
                self._ui(
                    self.main.write_message,
                    f"Plugins: loaded {count} plugin(s) from {plugin_dir}",
                    "green",
                )
        except Exception as exc:
            logger.warning("Plugin load error: %s", exc)

    def _validate_audio_files(self) -> None:
        """Validate that required audio files exist."""
        valid_alarm, error_alarm = ConfigValidator.validate_audio_file(
            ALARM_SOUND, "Alarm sound"
        )
        valid_faction, error_faction = ConfigValidator.validate_audio_file(
            FACTION_SOUND, "Faction sound"
        )

        if not valid_alarm:
            logger.warning(error_alarm)
            self._ui(self.main.write_message, f"Warning: {error_alarm}", "red")

        if not valid_faction:
            logger.warning(error_faction)
            self._ui(self.main.write_message, f"Warning: {error_faction}", "red")

    def _prune_pilot_history(self) -> None:
        """Prune the persistent pilot-sighting store (#214) once per app
        start -- not on every load_settings() reload, since that can fire
        repeatedly during a session whenever settings.json changes."""
        try:
            from evealert.tools.pilot_history_store import (  # noqa: PLC0415
                prune_older_than,
            )

            prune_older_than(self._pilot_history_retention_days)
        except Exception as exc:
            logger.debug("Pilot history prune failed: %s", exc)

    def _build_intel_watchers(self) -> list:
        """#171: construct one IntelWatcher per entry in self._intel_channels.

        Extracted from start() so the per-channel wiring (channel tagging,
        shared cross-channel dedup) is directly unit-testable without
        needing a running event loop.
        """
        from pathlib import Path  # noqa: PLC0415

        from evealert.tools.intel_watcher import (  # pylint: disable=import-outside-toplevel
            IntelWatcher,
            get_eve_chatlog_dir,
        )

        # #191: an explicit directory override takes precedence; empty
        # means fall back to auto-detection, same as pre-#191 behavior.
        chatlog_dir = Path(self._intel_log_dir) if self._intel_log_dir else get_eve_chatlog_dir()

        watchers = []
        for channel in self._intel_channels:
            watcher = IntelWatcher(
                channel_pattern=channel,
                channel_name=channel,
                chatlog_dir=chatlog_dir,
                # Bind `channel` at lambda-definition time (default arg),
                # not call time, so each watcher's callback reports ITS
                # OWN channel rather than whichever channel the loop
                # variable last held.
                callback=lambda line, ch=channel: self._on_intel_line(
                    line, channel=ch
                ),
                on_intel=self._on_intel_report,
                is_duplicate=self._is_duplicate_intel_line,
            )
            watchers.append(watcher)
        return watchers

    async def _start_r2z2_consumer(self, system_name: str) -> None:
        """#169: resolve the configured system to an ID and start the R2Z2
        live-kill consumer. Split out of start() (which runs synchronously
        before the loop is pumping) because system-name resolution needs
        an HTTP call."""
        try:
            from evealert.tools.r2z2 import R2Z2Consumer  # noqa: PLC0415
            from evealert.tools.universe import get_universe_cache  # noqa: PLC0415

            origin_id = await get_universe_cache().get_system_id(system_name)
            if origin_id is None:
                logger.warning(
                    "R2Z2: could not resolve system %r to an ID -- consumer not started",
                    system_name,
                )
                return
            self._r2z2_consumer = R2Z2Consumer(
                origin_system_id=origin_id,
                watch_jumps=self._r2z2_watch_jumps,
                alliance_watchlist=self._r2z2_alliance_watchlist,
                on_kill=self._on_r2z2_kill,
                sequence=self._r2z2_last_sequence,
            )
            self.loop.create_task(self._r2z2_consumer.run())
            # #170: gate-camp clustering rides on the same live-kill buffer.
            self._gatecamp_task = self.loop.create_task(
                self._gatecamp_monitor(origin_id)
            )
        except Exception as exc:
            logger.debug("R2Z2 consumer failed to start: %s", exc)

    async def _gatecamp_monitor(self, origin_id: int) -> None:
        """#170: periodically check the R2Z2 kill buffer for gate-camp
        clustering and warn once per camp per hour when a full-confidence
        camp is within adjacent.max_jumps. Reuses the existing "adjacent"
        settings (poll_interval, max_jumps) rather than introducing a
        separate config block -- this is an analysis layer on top of the
        same live-kill feed, not a second data source."""
        from evealert.tools.gatecamp import get_active_camps, resolve_camp_names  # noqa: PLC0415
        from evealert.tools.universe import get_universe_cache  # noqa: PLC0415

        cache = get_universe_cache()
        try:
            while self.running and self._r2z2_consumer is not None:
                await asyncio.sleep(self._adjacent_poll_interval)
                if not self.running or self._r2z2_consumer is None:
                    break
                try:
                    camps = [
                        c for c in get_active_camps(self._r2z2_consumer)
                        if c.confidence == "camp"
                    ]
                    if not camps:
                        continue
                    nearby = await cache.get_systems_within_jumps(
                        origin_id, self._adjacent_max_jumps
                    )
                    now = time.time()
                    for camp in camps:
                        jump_dist = nearby.get(camp.system_id)
                        if jump_dist is None:
                            continue
                        key = (camp.system_id, camp.location_id)
                        if now - self._gatecamp_last_warned.get(key, 0.0) < 3600:
                            continue
                        self._gatecamp_last_warned[key] = now
                        await resolve_camp_names([camp])
                        label = camp.gate_name or camp.system_name or f"system {camp.system_id}"
                        self._ui(
                            self.main.write_message,
                            f"GATE CAMP: {label} ({jump_dist}j away) — "
                            f"{camp.kill_count} kills, "
                            f"{int(camp.last_kill_age_seconds)}s since last",
                            "red",
                        )
                except Exception as exc:
                    logger.debug("Gate-camp monitor cycle failed: %s", exc)
        except Exception as exc:
            logger.debug("Gate-camp monitor init error: %s", exc)

    def _on_r2z2_kill(self, killmail, jump_dist: int | None) -> None:
        """R2Z2Consumer.on_kill callback (#169) -- called synchronously from
        the consumer's poll loop on the alert asyncio thread. Ship-name and
        system-name resolution need HTTP calls, so hand off to a task
        rather than blocking the poll loop."""
        self._r2z2_last_sequence = (
            self._r2z2_consumer.last_sequence if self._r2z2_consumer else None
        )
        self.loop.create_task(self._report_r2z2_kill(killmail, jump_dist))

    async def _report_r2z2_kill(self, killmail, jump_dist: int | None) -> None:
        """Log a `LIVE KILL: ...` line for a matched R2Z2 kill and, when it
        happened within the configured alarm radius, trigger the alarm
        sound (reusing the Enemy alarm's cooldown machinery)."""
        try:
            from evealert.tools.r2z2 import resolve_ship_name  # noqa: PLC0415
            from evealert.tools.universe import get_universe_cache  # noqa: PLC0415
            from evealert.tools.http_common import DEFAULT_HEADERS  # noqa: PLC0415
            import httpx  # noqa: PLC0415

            cache = get_universe_cache()
            system_name = await cache.get_system_name(killmail.solar_system_id)
            system_label = system_name or f"system {killmail.solar_system_id}"
            async with httpx.AsyncClient(timeout=8.0, headers=DEFAULT_HEADERS) as client:
                ship_name = await resolve_ship_name(client, killmail.victim_ship_type_id)
            ship_label = ship_name or "Unknown ship"

            jump_note = f"({jump_dist}j away)" if jump_dist is not None else "(watchlist)"
            msg = (
                f"LIVE KILL: {ship_label} destroyed in {system_label} {jump_note} "
                f"— {killmail.attacker_count} attackers"
            )
            self._ui(self.main.write_message, msg, "yellow")

            if jump_dist is not None and jump_dist <= self._r2z2_alarm_jumps:
                await self.play_sound(ALARM_SOUND, "Enemy")
        except Exception as exc:
            logger.debug("R2Z2 kill report failed: %s", exc)

    def _get_adjacent_kill_count(self) -> int:
        """Recent-kill count for the threat score's adjacent_kills signal.

        Prefers the R2Z2 live consumer's buffer (#169, seconds-fresh)
        over NeighborMonitor's poll-cycle count when R2Z2 is active --
        it's the same poll_interval window either way, just fed from a
        push stream instead of a periodic zKB list poll.
        """
        if self._r2z2_consumer is not None:
            return self._r2z2_consumer.kill_count_since(self._adjacent_poll_interval)
        if self._neighbor_monitor and hasattr(self._neighbor_monitor, "last_kill_count"):
            return self._neighbor_monitor.last_kill_count
        return 0

    def start(self) -> bool:
        """Start the detection engine in this (daemon) thread."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        self.loop.run_until_complete(self.vision_check())
        if self.check:
            self.vision_t = self.loop.create_task(self.vision_thread())
            self.vision_faction_t = self.loop.create_task(self.vision_faction_thread())
            self.alert_t = self.loop.create_task(self.run())

            # #174: one vision task pair per enabled extra client.
            self._extra_client_tasks = []
            for client in self._extra_clients:
                if not client.enabled:
                    continue
                self._extra_client_tasks.append(
                    self.loop.create_task(self._extra_client_vision_thread(client))
                )
                self._extra_client_tasks.append(
                    self.loop.create_task(self._extra_client_vision_faction_thread(client))
                )

            # Start intel log watcher(s) if configured -- one per channel (#171)
            if self._intel_log_enabled and self._intel_channels:
                self._intel_watchers = self._build_intel_watchers()
                for watcher in self._intel_watchers:
                    self.loop.create_task(watcher.run())

            self.running = True
            self._ui(self.main.write_message, "System: EVE Alert started.", "green")
            # Fire background update check — non-blocking, silent on failure
            self.loop.create_task(self._check_for_update())
            # v3.2: pipe/pocket classification + sovereignty display (one-shot at start)
            self.loop.create_task(self._display_system_info())
            # v7.1 (#169): live kill feed (R2Z2) supersedes the adjacent-system
            # poller when enabled -- push delivery within seconds vs. a
            # 60s+ poll loop. NeighborMonitor becomes optional/legacy.
            if self._r2z2_enabled:
                system_name = self._settings_store.get("server.system", "").strip()
                if system_name and system_name != "Enter a System Name":
                    self.loop.create_task(self._start_r2z2_consumer(system_name))
            elif self._adjacent_enabled:
                # v3.2: adjacent system kill monitor
                system_name = self._settings_store.get("server.system", "").strip()
                if system_name and system_name != "Enter a System Name":
                    from evealert.tools.neighbor_monitor import (  # pylint: disable=import-outside-toplevel
                        NeighborMonitor,
                    )

                    self._neighbor_monitor = NeighborMonitor(
                        system_name=system_name,
                        max_jumps=self._adjacent_max_jumps,
                        min_kills=self._adjacent_min_kills,
                        poll_interval=self._adjacent_poll_interval,
                        callback=lambda msg: self._ui(
                            self.main.write_message, msg, "yellow"
                        ),
                    )
                    self.loop.create_task(self._neighbor_monitor.run())
            # v3.3: D-scan watcher
            if self._dscan_enabled:
                from evealert.tools.dscan_watcher import (  # pylint: disable=import-outside-toplevel
                    DscanWatcher,
                )

                self._dscan_watcher = DscanWatcher(
                    on_threat=self._on_dscan_threat,
                    on_probe=self._on_dscan_probe,
                    on_entry=self._on_dscan_entry,
                    on_new_signature=self._on_dscan_new_signature,
                )
                self.loop.create_task(self._dscan_watcher.run())
            # Start web status server if enabled
            if self._web_ui_enabled:
                from evealert.tools.web_server import (  # pylint: disable=import-outside-toplevel
                    WebStatusServer,
                )

                self._web_server = WebStatusServer(
                    port=self._web_ui_port,
                    stats_ref=self.statistics,
                    running_ref=[self.running],
                )
                self.loop.create_task(self._web_server.serve())
                self._ui(
                    self.main.write_message,
                    f"Web UI: http://127.0.0.1:{self._web_ui_port}/",
                    "green",
                )
            # v3.6: wormhole awareness tasks
            if self._thera_enabled:
                self._thera_task = self.loop.create_task(self._thera_monitor())
            if self._wh_drop_enabled:
                from evealert.tools.wormhole import (  # pylint: disable=import-outside-toplevel
                    WhDropDetector,
                )

                self._wh_drop_detector = WhDropDetector(
                    threshold=self._wh_drop_threshold
                )
            # v3.7: killmail monitor
            if self._fleet_killmail_enabled and self._fleet_tracked_ids:
                from evealert.tools.fleet_context import (  # pylint: disable=import-outside-toplevel
                    KillmailMonitor,
                )

                self._killmail_monitor = KillmailMonitor(
                    character_ids=self._fleet_tracked_ids,
                    callback=lambda msg: self._ui(self.main.write_message, msg, "cyan"),
                )
                self.loop.create_task(self._killmail_monitor.run())
            # v4.0: ESI standings + fleet monitor
            if (
                self._esi_standings_classify
                or self._esi_fleet_monitor
                or self._esi_structure_alerts
            ):
                self.loop.create_task(self._esi_deep_integration_start())
            # Peak hours monitor (#151)
            if self._peak_hours_warning:
                self.loop.create_task(self._peak_hours_monitor())
            # #177: periodic TTL-cache purge, always on (independent of
            # any single feature toggle -- zKB-on-alarm alone populates
            # these caches over a long session).
            self._cache_maintenance_task_handle = self.loop.create_task(
                self._cache_maintenance_task()
            )
            # Notify plugins that detection has started
            try:
                from evealert.tools.plugin_loader import (  # pylint: disable=import-outside-toplevel
                    get_plugin_manager,
                )

                get_plugin_manager().call("on_start")
            except Exception:
                pass
            self.loop.run_forever()
            logger.debug("Alert loop terminated.")
            return True
        return False

    def _shutdown_loop(self) -> None:
        """Cancel all long-running tasks then stop the loop.

        Runs on the loop's own thread (scheduled via call_soon_threadsafe),
        so task cancellation is thread-safe (#102).
        """
        for task in (
            self.vision_t,
            self.vision_faction_t,
            self.alert_t,
            self._thera_task,
            self._sov_task,
            self._esi_standings_task,
            self._gatecamp_task,
            self._cache_maintenance_task_handle,
            *self._extra_client_tasks,
        ):
            if task is not None and not task.done():
                task.cancel()
        # Close the mss/dxcam capture backend(s) on the thread that created
        # them (#190) -- extra clients' WindowCapture instances (#174) were
        # ALSO created on this same alert thread (inside their vision
        # tasks), so they must be closed here too, not from stop()'s caller
        # thread.
        self.wincap.close()
        for client in self._extra_clients:
            client.wincap.close()
        self.loop.stop()

    def stop(self) -> None:
        """Stop the detection engine. Safe to call from any thread."""
        # Flip the flag first so any monitor mid-iteration sees False before
        # the loop is torn down (#102).
        self.running = False
        # Signal class-based monitors (thread-safe flag flips).
        for monitor in (
            self._web_server,
            self._neighbor_monitor,
            self._dscan_watcher,
            self._killmail_monitor,
            self._r2z2_consumer,
            *self._intel_watchers,
        ):
            if monitor is not None:
                monitor.stop()
        # Persist the R2Z2 sequence position (#169) so a restart resumes
        # near the live tail instead of re-fetching a fresh one. Read-merge-
        # write against the shared SettingsStore cache -- never seeded from
        # DEFAULT_SETTINGS (#108).
        if self._r2z2_consumer is not None:
            try:
                self._settings_store.set(
                    "r2z2.last_sequence", self._r2z2_consumer.last_sequence
                )
                self._settings_store.save()
            except Exception as exc:
                logger.debug("R2Z2: failed to persist last_sequence: %s", exc)
        # Cancel raw asyncio tasks and stop the loop on the loop's own thread.
        # wincap.close() is deferred to _shutdown_loop() so it executes on the
        # alert thread that owns the mss OS handles (#190).
        if self.loop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(self._shutdown_loop)
        try:
            from evealert.tools.plugin_loader import (  # pylint: disable=import-outside-toplevel
                get_plugin_manager,
            )

            get_plugin_manager().call("on_stop")
        except Exception:
            pass
        # Wrap the rest of cleanup so one failure doesn't abort stats persistence
        try:
            self._web_server = None
            self._neighbor_monitor = None
            self._dscan_watcher = None
            self._killmail_monitor = None
            self._r2z2_consumer = None
            self._intel_watchers = []
            self._extra_client_tasks = []
            # NOTE: wincap.close() is intentionally NOT called here — it runs via
            # _shutdown_loop() on the alert thread to respect mss thread affinity.
            self.currently_playing_sounds.clear()
            self.alarm_trigger_counts.clear()
            self.cooldown_timers.clear()
            self.alert_vision.debug_mode = False
            self.alert_vision_faction.debug_mode_faction = False
            self._ui(self.main.update_alert_button)
            self._ui(self.main.update_faction_button)
        except Exception:
            logger.exception("Non-fatal error during stop() cleanup")
        # Persist stats even if cleanup above partially failed
        try:
            save_lifetime_stats(self.statistics)
            save_session_report(self.statistics, time.time())
        except Exception:
            logger.exception("Failed to save statistics on stop()")

    def clean_up(self) -> None:
        self.stop()

    def load_settings(self) -> None:
        settings = self._settings_store.load()

        if settings:
            is_valid, errors = ConfigValidator.validate_settings_dict(settings)
            if not is_valid:
                error_msg = "Configuration validation failed:\n" + "\n".join(errors)
                logger.error(error_msg)
                self._ui(
                    self.main.write_message,
                    "Settings validation failed. Check logs.",
                    "red",
                )
                for error in errors:
                    self._ui(self.main.write_message, f"  - {error}", "red")
                return

            # #174: multi-client support (MVP). A non-empty settings["clients"]
            # list makes clients[0] the primary region source -- still the
            # SAME self.x1/y1/x2/y2 attributes every other part of the engine
            # already reads, just sourced from the list instead of the
            # top-level keys. clients[1:] become extra, independent clients
            # (see _rebuild_extra_clients() below). An absent/empty list (the
            # default, and every pre-#174 install) reads the legacy
            # top-level alert_region_1/2 keys exactly as before -- one-way
            # migration, zero behavior change until a user opts in.
            clients_setting = settings.get("clients") or []
            if clients_setting:
                primary = clients_setting[0]
                self.x1 = int(primary.get("alert_region_1", {}).get("x", 0))
                self.y1 = int(primary.get("alert_region_1", {}).get("y", 0))
                self.x2 = int(primary.get("alert_region_2", {}).get("x", 0))
                self.y2 = int(primary.get("alert_region_2", {}).get("y", 0))
                self.x1_faction = int(primary.get("faction_region_1", {}).get("x", 0))
                self.y1_faction = int(primary.get("faction_region_1", {}).get("y", 0))
                self.x2_faction = int(primary.get("faction_region_2", {}).get("x", 0))
                self.y2_faction = int(primary.get("faction_region_2", {}).get("y", 0))
            else:
                self.x1 = int(settings["alert_region_1"]["x"])
                self.y1 = int(settings["alert_region_1"]["y"])
                self.x2 = int(settings["alert_region_2"]["x"])
                self.y2 = int(settings["alert_region_2"]["y"])
                self.x1_faction = int(settings["faction_region_1"]["x"])
                self.y1_faction = int(settings["faction_region_1"]["y"])
                self.x2_faction = int(settings["faction_region_2"]["x"])
                self.y2_faction = int(settings["faction_region_2"]["y"])
            self.detection = int(settings["detectionscale"]["value"])
            self.detection_faction = int(settings["faction_scale"]["value"])
            # #175: clamp to a sane range -- 0 or negative would make every
            # cv.resize() call a no-op-or-crash rather than "off".
            downscale = float(settings.get("detection", {}).get("downscale", 1.0))
            self._detection_downscale = max(0.1, min(downscale, 1.0))
            # #176: hot-swappable capture backend. set_backend() is a no-op
            # when unchanged, so this is safe to call on every reload.
            capture_backend = str(settings.get("detection", {}).get("capture_backend", "mss"))
            self.wincap.set_backend(capture_backend)
            self.cooldowntimer = int(settings["cooldown_timer"]["value"])
            self.volume = settings.get("volume", {}).get("value", 100) / 100.0
            self.mute = settings["server"]["mute"]

            # Resolve sound paths: use user-specified file if it exists, else bundled default
            sounds = settings.get("sounds", {})
            user_alarm = sounds.get("alarm", "")
            user_faction = sounds.get("faction", "")
            self._alarm_sound = (
                user_alarm if user_alarm and os.path.isfile(user_alarm) else ALARM_SOUND
            )
            self._faction_sound = (
                user_faction
                if user_faction and os.path.isfile(user_faction)
                else FACTION_SOUND
            )

            # Per-image threshold overrides: {basename: int 0-100 or null}
            self.image_thresholds = settings.get("image_thresholds", {})

            # Intelligence settings
            intel = settings.get("intelligence", {})
            self._zkillboard_enabled = bool(intel.get("zkillboard_enabled", False))
            self._zkillboard_cooldown = int(intel.get("zkillboard_cooldown", 300))
            self._intel_log_enabled = bool(intel.get("intel_log_enabled", False))
            self._intel_log_channel = str(intel.get("intel_log_channel", ""))
            # Multi-channel intel watcher (#171): intel_channels is the new
            # canonical list-valued key. Old configs that only have the
            # legacy single-channel string migrate transparently into a
            # one-element list rather than losing their configured channel.
            raw_channels = intel.get("intel_channels")
            if not raw_channels:
                legacy = self._intel_log_channel.strip()
                raw_channels = [legacy] if legacy else []
            self._intel_channels = [
                str(c).strip() for c in raw_channels if str(c).strip()
            ]
            # Chatlog directory override (#191) -- empty means auto-detect
            self._intel_log_dir = str(intel.get("intel_log_dir", "")).strip()
            # Peak hours warning (#151)
            self._peak_hours_warning = bool(intel.get("peak_hours_warning", True))
            self._peak_threshold_multiplier = float(intel.get("peak_threshold_multiplier", 1.5))
            # Intel threat-radius gating (#198)
            self._intel_threat_check_enabled = bool(intel.get("intel_threat_check_enabled", False))
            self._intel_threat_radius = int(intel.get("intel_threat_radius", 5))
            # Intel-report correlation on Enemy alarms (#212)
            self._correlate_intel_enabled = bool(intel.get("correlate_intel_reports", True))
            # Persistent pilot-sighting history (#214/#215, v7.0)
            self._pilot_history_retention_days = int(
                intel.get("pilot_history_retention_days", 180)
            )
            self._pilot_history_enabled = bool(intel.get("pilot_history_enabled", True))

            # ESI augmentation settings
            esi = settings.get("esi", {})
            self._esi_enabled = bool(esi.get("enabled", False))
            self._esi_show_corp = bool(esi.get("show_corp", True))
            self._esi_show_alliance = bool(esi.get("show_alliance", True))
            self._esi_alert_flashy = bool(esi.get("alert_flashy", False))

            # Threat tiers {name_substring: tier}
            self._threat_tiers: dict = dict(settings.get("threat_tiers", {}))

            # Web status UI settings
            web = settings.get("web_ui", {})
            self._web_ui_enabled = bool(web.get("enabled", False))
            self._web_ui_port = int(web.get("port", 8765))

            # Adjacent system monitor settings
            adj = settings.get("adjacent", {})
            self._adjacent_enabled = bool(adj.get("enabled", False))
            self._adjacent_max_jumps = int(adj.get("max_jumps", 3))
            self._adjacent_poll_interval = int(adj.get("poll_interval", 120))
            self._adjacent_min_kills = int(adj.get("min_kills", 1))
            self._adjacent_destination = str(adj.get("destination_system", ""))

            # Live killmail stream settings (#169, v7.1)
            r2z2 = settings.get("r2z2", {})
            self._r2z2_enabled = bool(r2z2.get("enabled", False))
            self._r2z2_alarm_jumps = int(r2z2.get("alarm_jumps", 2))
            self._r2z2_watch_jumps = int(r2z2.get("watch_jumps", 5))
            self._r2z2_alliance_watchlist = {
                int(a) for a in r2z2.get("alliance_watchlist", []) if str(a).strip()
            }
            raw_sequence = r2z2.get("last_sequence")
            self._r2z2_last_sequence = int(raw_sequence) if raw_sequence is not None else None

            # D-scan monitor settings
            ds = settings.get("dscan", {})
            self._dscan_enabled = bool(ds.get("enabled", False))
            self._dscan_alert_red = bool(ds.get("alert_red", True))
            self._dscan_alert_orange = bool(ds.get("alert_orange", False))
            self._dscan_alert_probes = bool(ds.get("alert_probes", True))
            self._dscan_alert_new_sig = bool(ds.get("alert_new_signatures", True))

            # KOS settings
            kos = settings.get("kos", {})
            self._kos_cva_enabled = bool(kos.get("cva_enabled", True))
            self._kos_custom_urls = list(kos.get("custom_urls", []))

            # Push notifications + alarm options
            self._push_config = dict(settings.get("push", {}))
            notif = settings.get("notifications", {})
            self._auto_screenshot = bool(notif.get("auto_screenshot", False))
            self._escalation_threshold = int(notif.get("escalation_threshold", 0))
            self._tts_enabled = bool(notif.get("tts_enabled", False))
            self._tts_rate = int(notif.get("tts_rate", 175))
            # Automation bridge (#153)
            auto = settings.get("automation", {})
            self._automation_enabled = bool(auto.get("enabled", False))
            self._automation_webhook_url = str(auto.get("webhook_url", "")).strip()

            # Wormhole settings
            wh = settings.get("wormhole", {})
            self._thera_enabled = bool(wh.get("thera_enabled", False))
            self._thera_max_jumps = int(wh.get("thera_max_jumps", 5))
            self._wh_drop_enabled = bool(wh.get("wh_drop_enabled", False))
            self._wh_drop_threshold = int(wh.get("wh_drop_threshold", 3))

            # Fleet context settings
            fleet = settings.get("fleet", {})
            self._fleet_composition_enabled = bool(
                fleet.get("composition_enabled", False)
            )
            self._fleet_killmail_enabled = bool(fleet.get("killmail_enabled", False))
            self._fleet_tracked_ids = list(fleet.get("tracked_character_ids", []))

            # ESI OAuth deep integration
            esi = settings.get("esi_oauth", {})
            self._esi_standings_classify = bool(
                esi.get("standings_auto_classify", False)
            )
            self._standings_filter_blues = bool(
                esi.get("standings_filter_blues", False)
            )
            self._esi_fleet_monitor = bool(esi.get("fleet_monitor", False))
            self._esi_structure_alerts = bool(esi.get("structure_alerts", False))

            # OCR name detection (#98)
            ocr = settings.get("ocr", {})
            self._ocr_enabled = bool(ocr.get("enabled", False))
            reg = ocr.get("region", {})
            self._ocr_region = (
                int(reg.get("x1", 0)),
                int(reg.get("y1", 0)),
                int(reg.get("x2", 0)),
                int(reg.get("y2", 0)),
            )

            # Per-type cooldowns
            self._cooldown_enemy = int(
                settings.get("cooldown_timer_enemy", {}).get(
                    "value", self.cooldowntimer
                )
            )
            # Re-arm after sustained presence (#144); 0 = disabled
            self._rearm_minutes = int(
                settings.get("alerts", {}).get("rearm_minutes", 0)
            )
            self._cooldown_faction = int(
                settings.get("cooldown_timer_faction", {}).get(
                    "value", self.cooldowntimer
                )
            )

            # Webhook template
            self._webhook_template = settings.get("server", {}).get(
                "webhook_template",
                "{alarm_type} detected in {system} at {time} (session #{count})",
            )

            # Per-type webhook targets
            wh = settings.get("webhooks", {})
            self._webhook_enemy_url = wh.get("enemy", {}).get("url", "")
            self._webhook_enemy_min = int(wh.get("enemy", {}).get("min_count", 0))
            self._webhook_faction_url = wh.get("faction", {}).get("url", "")
            self._webhook_faction_min = int(wh.get("faction", {}).get("min_count", 0))

            # Diagnostic / verbose-logging mode (#v4.2)
            diag = settings.get("diagnostics", {})
            self._diagnostics_enabled = bool(diag.get("enabled", False))
            from evealert.settings.logger import (  # pylint: disable=import-outside-toplevel
                set_verbose,
            )

            set_verbose(
                self._diagnostics_enabled, restore_level=settings.get("log_level")
            )
            if self._diagnostics_enabled:
                from evealert.settings.diagnostics import (  # pylint: disable=import-outside-toplevel
                    write_context_log,
                )

                write_context_log(settings)

            # Webhook — create/update from settings so the engine owns it directly
            webhook_url = settings.get("server", {}).get("webhook", "")
            if webhook_url and webhook_url.startswith("https://discord.com/api/webhooks/"):
                try:
                    from dhooks_lite import Webhook  # noqa: PLC0415
                    self._webhook = Webhook(
                        webhook_url,
                        username="Gneuten",
                        avatar_url="https://cdn.discordapp.com/avatars/990582360103870495/410d536127874481b9771b9eb9aa8104.png",
                    )
                except Exception as e:
                    logger.error("Failed to create webhook: %s", e)
                    self._webhook = None
            else:
                self._webhook = None

            if self._settings_store.changed:
                vision_opened = self.alert_vision.is_vision_open
                faction_vision_opened = self.alert_vision_faction.is_faction_vision_open

                # Reload image file lists in case templates were added/removed
                try:
                    alert_files, faction_files = _load_image_files()
                    self._alert_files = alert_files
                    self._faction_files = faction_files
                except OSError as e:
                    logger.error("Failed to reload template images: %s", e)
                    alert_files = self._alert_files
                    faction_files = self._faction_files

                self.alert_vision = Vision(alert_files)
                self.alert_vision_faction = Vision(faction_files)
                if vision_opened:
                    self.set_vision()
                if faction_vision_opened:
                    self.set_vision_faction()
                self._ui(self.main.write_message, "Settings: Loaded.", "green")

            # #174: extra clients (settings["clients"][1:]) get their own
            # Vision instances built from the current template set. Runs
            # on every load_settings() call (not gated by
            # self._settings_store.changed like the alert_vision refresh
            # above) so the very first call -- from __init__, where
            # nothing has "changed" yet -- still picks up any clients
            # already present in settings.json. _rebuild_extra_clients()
            # is a cheap no-op when the desired config already matches
            # what's running.
            self._rebuild_extra_clients(clients_setting[1:], self._alert_files, self._faction_files)

    def _rebuild_extra_clients(
        self, client_configs: list, alert_files: list, faction_files: list
    ) -> None:
        """(Re)build self._extra_clients from settings["clients"][1:]
        (#174). A no-op when the desired client list is identical to what's
        already running (name + all region coords unchanged), so an
        unrelated settings save doesn't reset every extra client's dedup/
        cooldown history. Old clients are closed (releasing their
        WindowCapture/Vision resources) before new ones are built.
        """
        desired: list[tuple] = []
        for cfg in client_configs:
            if not isinstance(cfg, dict):
                continue
            desired.append((
                str(cfg.get("name", "")).strip() or "Client",
                int(cfg.get("alert_region_1", {}).get("x", 0)),
                int(cfg.get("alert_region_1", {}).get("y", 0)),
                int(cfg.get("alert_region_2", {}).get("x", 0)),
                int(cfg.get("alert_region_2", {}).get("y", 0)),
                int(cfg.get("faction_region_1", {}).get("x", 0)),
                int(cfg.get("faction_region_1", {}).get("y", 0)),
                int(cfg.get("faction_region_2", {}).get("x", 0)),
                int(cfg.get("faction_region_2", {}).get("y", 0)),
            ))

        current = [c.region_key() for c in self._extra_clients]
        if desired == current:
            # Region/identity unchanged -- still refresh `enabled` and
            # `character` in place (cheap, no resource churn) in case only
            # those changed.
            for client, cfg in zip(self._extra_clients, client_configs):
                client.enabled = bool(cfg.get("enabled", True))
                client.character = str(cfg.get("character", ""))
            return

        for client in self._extra_clients:
            client.close()

        rebuilt = []
        for (name, x1, y1, x2, y2, x1f, y1f, x2f, y2f), cfg in zip(desired, client_configs):
            rebuilt.append(_ExtraClient(
                name=name,
                character=str(cfg.get("character", "")),
                x1=x1, y1=y1, x2=x2, y2=y2,
                x1_faction=x1f, y1_faction=y1f, x2_faction=x2f, y2_faction=y2f,
                enabled=bool(cfg.get("enabled", True)),
                needle_paths=alert_files,
                needle_faction_paths=faction_files,
            ))
        self._extra_clients = rebuilt

    def set_vision(self) -> None:
        if self.is_running:
            self.alert_vision.debug_mode = not self.alert_vision.debug_mode
            self._ui(self.main.update_alert_button)

    def set_vision_faction(self) -> None:
        if self.is_running:
            self.alert_vision_faction.debug_mode_faction = (
                not self.alert_vision_faction.debug_mode_faction
            )
            self._ui(self.main.update_faction_button)

    # ------------------------------------------------------------------
    # Async detection tasks (run on the alert daemon thread's event loop)
    # ------------------------------------------------------------------

    async def vision_check(self) -> None:
        """Validate that screenshot capture works for the configured alert region."""
        self.load_settings()
        # Detect degenerate region before attempting capture
        if self.x1 == self.x2 or self.y1 == self.y2:
            self._ui(self.main.write_message,
                     "Wrong Alert Settings — alert region has zero width or height.", "red")
            self.check = False
            return
        screenshot, _ = self.wincap.get_screenshot_value(
            self.y1, self.x1, self.x2, self.y2
        )
        if screenshot is not None:
            self.check = True
        else:
            self._ui(self.main.write_message,
                     "Screenshot capture failed — check capture permissions or region coords.", "red")
            self.check = False

    async def vision_thread(self) -> None:
        """Continuously check for enemy detection in the alert region."""
        while True:
            screenshot, _ = self.wincap.get_screenshot_value(
                self.y1, self.x1, self.x2, self.y2
            )
            if screenshot is not None:
                enemy = self.alert_vision.find(
                    screenshot, self.detection, self.image_thresholds,
                    self._detection_downscale,
                )
                # Retain match centers for per-enemy dedup (#100). Set both
                # together (no await between) so run() sees a consistent pair.
                self._enemy_points = list(enemy)
                self.enemy = bool(enemy)
            else:
                self._enemy_points = []
                self.enemy = False
                self._ui(self.main.write_message,
                         "Screenshot capture failed — check capture permissions or region coords.",
                         "red")
                self.clean_up()
            await asyncio.sleep(VISION_SLEEP_INTERVAL)

    async def vision_faction_thread(self) -> None:
        """Continuously check for faction detection in the faction region."""
        while True:
            screenshot_faction, _ = self.wincap.get_screenshot_value(
                self.y1_faction, self.x1_faction, self.x2_faction, self.y2_faction
            )
            if screenshot_faction is not None:
                faction = self.alert_vision_faction.find_faction(
                    screenshot_faction, self.detection_faction, self.image_thresholds,
                    self._detection_downscale,
                )
                self.faction = bool(faction)
            else:
                # Reset flag on capture failure so stale True doesn't loop alarms
                self.faction = False
            await asyncio.sleep(VISION_SLEEP_INTERVAL)

    async def _extra_client_vision_thread(self, client: "_ExtraClient") -> None:
        """#174: per-extra-client analog of vision_thread(). Reuses the
        SAME global detection sensitivity/per-image-threshold/downscale
        settings as the primary client -- the MVP doesn't support
        per-client thresholds. Unlike the primary client's vision_thread(),
        a capture failure here does NOT stop the whole engine (that would
        mean one multiboxed window closing kills monitoring for every
        other client) -- it just logs and retries next cycle.
        """
        while True:
            screenshot, _ = client.wincap.get_screenshot_value(
                client.y1, client.x1, client.x2, client.y2
            )
            if screenshot is not None:
                enemy = client.vision.find(
                    screenshot, self.detection, self.image_thresholds,
                    self._detection_downscale,
                )
                client.enemy_points = list(enemy)
                client.enemy = bool(enemy)
            else:
                client.enemy_points = []
                client.enemy = False
                logger.debug("Capture failed for extra client %r", client.name)
            await asyncio.sleep(VISION_SLEEP_INTERVAL)

    async def _extra_client_vision_faction_thread(self, client: "_ExtraClient") -> None:
        """#174: per-extra-client analog of vision_faction_thread()."""
        while True:
            screenshot_faction, _ = client.wincap.get_screenshot_value(
                client.y1_faction, client.x1_faction, client.x2_faction, client.y2_faction
            )
            if screenshot_faction is not None:
                faction = client.vision_faction.find_faction(
                    screenshot_faction, self.detection_faction, self.image_thresholds,
                    self._detection_downscale,
                )
                client.faction = bool(faction)
            else:
                client.faction = False
                logger.debug("Faction capture failed for extra client %r", client.name)
            await asyncio.sleep(VISION_SLEEP_INTERVAL)

    async def reset_alarm(self, alarm_type: str) -> None:
        """Reset alarm counters and webhook state when detection clears."""
        if alarm_type in self.alarm_trigger_counts:
            self.alarm_trigger_counts[alarm_type] = 0
            self.cooldown_timers[alarm_type] = 0
        if alarm_type == "Enemy":
            self._local_hostile_count = 0  # v3.5: reset escalation counter
            self._seen_enemies = {}  # #100: enemy left — allow re-alert on return
            self._wh_drop_seen_names.clear()  # #106: reset WH-drop counting
            if self._wh_drop_detector is not None:
                self._wh_drop_detector.reset()
            # #213: drop cached OCR identities too — the next engagement
            # should resolve fresh rather than reuse stale name mappings.
            self._last_identity_keys = frozenset()
            self._last_identity_resolve_time = 0.0
            self._last_enemy_identities = {}
            self._last_ocr_log_message = ""

        if self._webhook and alarm_type == "Enemy" and self.webhook_sent:
            try:
                reset_msg = (
                    f"Alarm cleared in {self._settings_store.get('server.system', '')}"
                )
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._webhook.execute, reset_msg)
            except Exception as e:
                logger.error("Error sending reset webhook: %s", e)
            self.webhook_sent = False

    @staticmethod
    def _quantize_point(point, grid: int = 20) -> tuple:
        """Snap an (x, y) match center to a coarse grid so sub-pixel jitter
        between frames maps to the same enemy identity (#100)."""
        x, y = point
        return (int(x) // grid, int(y) // grid)

    def _should_alarm_enemy(self, enemy_identities: dict | None = None) -> bool:
        """Return True only when a genuinely new enemy has appeared, or a
        previously-alarmed enemy has become due for a sustained-presence
        re-arm. Prevents the alarm (stats/sound/webhook/plugins/push) from
        re-firing on every poll while the same enemy stays on screen (#100).

        A still-present enemy does NOT re-alarm just because
        cooldown_timer_enemy seconds have elapsed since their last alarm —
        an earlier version did exactly that, which meant a pilot who simply
        sat in system re-triggered the full alarm pipeline (log/ESI/sound/
        webhook) once per cooldown window, indefinitely, for as long as
        they stayed. An identity that has already alarmed only fires again
        when either (a) it drops out of _seen_enemies because the pilot
        actually left (see reset_alarm, #100), or (b) rearm_minutes > 0 and
        they've been continuously present for that long (#144) — an
        explicit opt-in for periodic reminders on a sustained threat, off
        by default. cooldown_timer_enemy still governs the separate,
        alarm-type-level sound-spam throttle in play_sound().

        #213: dedup identity is the OCR-resolved pilot NAME when available
        (enemy_identities, from _resolve_enemy_identities — quantized
        position -> name), falling back to the icon's quantized screen
        position for any icon OCR couldn't identify. Keying by name (not
        just position) prevents a Local-roster re-sort from making a
        still-present pilot look like a "brand-new enemy" and re-firing the
        alarm; it also prevents a genuinely different pilot from being
        silently suppressed just because they landed on the screen position
        an earlier, now-departed pilot used to occupy.
        """
        now = time.time()
        enemy_identities = enemy_identities or {}
        keys = {
            enemy_identities.get(self._quantize_point(p), self._quantize_point(p))
            for p in (self._enemy_points or [])
        }
        if not keys:
            keys = {(-1, -1)}

        trigger = False
        for key in keys:
            sighting = self._seen_enemies.get(key)
            if sighting is None:
                # Brand-new enemy
                trigger = True
            elif sighting.rearm_at > 0 and now >= sighting.rearm_at:
                # Sustained-presence re-arm (#144) — opt-in only
                trigger = True

        # Prune to only currently-visible enemies; update or create records.
        rearm_delta = self._rearm_minutes * 60
        new_seen: dict = {}
        for key in keys:
            old = self._seen_enemies.get(key)
            if old is None:
                rearm_at = (now + rearm_delta) if rearm_delta > 0 else 0
                new_seen[key] = _EnemySighting(
                    first_seen=now, last_alarm=now if trigger else 0, rearm_at=rearm_at
                )
            else:
                # Advance rearm_at if it fired
                new_rearm_at = old.rearm_at
                if new_rearm_at > 0 and now >= new_rearm_at:
                    new_rearm_at = now + rearm_delta if rearm_delta > 0 else 0
                new_seen[key] = _EnemySighting(
                    first_seen=old.first_seen,
                    last_alarm=now if trigger else old.last_alarm,
                    rearm_at=new_rearm_at,
                )
        self._seen_enemies = new_seen
        return trigger

    def _should_alarm_extra_client_enemy(self, client: "_ExtraClient") -> bool:
        """#174: simplified per-extra-client analog of _should_alarm_enemy()
        -- position-quantized dedup only (no OCR identity resolution;
        that stays primary-client/global-only in this MVP, so extra
        clients dedup by icon position exactly like the primary client
        did before #213 added name-based identity)."""
        now = time.time()
        keys = {self._quantize_point(p) for p in (client.enemy_points or [])}
        if not keys:
            keys = {(-1, -1)}

        rearm_delta = self._rearm_minutes * 60
        trigger = False
        for key in keys:
            sighting = client.seen_enemies.get(key)
            if sighting is None:
                trigger = True
            elif sighting.rearm_at > 0 and now >= sighting.rearm_at:
                trigger = True

        new_seen: dict = {}
        for key in keys:
            old = client.seen_enemies.get(key)
            if old is None:
                rearm_at = (now + rearm_delta) if rearm_delta > 0 else 0
                new_seen[key] = _EnemySighting(
                    first_seen=now, last_alarm=now if trigger else 0, rearm_at=rearm_at
                )
            else:
                new_rearm_at = old.rearm_at
                if new_rearm_at > 0 and now >= new_rearm_at:
                    new_rearm_at = now + rearm_delta if rearm_delta > 0 else 0
                new_seen[key] = _EnemySighting(
                    first_seen=old.first_seen,
                    last_alarm=now if trigger else old.last_alarm,
                    rearm_at=new_rearm_at,
                )
        client.seen_enemies = new_seen
        return trigger

    def _reset_extra_client_alarm(self, client: "_ExtraClient", alarm_type: str) -> None:
        """#174: lightweight per-extra-client analog of reset_alarm() --
        only touches this client's own cooldown/trigger-count/dedup
        state, never the primary client's or global subsystems (WH-drop,
        OCR identity cache, escalation counter) that don't make sense
        per-client in this MVP."""
        key = (client.name, alarm_type)
        if key in self.alarm_trigger_counts:
            self.alarm_trigger_counts[key] = 0
            self.cooldown_timers[key] = 0
        if alarm_type == "Enemy":
            client.seen_enemies = {}

    async def alarm_detection(
        self,
        alarm_text: str,
        sound: str = ALARM_SOUND,
        alarm_type: str = "Enemy",
        client_name: str | None = None,
    ) -> None:
        """Trigger an alarm: log message, statistics, sound, webhook.

        *client_name* (#174): when set (an extra multi-client entry),
        alarm_text is prefixed (`[ClientName] ...`) and sound cooldowns
        are tracked independently (see play_sound()). Intel/threat-score/
        Discord-webhook-template/push/screenshot/escalation/zKillboard
        stay primary-client/global-only in this MVP -- system-level
        context (current system, ESI augmentation) isn't meaningfully
        per-client yet, matching this issue's own "threat score/intel
        remain global" design note.
        """
        if client_name:
            alarm_text = f"[{client_name}] {alarm_text}"
        self._ui(self.main.write_message, alarm_text, "red")
        self.statistics.add_alarm(alarm_type)
        save_lifetime_stats(self.statistics)
        # TTS (#139) — speak alarm text aloud if enabled
        if self._tts_enabled:
            try:
                from evealert.tools.tts import speak  # noqa: PLC0415
                speak(alarm_text, self._tts_rate)
            except Exception:
                pass
        # Automation bridge (#153) — POST JSON to configured webhook
        if self._automation_enabled and self._automation_webhook_url:
            self.loop.create_task(
                self._post_automation_webhook(alarm_text, alarm_type)
            )
        await self.play_sound(sound, alarm_type, client_name)

        if client_name is not None:
            return  # #174: everything below is primary-client/global-only

        await self.send_webhook_message(alarm_type)

        # v3.5: push notifications (Telegram / Pushover / ntfy)
        if self._push_config:
            try:
                from evealert.tools.push_notifier import (  # pylint: disable=import-outside-toplevel
                    get_push_notifier,
                )

                notifier = get_push_notifier(**self._push_config)
                if notifier.is_configured():
                    system = self._settings_store.get("server.system", "")
                    msg = f"{alarm_type} alarm in {system}" if system else alarm_type
                    asyncio.ensure_future(notifier.send(msg))
            except Exception as exc:
                logger.debug("Push notification dispatch failed: %s", exc)

        # v3.5: auto-screenshot on alarm
        if self._auto_screenshot:
            try:
                self._capture_alarm_screenshot()
            except Exception as exc:
                logger.debug("Alarm screenshot failed: %s", exc)

        # v3.5: escalation check
        if self._escalation_threshold > 0:
            self._local_hostile_count += 1
            if self._local_hostile_count >= self._escalation_threshold:
                self._ui(
                    self.main.write_message,
                    f"ESCALATION: {self._local_hostile_count} hostile(s) detected — elevated threat",
                    "red",
                )

        # Notify plugins
        try:
            from evealert.tools.plugin_loader import (  # pylint: disable=import-outside-toplevel
                get_plugin_manager,
            )

            system = self._settings_store.get("server.system", "")
            ts = time.strftime("%H:%M:%S")
            hook = "on_enemy" if alarm_type == "Enemy" else "on_faction"
            get_plugin_manager().call(hook, system=system, timestamp=ts)
        except Exception as exc:
            logger.debug("Plugin on_enemy/on_faction hook failed: %s", exc)

        # Zkillboard lookup — Enemy alarms only, subject to cooldown
        if alarm_type == "Enemy" and self._zkillboard_enabled:
            now = time.time()
            if now >= self._zkillboard_next_lookup:
                self._zkillboard_next_lookup = now + self._zkillboard_cooldown
                system_name = self._settings_store.get("server.system", "").strip()
                if system_name:
                    asyncio.ensure_future(self._fetch_and_report_kills(system_name))

        # ESI augmentation — show corp/alliance of recent Local joiners.
        # Pass any OCR names captured at alarm time directly so _augment_with_esi
        # doesn't need a second screen capture that may run too late.
        if alarm_type == "Enemy" and (self._esi_enabled or self._ocr_enabled):
            asyncio.ensure_future(self._augment_with_esi(
                hint_names=list(self._last_ocr_names)
            ))

    async def _send_typed_webhook(self, alarm_type: str, msg: str) -> None:
        """Fire the per-type webhook URL if configured and min-count threshold is met."""
        if alarm_type == "Enemy":
            url = self._webhook_enemy_url
            min_count = self._webhook_enemy_min
            count = self.statistics.session_by_type.get("Enemy", 0)
        elif alarm_type == "Faction":
            url = self._webhook_faction_url
            min_count = self._webhook_faction_min
            count = self.statistics.session_by_type.get("Faction", 0)
        else:
            return

        if not url:
            return
        if count < min_count:
            logger.debug(
                "Skipping typed webhook: count %d < min_count %d", count, min_count
            )
            return

        try:
            from dhooks_lite import (
                Webhook as _Webhook,  # pylint: disable=import-outside-toplevel
            )

            hook = _Webhook(url, username="EVE Alert")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, hook.execute, msg)
        except Exception as e:
            logger.error("Error sending %s webhook: %s", alarm_type, e)

    def _on_intel_line(self, line: str, channel: str | None = None) -> None:
        """Called from IntelWatcher for each new chat-log line.

        Posts the line to the GUI log on the main Tkinter thread.
        Only lines that look like player chat (not system messages) are forwarded.

        *channel* (#171): which watched channel this line came from, when
        more than one is configured -- tagged onto the log line so a
        multi-channel setup stays legible.
        """
        # EVE chat log system messages start with "  " (two spaces) or specific tokens
        stripped = line.strip()
        # Skip empty lines and EVE session-header lines (start with "--")
        if not stripped or stripped.startswith("-------"):
            return
        channel_tag = f"[{channel}] " if channel else ""
        self._ui(self.main.write_message, f"Intel: {channel_tag}{stripped}", "cyan")
        # Notify plugins
        try:
            from evealert.tools.plugin_loader import (  # pylint: disable=import-outside-toplevel
                get_plugin_manager,
            )

            get_plugin_manager().call("on_intel", line=stripped)
        except Exception as exc:
            logger.debug("Plugin on_intel hook failed: %s", exc)

    def _on_intel_report(self, report) -> None:
        """Called from IntelWatcher with a parsed IntelReport (#142).

        Logs a structured summary line with dotlan and zkillboard links,
        buffers the report for Enemy-alarm correlation (#212), and
        schedules an async jump-distance lookup if the user's home system is
        configured.
        """
        try:
            self._intel_reports_recent.append((time.time(), report))
            # #171: tag the channel this report came from when more than
            # one is configured, e.g. "Intel[NC-INT]: 3 hostile(s) in ...".
            channel_tag = f"[{report.channel}]" if getattr(report, "channel", None) else ""
            if report.is_clear:
                # Dotlan link for the reported system, embedded on the
                # system name (#210) rather than a separate visible URL.
                system_display = report.system or ""
                if report.system:
                    system_display = make_link(
                        report.system,
                        f"https://dotlan.net/system/{report.system.replace(' ', '_')}",
                    )
                system_str = f"{system_display} " if report.system else ""
                self._ui(
                    self.main.write_message,
                    f"Intel{channel_tag}: {system_str}CLEAR",
                    "green",
                )
            else:
                count_str = f"{report.hostile_count}" if report.hostile_count else "?"
                ship_str = f" [{', '.join(report.ships[:3])}]" if report.ships else ""

                # Dotlan link for the reported system, embedded on the name.
                system_display = report.system or "unknown system"
                if report.system:
                    system_display = make_link(
                        report.system,
                        f"https://dotlan.net/system/{report.system.replace(' ', '_')}",
                    )
                # zkillboard search link for the reporting pilot, embedded
                # on their name (#210) rather than a separate visible URL.
                reporter_str = ""
                if report.pilot:
                    pilot_enc = report.pilot.replace(" ", "+")
                    reporter_link = make_link(
                        report.pilot, f"https://zkillboard.com/search/#{pilot_enc}"
                    )
                    reporter_str = f" | reporter: {reporter_link}"

                self._ui(
                    self.main.write_message,
                    f"Intel{channel_tag}: {count_str} hostile(s) in {system_display}{ship_str}{reporter_str}",
                    "red",
                )
                # zkillboard links for each mentioned hostile pilot (#197),
                # embedded on their name (#210) rather than a separate URL.
                for hostile in report.mentioned_pilots:
                    hostile_enc = hostile.replace(" ", "+")
                    hostile_link = make_link(
                        hostile, f"https://zkillboard.com/search/#{hostile_enc}"
                    )
                    self._ui(
                        self.main.write_message,
                        f"  hostile: {hostile_link}",
                        "orange",
                    )
                # Persistent pilot-sighting history (#215, v7.0): one
                # sighting per mentioned hostile pilot. The reporting pilot
                # themselves is deliberately excluded -- they're your own
                # intel channel's population (often allies), not a hostile
                # sighting; only who they *mention* counts.
                if self._pilot_history_enabled:
                    try:
                        from evealert.tools.pilot_history_store import (  # pylint: disable=import-outside-toplevel
                            record_sighting,
                        )

                        report_ship = report.ships[0] if report.ships else None
                        for hostile in report.mentioned_pilots:
                            record_sighting(
                                hostile,
                                source="intel",
                                system=report.system,
                                ship=report_ship,
                            )
                    except Exception as exc:
                        logger.debug("Pilot history record (intel) failed: %s", exc)
                # Queue a jump-distance lookup if we have a home system
                home = self._settings_store.get("server.system", "").strip()
                if home and home != "Enter a System Name" and report.system:
                    self.loop.create_task(
                        self._lookup_jump_distance(home, report.system, report.mentioned_pilots)
                    )
        except Exception as exc:
            logger.debug("_on_intel_report failed: %s", exc)

    def _is_duplicate_intel_line(self, line: str) -> bool:
        """#171: cross-channel dedup shared by every IntelWatcher instance
        (passed in as each watcher's is_duplicate callback) -- the same
        paste often hits multiple watched channels within seconds of
        itself, and only the first should fire callbacks.

        Normalizes on (reporting pilot, message body), stripping the raw
        EVE chat-log timestamp -- which differs even for a copy-pasted
        duplicate posted a moment later in another channel.
        """
        normalized = _normalize_intel_line(line)
        now = time.time()
        last_seen = self._recent_intel_lines.get(normalized)
        is_dup = (
            last_seen is not None
            and (now - last_seen) < self._INTEL_DEDUP_WINDOW_SECONDS
        )
        self._recent_intel_lines[normalized] = now
        # Opportunistic prune so this dict doesn't grow unbounded over a
        # long session -- only entries outside the dedup window are dropped.
        if len(self._recent_intel_lines) > 200:
            cutoff = now - self._INTEL_DEDUP_WINDOW_SECONDS
            self._recent_intel_lines = {
                k: v for k, v in self._recent_intel_lines.items() if v >= cutoff
            }
        return is_dup

    def _find_recent_intel_report(self, pilot_name: str):
        """#212: return (IntelReport, age_seconds) for the most recent
        buffered intel report that mentions *pilot_name* -- a case-
        insensitive exact match against either the report's
        mentioned_pilots (reuses intel_parser._find_mentioned_pilots'
        heuristics, not reinvented here) or the reporting pilot themselves
        -- within _INTEL_CORRELATION_WINDOW_SECONDS. Returns None if
        nothing matches.

        The buffer is appended in chronological order, so the newest
        report is checked first and iteration stops at the first entry
        older than the correlation window (everything before it is only
        older still).
        """
        name_lower = pilot_name.lower()
        now = time.time()
        for received_at, report in reversed(self._intel_reports_recent):
            age = now - received_at
            if age > self._INTEL_CORRELATION_WINDOW_SECONDS:
                break
            mentioned_lower = {p.lower() for p in report.mentioned_pilots}
            if name_lower in mentioned_lower or (
                report.pilot and report.pilot.lower() == name_lower
            ):
                return report, age
        return None

    async def _lookup_jump_distance(
        self,
        origin: str,
        destination: str,
        mentioned_pilots: list[str] | None = None,
    ) -> None:
        """Compute jump distance between two systems via ESI route and log it.

        If intel threat-radius check is enabled (#198) and the destination is
        within *intel_threat_radius* jumps, trigger _augment_with_esi on the
        mentioned hostile pilot names.
        """
        try:
            from evealert.tools.universe import get_universe_cache  # noqa: PLC0415

            cache = get_universe_cache()
            origin_id = await cache.get_system_id(origin)
            dest_id = await cache.get_system_id(destination)
            if origin_id and dest_id:
                route = await cache.get_route(origin_id, dest_id)
                if route is not None:
                    jumps = len(route) - 1
                    label = f"{jumps} jump{'s' if jumps != 1 else ''}"
                    self._ui(
                        self.main.write_message,
                        f"Intel: {destination} is {label} from {origin}",
                        "cyan",
                    )
                    # Gate ESI/KOS check by threat radius (#198)
                    if (
                        self._intel_threat_check_enabled
                        and mentioned_pilots
                        and jumps <= self._intel_threat_radius
                    ):
                        logger.debug(
                            "Intel threat radius: %s is %d jump(s) away, running ESI check on %s",
                            destination, jumps, mentioned_pilots,
                        )
                        self.loop.create_task(self._augment_with_esi(mentioned_pilots))
        except Exception as exc:
            logger.debug("Jump distance lookup failed: %s", exc)

    async def run_intel_check(self, names: list[str]) -> None:
        """Public entry point to run the ESI/KOS/zKillboard intel pipeline
        on an explicit pilot-name list, outside the normal alarm flow (#201).

        Used by the Settings dialog's "Test OCR on Region" button so
        confirming OCR works also gives the user a real intel check —
        the exact same pipeline a live Enemy alarm uses. Safe to call
        whether or not the detection engine is currently running.
        """
        await self._augment_with_esi(hint_names=names)

    async def _augment_with_esi(self, hint_names: list[str] | None = None) -> None:
        """Background task: enriched ESI + Zkillboard pilot intel on Enemy alarm.

        *hint_names* — pilot names already captured by OCR at alarm time.
        When provided they are used directly, avoiding a second screen capture
        that may run too late (async delay) to see the same list.

        Posts per-pilot lines to the log pane covering:
          - Corp/alliance (v3.0)
          - Character age, corp history count (v3.1 #69)
          - Zkillboard kill profile (v3.1 #70)
          - Threat tier match (v3.1 #71)
          - Flashy security status alert (v3.1 #72)
        """
        try:
            from evealert.tools.esi_standings import (  # pylint: disable=import-outside-toplevel
                extract_joining_characters,
                get_esi_client,
            )
            from evealert.tools.intel_watcher import (  # pylint: disable=import-outside-toplevel
                find_intel_log,
                get_eve_chatlog_dir,
            )

            # If OCR names were captured at alarm time, use them directly.
            # This avoids a second screen-capture that runs later (async) when
            # the Local list may have scrolled or the window may have changed.
            if hint_names:
                names = list(hint_names)
                self._ui(
                    self.main.write_message,
                    f"Intel [ESI]: using {len(names)} name(s) from alarm OCR: {', '.join(names)}",
                    "cyan",
                )
            else:
                chatlog_dir = get_eve_chatlog_dir()
                self._ui(
                    self.main.write_message,
                    f"Intel [ESI]: chatlog dir = {chatlog_dir or 'NOT FOUND'}",
                    "cyan",
                )

                # Gather Local names from the chat log (best-effort — may be absent).
                names = []
                local_log = find_intel_log(chatlog_dir, "Local") if chatlog_dir else None
                self._ui(
                    self.main.write_message,
                    f"Intel [ESI]: Local log = {local_log or 'not found (chatlog dir missing or no Local file)'}",
                    "cyan",
                )
                if local_log is not None:
                    try:
                        # EVE chat logs are UTF-16 LE; detect via BOM
                        with open(local_log, "rb") as fh:
                            raw = fh.read()
                        enc = "utf-16-le" if raw.startswith(b"\xff\xfe") else "utf-8"
                        text = raw[2:].decode(enc, errors="replace") if enc == "utf-16-le" else raw.decode(enc, errors="replace")
                        lines = text.splitlines()[-50:]
                        # NOTE (#202): this only catches pilots who JOINED Local
                        # within the last 50 lines — it correctly covers the
                        # common "hostile just jumped/warped in and triggered
                        # this alarm" case, but it structurally cannot find a
                        # hostile who was already present before the alarm
                        # fired (no "joined" line exists for them). There is
                        # no ESI endpoint that lists a system's current
                        # population, so OCR is the only way to cover that
                        # case — see the message below when this comes up empty.
                        names = extract_joining_characters(lines)
                        self._ui(
                            self.main.write_message,
                            f"Intel [ESI]: Local log encoding={enc}, last-50 lines={len(lines)}, "
                            f"joining characters found: {names or 'none'}",
                            "cyan",
                        )
                    except OSError as exc:
                        self._ui(
                            self.main.write_message,
                            f"Intel [ESI]: could not read Local log — {exc}",
                            "yellow",
                        )
                        names = []
                # NOTE (#202): a second OCR capture used to run here when
                # _ocr_enabled was True. It always targeted the exact same
                # region _build_enemy_alarm_text had just captured moments
                # earlier at alarm time (or, when this function is invoked
                # from the intel-channel jump-radius check, an unrelated
                # remote system that OCR — reading only the player's own
                # screen — could never usefully capture). Retrying it here
                # was redundant, blocked this async task for the duration of
                # a full OCR pass, and never had a real chance of succeeding
                # where the first attempt just failed. Removed.
            # end else (no hint_names)

            if not names:
                if self._ocr_enabled:
                    msg = (
                        "Intel [ESI]: OCR found no names at alarm time and no recent "
                        "Local joins — the hostile may already have been in-system "
                        "before this alarm fired. Check the OCR region in Settings."
                    )
                else:
                    msg = (
                        "Intel [ESI]: no recent Local joins found. ESI Augmentation "
                        "without OCR can only detect pilots who just joined Local — "
                        "enable 'Read pilot names from Local on alarm' in Settings for "
                        "coverage of pilots already present in system."
                    )
                self._ui(self.main.write_message, msg, "yellow")
                return

            # v3.6: WH drop heuristic — count each NEW pilot once (names that
            # were already counted since the last reset are skipped so the
            # warning doesn't re-fire on every subsequent alarm, #106).
            if self._wh_drop_detector and self._wh_drop_enabled:
                new_names = [n for n in names if n not in self._wh_drop_seen_names]
                for name in new_names:
                    self._wh_drop_seen_names.add(name)
                    if self._wh_drop_detector.record_join():
                        self._ui(
                            self.main.write_message,
                            f"WH DROP WARNING: {len(new_names)} new pilot(s) joined "
                            "Local rapidly — possible fleet drop",
                            "red",
                        )
                        self._wh_drop_detector.reset()
                        break

            client = get_esi_client()
            self._ui(
                self.main.write_message,
                f"Intel [ESI]: querying ESI for {len(names[:5])} pilot(s): {', '.join(names[:5])}",
                "cyan",
            )
            results = await client.lookup_many(names[:5])
            # #203: KOS checks (CVA / custom APIs) accept a bare pilot name and
            # do not need ESI data — they must still run even when ESI fails
            # (network issue, 5xx, a name ESI's search can't resolve). Do NOT
            # return early here; build a name→CharacterInfo lookup instead so
            # the per-pilot loop below can enrich with ESI data WHEN available
            # and fall back to KOS-only (name-only) checks when it isn't.
            results_by_name = {r.name.lower(): r for r in results}
            if not results:
                self._ui(
                    self.main.write_message,
                    "Intel [ESI]: ESI lookup returned no results (network issue or "
                    "unknown names) — running KOS check on name(s) directly",
                    "yellow",
                )

            # v3.7: fleet composition analysis (3+ hostiles; needs resolved IDs)
            if self._fleet_composition_enabled and len(results) >= 3:
                try:
                    from evealert.tools.fleet_context import (  # pylint: disable=import-outside-toplevel
                        analyze_fleet_composition,
                    )

                    char_ids = [info.character_id for info in results]
                    composition = await analyze_fleet_composition(char_ids)
                    if composition:
                        self._ui(
                            self.main.write_message,
                            f"Fleet analysis: {composition.threat_summary}",
                            "red",
                        )
                except Exception as exc:
                    logger.debug("Fleet composition analysis failed: %s", exc)

            self._ui(self.main.write_message, "ESI — Local pilot intel:", "cyan")

            _any_kos = False
            _kos_tier_label = ""
            _max_danger_ratio = 0.0
            # #218: aggregated across pilots, same pattern as _max_danger_ratio
            _max_history_frequency = 0
            _any_history_regular_route = False

            for name in names[:5]:
                info = results_by_name.get(name.lower())
                display_name = info.name if info is not None else name
                corp_name = (info.corporation_name or "") if info is not None else ""
                alliance_name = (info.alliance_name or "") if info is not None else ""

                # Fetch the zKillboard profile FIRST (#208) so its result can
                # gate whether the character link is even shown: a pilot
                # zkillboard has never indexed (no killmail history) has no
                # character page and the link 404s. _fetch_zkb_profile
                # returns None for that case (not a zero-stat profile) — see
                # its docstring. Fetched once, reused below for the stats
                # line so this doesn't cost a second network round-trip.
                zkb = None
                if info is not None:
                    try:
                        zkb = await client.get_zkillboard_profile(info.character_id)
                    except Exception as exc:
                        logger.debug("Zkillboard profile augmentation failed: %s", exc)

                # ── Threat tier check ────────────────────────────────
                tier = None
                for substr, t in self._threat_tiers.items():
                    if (
                        substr.lower() in display_name.lower()
                        or substr.lower() in corp_name.lower()
                        or substr.lower() in alliance_name.lower()
                    ):
                        tier = t
                        break

                # #173: a manual "blue" tier is a deliberate ally tag, kept
                # in the same threat_tiers dict as red/orange/yellow. It's
                # honored identically to an ESI-standings score >= +5.0
                # (#147) -- same toggle, same [ALLY]-and-skip behavior --
                # so KOS/threat counting for this pilot is suppressed
                # regardless of which mechanism identified them as blue.
                if tier == "blue" and self._standings_filter_blues:
                    self._ui(
                        self.main.write_message,
                        f"    [ALLY] {display_name} — manual blue tier (filtered)",
                        "green",
                    )
                    continue

                # ── Build header line ────────────────────────────────
                tier_prefix = {
                    "red": "⚠ [KOS-RED]",
                    "orange": "⚠ [HOSTILE]",
                    "yellow": "[CAUTION]",
                }.get(tier or "", "")

                # zkillboard character link (#205) is embedded directly on
                # the pilot's name (#210) rather than shown as a separate
                # visible URL — only when zkillboard actually has this
                # pilot on record (#208); otherwise the page 404s (common
                # for very young / PvE-only pilots).
                name_display = display_name
                if info is not None and zkb is not None:
                    name_display = make_link(
                        display_name,
                        f"https://zkillboard.com/character/{info.character_id}/",
                    )

                parts = [f"  {tier_prefix} {name_display}".strip()]
                if info is not None:
                    if self._esi_show_corp and corp_name:
                        parts.append(f"[{corp_name}]")
                    if self._esi_show_alliance and alliance_name:
                        parts.append(f"<{alliance_name}>")
                    # age and corp history
                    if info.age_days >= 0:
                        age_str = f"{info.age_days}d old"
                        corps_str = f"{info.corp_history_count} corp(s)"
                        parts.append(f"— {age_str}, {corps_str}")
                else:
                    # #203: ESI didn't resolve this name — KOS still checked below.
                    parts.append("— ESI lookup unavailable")

                line_colour = (
                    "red"
                    if tier == "red"
                    else "yellow" if tier in ("orange", "yellow") else "cyan"
                )
                self._ui(self.main.write_message, " ".join(parts), line_colour)

                if info is not None:
                    # ── Flashy security status ──────────────────────────
                    if self._esi_alert_flashy and info.security_status <= -5.0:
                        self._ui(
                            self.main.write_message,
                            f"    ⚠ FLASHY: {display_name} (sec: {info.security_status:.1f}) — attackable in low-sec",
                            "red",
                        )

                    # ── Cyno-alt heuristic ───────────────────────────
                    if info.age_days < 30:
                        self._ui(
                            self.main.write_message,
                            f"    ⚠ YOUNG PILOT: {display_name} ({info.age_days}d old) — possible cyno/scout alt",
                            "yellow",
                        )

                    # ── Zkillboard kill profile line (already fetched above) ─
                    try:
                        if zkb and (zkb.kills_total > 0 or zkb.losses_total > 0):
                            if zkb.danger_ratio > _max_danger_ratio:
                                _max_danger_ratio = zkb.danger_ratio
                            danger_pct = int(zkb.danger_ratio * 100)
                            ship_str = f" | flies {zkb.top_ship}" if zkb.top_ship else ""
                            self._ui(
                                self.main.write_message,
                                f"    ZKB: {zkb.kills_total}K/{zkb.losses_total}L (all-time) "
                                f"[{danger_pct}% danger]{ship_str}",
                                "cyan",
                            )
                            # Ship cross-reference (#150): match top_ship against D-scan types
                            if zkb.top_ship and self._dscan_watcher:
                                visible = self._dscan_watcher.current_visible_types
                                if any(zkb.top_ship.lower() in v.lower() for v in visible):
                                    self._ui(
                                        self.main.write_message,
                                        f"    ⚠ MATCH: {display_name} typically flies {zkb.top_ship}"
                                        " — that type is on D-scan NOW",
                                        "red",
                                    )
                    except Exception as exc:
                        logger.debug("Zkillboard stats line rendering failed: %s", exc)

                # ── Intel-channel correlation (#212) — runs regardless of
                # ESI resolution: intel matches on the raw name, not an ESI
                # character ID, so an unresolved name can still correlate. ─
                if self._correlate_intel_enabled:
                    try:
                        from evealert.tools.intel_parser import (  # pylint: disable=import-outside-toplevel
                            _strip_header as _strip_intel_header,
                        )

                        match = self._find_recent_intel_report(display_name)
                        if match is not None:
                            intel_report, age_seconds = match
                            parsed = _strip_intel_header(intel_report.raw_line)
                            message = parsed[1] if parsed else intel_report.raw_line
                            detail = f'"{message}"'
                            if intel_report.system:
                                detail += f" in {intel_report.system}"
                            self._ui(
                                self.main.write_message,
                                f"    Intel ({_format_intel_age(age_seconds)} ago, "
                                f"reported by {intel_report.pilot}): {detail}",
                                "cyan",
                            )
                    except Exception as exc:
                        logger.debug("Intel correlation failed: %s", exc)

                # ── Persistent pilot-sighting history (#215, v7.0) — runs
                # regardless of ESI resolution: the pilot's name and current
                # system are known even when ESI can't resolve them. ─
                if self._pilot_history_enabled:
                    try:
                        from evealert.tools.pilot_history_store import (  # pylint: disable=import-outside-toplevel
                            record_sighting,
                        )

                        current_system = self._settings_store.get(
                            "server.system", ""
                        ).strip()
                        if current_system == "Enter a System Name":
                            current_system = ""
                        record_sighting(
                            display_name,
                            source="local",
                            system=current_system or None,
                            ship=(zkb.top_ship if zkb else None),
                            corp=corp_name or None,
                            alliance=alliance_name or None,
                        )
                    except Exception as exc:
                        logger.debug("Pilot history record (local) failed: %s", exc)

                # ── Pilot sighting-history summary (#216, v7.0) — makes the
                # accumulated history in #214/#215's store useful in the
                # moment, not just data at rest. Runs regardless of ESI
                # resolution, same reasoning as the blocks above. ─
                if self._pilot_history_enabled:
                    try:
                        from evealert.tools.pilot_history_analytics import (  # pylint: disable=import-outside-toplevel
                            format_pathing,
                            format_summary,
                            infer_pathing,
                            summarize,
                        )

                        current_system = self._settings_store.get(
                            "server.system", ""
                        ).strip()
                        if current_system == "Enter a System Name":
                            current_system = ""

                        summary = summarize(display_name)
                        if summary is not None:
                            history_line = f"    History: {format_summary(summary)}"
                            # #217: pathing is an additional segment on the
                            # same line, only when it clears its own
                            # confidence floor (see infer_pathing).
                            pathing = await infer_pathing(display_name)
                            if pathing is not None:
                                history_line += f" — {format_pathing(pathing)}"
                            self._ui(self.main.write_message, history_line, "cyan")

                            # #218: feed the same history data into the
                            # composite threat score -- aggregated across
                            # pilots the same way _max_danger_ratio is.
                            if current_system:
                                pilot_frequency = next(
                                    (
                                        count
                                        for sys, count in summary.top_systems
                                        if sys == current_system
                                    ),
                                    0,
                                )
                                _max_history_frequency = max(
                                    _max_history_frequency, pilot_frequency
                                )
                                if pathing is not None and (
                                    pathing.home_system == current_system
                                    or any(
                                        current_system in pair
                                        for pair, _count in pathing.top_transitions
                                    )
                                ):
                                    _any_history_regular_route = True
                    except Exception as exc:
                        logger.debug("Pilot history summary failed: %s", exc)

                # ── KOS check (v3.4) — runs regardless of ESI resolution (#203) ─
                try:
                    from evealert.tools.kos_checker import (  # pylint: disable=import-outside-toplevel
                        get_kos_checker,
                    )

                    kos_checker = get_kos_checker(
                        cva_enabled=self._kos_cva_enabled,
                        api_urls=self._kos_custom_urls,
                    )
                    kos_result = await kos_checker.check(
                        display_name, corp_name, alliance_name
                    )
                    if kos_result:
                        _any_kos = True
                        _kos_tier_label = kos_result.label
                        self._ui(
                            self.main.write_message,
                            f"    ⚠ KOS ({kos_result.source}): {display_name} — {kos_result.label}",
                            "red",
                        )
                except Exception as exc:
                    logger.debug("KOS check failed: %s", exc)

                # ── ESI standings auto-classify (v4.0; needs resolved IDs) ────
                if info is not None and self._esi_standings_classify and self._esi_standings_cache:
                    # Standings can be set at character, corp, OR alliance level
                    # (#106) — collect all that apply and use the most hostile.
                    candidates = [
                        self._esi_standings_cache.get(info.character_id),
                        self._esi_standings_cache.get(info.corporation_id),
                        self._esi_standings_cache.get(info.alliance_id),
                    ]
                    found = [s for s in candidates if s is not None]
                    standing = min(found) if found else None
                    if standing is not None:
                        if standing <= -5.0:
                            tier_color = "red"
                            tier_label = "terrible standing"
                        elif standing < 0:
                            tier_color = "yellow"
                            tier_label = f"bad standing ({standing:+.1f})"
                        elif standing >= 5.0:
                            tier_color = "green"
                            tier_label = f"excellent standing ({standing:+.1f})"
                            # Blues filter (#147): skip KOS/threat for allies
                            if self._standings_filter_blues:
                                self._ui(
                                    self.main.write_message,
                                    f"    [ALLY] {info.name} — standing {standing:+.1f} (filtered)",
                                    "green",
                                )
                                continue
                        else:
                            tier_color = "cyan"
                            tier_label = f"standing {standing:+.1f}"
                        self._ui(
                            self.main.write_message,
                            f"    Standing: {info.name} — {tier_label}",
                            tier_color,
                        )

        except Exception as exc:
            logger.debug("ESI augmentation error: %s", exc)

        # ── Composite threat score (#141) ─────────────────────────────────
        try:
            from evealert.tools.threat_score import compute_threat_score  # noqa: PLC0415
            from evealert.data.ship_classes import ShipThreatClass  # noqa: PLC0415

            top_class = max(
                self._dscan_last_classes,
                key=lambda c: ShipThreatClass(c).urgency,
                default=ShipThreatClass.UNKNOWN,
            )
            assessment = compute_threat_score(
                local_hostile_count=len(names) if names else 0,
                is_kos=_any_kos,
                kos_tier=_kos_tier_label,
                danger_ratio=_max_danger_ratio,
                dscan_threat_class=top_class.value if top_class != ShipThreatClass.UNKNOWN else "",
                adjacent_kills=self._get_adjacent_kill_count(),
                is_cyno=ShipThreatClass.CYNO in self._dscan_last_classes,
                history_frequency=_max_history_frequency,
                history_is_regular_route=_any_history_regular_route,
            )
            colour = {"CRITICAL": "red", "HIGH": "yellow", "CAUTION": "cyan"}[assessment.label]
            self._ui(self.main.write_message, str(assessment), colour)
        except Exception as exc:
            logger.debug("Threat score computation failed: %s", exc)

    # ------------------------------------------------------------------
    # D-scan callbacks (v3.3) — called from DscanWatcher on the alert thread
    # ------------------------------------------------------------------

    def _capture_alarm_screenshot(self) -> None:
        """Save a screenshot of the alert region to the sessions directory."""
        try:
            import mss  # pylint: disable=import-outside-toplevel

            from evealert.settings.stats_store import (  # pylint: disable=import-outside-toplevel
                get_sessions_dir,
            )

            fname = time.strftime("screenshot_%Y%m%d_%H%M%S.png")
            dest = get_sessions_dir() / fname
            region = {
                "top": self.y1,
                "left": self.x1,
                "width": max(1, self.x2 - self.x1),
                "height": max(1, self.y2 - self.y1),
            }
            with mss.mss() as sct:
                img = sct.grab(region)
                mss.tools.to_png(img.rgb, img.size, output=str(dest))
            logger.info("Alarm screenshot saved: %s", dest)
        except Exception as exc:
            logger.debug("Screenshot failed: %s", exc)

    def _on_dscan_threat(self, tier: str, name: str, threat_class=None) -> None:
        """Called when a RED or ORANGE ship appears on D-scan."""
        from evealert.data.ship_classes import ShipThreatClass  # noqa: PLC0415

        # Store the class so the threat score can use it
        if threat_class and threat_class != ShipThreatClass.UNKNOWN:
            self._dscan_last_classes.add(threat_class)

        # Dedicated CYNO alarm (#146) — fires regardless of tier, bypasses cooldown
        if threat_class == ShipThreatClass.CYNO:
            self.loop.create_task(self._fire_cyno_alarm(name))
            return

        # Build a human-readable label for the class
        class_labels = {
            ShipThreatClass.TACKLE:      "TACKLE — get out NOW",
            ShipThreatClass.DICTOR:      "DICTOR — bubble incoming",
            ShipThreatClass.FORCE_RECON: "FORCE RECON — cloaked threat",
            ShipThreatClass.COVERT_OPS:  "COVERT OPS — scanning",
            ShipThreatClass.COMBAT:      "combat ship",
        }
        class_label = class_labels.get(threat_class, "") if threat_class else ""
        suffix = f" [{class_label}]" if class_label else ""

        if tier == "red" and self._dscan_alert_red:
            self._ui(self.main.write_message, f"D-SCAN RED: {name}{suffix}", "red")
        elif tier == "orange" and self._dscan_alert_orange:
            self._ui(self.main.write_message, f"D-SCAN ORANGE: {name}{suffix}", "yellow")

    async def _fire_cyno_alarm(self, object_name: str) -> None:
        """Immediate CRITICAL alarm for cynosural field detection (#146).

        Bypasses the normal cooldown and dedup checks — a cyno means a capital
        ship is about to drop.  A fresh alarm fires every time a cyno appears
        in D-scan (they can re-light).
        """
        alarm_text = f"⚠ CYNO DETECTED: {object_name} — CAPITAL DROP IMMINENT — LEAVE NOW"
        self._ui(self.main.write_message, alarm_text, "red")
        # TTS (#139)
        if self._tts_enabled:
            try:
                from evealert.tools.tts import speak  # noqa: PLC0415
                speak("Cynosural field detected. Capital drop imminent. Leave now.",
                      self._tts_rate)
            except Exception:
                pass
        # Sound + stats (reuse existing machinery)
        await self.play_sound(ALARM_SOUND, "Enemy")
        self.statistics.add_alarm("Enemy")
        save_lifetime_stats(self.statistics)

    def _on_dscan_probe(self) -> None:
        """Called when probes are detected on D-scan."""
        if self._dscan_alert_probes:
            self._ui(
                self.main.write_message,
                "D-SCAN: PROBES DETECTED — someone is scanning!",
                "red",
            )

    def _on_dscan_new_signature(self, old_count: int, new_count: int) -> None:
        """Called when the cosmic signature count increases on D-scan (#145)."""
        if not self._dscan_alert_new_sig:
            return
        delta = new_count - old_count
        self._ui(
            self.main.write_message,
            f"D-SCAN: NEW SIGNATURE — {delta} new cosmic sig(s) ({old_count} → {new_count}) — "
            "possible wormhole connection!",
            "red",
        )

    def _on_dscan_entry(self, entry) -> None:
        """Called for every D-scan entry — update last-seen ship class set."""
        from evealert.data.ship_classes import ShipThreatClass  # noqa: PLC0415
        if hasattr(entry, 'threat_class') and entry.threat_class != ShipThreatClass.UNKNOWN:
            self._dscan_last_classes.add(entry.threat_class)

    async def _display_system_info(self) -> None:
        """One-shot task: show pipe/pocket classification and sovereignty on start."""
        try:
            from evealert.tools.universe import (  # pylint: disable=import-outside-toplevel
                get_universe_cache,
            )

            system_name = self._settings_store.get("server.system", "").strip()
            if not system_name or system_name == "Enter a System Name":
                return

            cache = get_universe_cache()
            system_id = await cache.get_system_id(system_name)
            if system_id is None:
                return

            # Pipe/pocket classification (#75)
            classification = await cache.classify_system(system_id)
            gate_count = await cache.get_gate_count(system_id)
            self._ui(
                self.main.write_message,
                f"System: {system_name} | Type: {classification} ({gate_count} gate(s))",
                "cyan",
            )

            # Sovereignty display (#76)
            sov = await cache.get_sovereignty(system_id)
            if sov and sov.alliance_name:
                ihub_str = "IHub: active" if sov.has_ihub else "IHub: none"
                tcu_str = "TCU: active" if sov.has_tcu else "TCU: none"
                self._ui(
                    self.main.write_message,
                    f"Sov: {sov.alliance_name} — {ihub_str} | {tcu_str}",
                    "cyan",
                )
            elif sov:
                self._ui(
                    self.main.write_message,
                    "Sov: NPC / high-sec — no player sovereignty",
                    "cyan",
                )

            # Start the sov change monitor in background
            self._sov_task = self.loop.create_task(
                self._sov_monitor(system_id, sov.alliance_id if sov else None)
            )

        except Exception as exc:
            logger.debug("System info display error: %s", exc)

    async def _sov_monitor(
        self, system_id: int, initial_alliance_id: int | None
    ) -> None:
        """Poll sovereignty every 5 minutes and alert on change."""
        try:
            from evealert.tools.universe import (  # pylint: disable=import-outside-toplevel
                get_universe_cache,
            )

            cache = get_universe_cache()
            current_holder = initial_alliance_id

            while self.running:
                await asyncio.sleep(300)  # 5-minute refresh
                if not self.running:
                    break
                try:
                    sov = await cache.get_sovereignty(system_id)
                    new_holder = sov.alliance_id if sov else None
                    if new_holder != current_holder:
                        new_name = (
                            sov.alliance_name
                            if sov and sov.alliance_name
                            else "NPC/unclaimed"
                        )
                        self._ui(
                            self.main.write_message,
                            f"SOV CHANGE: {new_name} now controls this system",
                            "yellow",
                        )
                        current_holder = new_holder
                except Exception as exc:
                    logger.debug("Sovereignty poll failed: %s", exc)
        except Exception as exc:
            logger.debug("Sov monitor error: %s", exc)

    async def _esi_deep_integration_start(self) -> None:
        """On-start ESI tasks: fleet membership, structure fuel, standings monitor."""
        try:
            from evealert.tools.esi_auth import (  # pylint: disable=import-outside-toplevel
                get_esi_auth,
                get_fleet_membership,
                get_structure_fuel_warnings,
            )

            auth = get_esi_auth()
            if not auth.is_authenticated:
                return

            # Fleet membership display (#96)
            if self._esi_fleet_monitor:
                fleet = await get_fleet_membership(auth)
                if fleet:
                    fleet_id = fleet.get("fleet_id", "?")
                    role = fleet.get("role", "fleet member")
                    self._ui(
                        self.main.write_message,
                        f"Fleet: in fleet #{fleet_id} as {role}",
                        "cyan",
                    )
                else:
                    self._ui(self.main.write_message, "Fleet: not in fleet.", "gray")

            # Structure fuel warnings (#97)
            if self._esi_structure_alerts:
                warnings = await get_structure_fuel_warnings(auth)
                for warn in warnings:
                    self._ui(
                        self.main.write_message,
                        f"STRUCTURE FUEL: {warn['name']} — {warn['days_left']} days remaining",
                        "red",
                    )

            # Standings auto-classify monitor loop (#95)
            if self._esi_standings_classify:
                self._esi_standings_task = self.loop.create_task(
                    self._esi_standings_monitor()
                )

            # Location auto-detection — always start when ESI is authenticated
            # so server.system stays current without manual configuration.
            self.loop.create_task(self._location_monitor())
        except Exception as exc:
            logger.debug("ESI deep integration error: %s", exc)

    async def _esi_standings_monitor(self) -> None:
        """Periodically fetch standings and auto-classify Local pilots."""
        try:
            from evealert.tools.esi_auth import (  # pylint: disable=import-outside-toplevel
                get_esi_auth,
                get_personal_standings,
            )

            auth = get_esi_auth()
            # Build a contact_id → standing dict
            standings_by_id: dict[int, float] = {}
            while self.running:
                try:
                    standings = await get_personal_standings(auth)
                    standings_by_id = {
                        s["from_id"]: s["standing"]
                        for s in standings
                        if "from_id" in s and "standing" in s
                    }
                except Exception as exc:
                    logger.debug("Standings poll error: %s", exc)
                    await asyncio.sleep(300)
                    continue

                # Classify any known contacts against current Local names
                # (The actual Local pilot set is not always directly accessible here;
                #  this stores standings for use by _augment_with_esi when invoked)
                self._esi_standings_cache = standings_by_id

                await asyncio.sleep(300)  # 5-minute refresh
        except Exception as exc:
            logger.debug("ESI standings monitor error: %s", exc)

    async def _location_monitor(self) -> None:
        """Poll ESI for the authenticated character's current solar system.

        Runs every 30 s (EVE's minimum location cache TTL).  When the system
        changes it:
          - Updates server.system in the settings store
          - Refreshes the UI context line
          - Logs the detection and triggers the one-shot system-info display

        Only runs when ESI authentication is active.
        """
        _POLL = 30  # seconds
        _last_system: str | None = None
        _first_detection = True

        try:
            from evealert.tools.esi_auth import (  # noqa: PLC0415
                get_character_location,
                get_esi_auth,
            )

            while self.running:
                auth = get_esi_auth()
                if not auth.is_authenticated:
                    await asyncio.sleep(_POLL)
                    continue

                system_name = await get_character_location(auth)

                if system_name and system_name != _last_system:
                    _last_system = system_name
                    # Persist to settings so all consumers pick it up
                    self._settings_store.set("server.system", system_name)
                    self._settings_store.save()
                    # Refresh the UI header
                    self._ui(self.main.refresh_context_line)
                    self._ui(
                        self.main.write_message,
                        f"System: auto-detected \u2192 {system_name}",
                        "cyan",
                    )
                    # Re-run one-shot system info on system change
                    if _first_detection:
                        _first_detection = False
                        self.loop.create_task(self._display_system_info())
                    else:
                        self.loop.create_task(self._display_system_info())

                await asyncio.sleep(_POLL)
        except Exception as exc:
            logger.debug("ESI location monitor error: %s", exc)

    async def _cache_maintenance_task(self) -> None:
        """#177: periodically purge expired entries from the TTL caches
        that only ever check-and-skip a stale entry on read, never evict
        it (zKB kill lookups, universe kill-count/heatmap caches). Left
        unpurged, a long AFK session accumulates one stale entry per
        distinct system/constellation ever looked up and never revisited
        -- bounded by EVE's universe size in the worst case, but with no
        cleanup at all otherwise. Runs independently of whether R2Z2/
        gate-camp/route-checking are enabled, since zKillboard-on-alarm
        lookups alone can populate these caches over a long session.
        """
        while self.running:
            await asyncio.sleep(self._CACHE_MAINTENANCE_INTERVAL_SECONDS)
            if not self.running:
                break
            try:
                from evealert.tools.universe import get_universe_cache  # noqa: PLC0415
                from evealert.tools.zkillboard import get_client  # noqa: PLC0415
                from evealert.tools.threat_heatmap import purge_expired_cache  # noqa: PLC0415

                universe_purged = get_universe_cache().purge_expired_kill_counts()
                zkb_purged = get_client().purge_expired()
                heatmap_purged = purge_expired_cache()
                total = universe_purged + zkb_purged + heatmap_purged
                if total:
                    logger.debug(
                        "Cache maintenance: purged %d expired entries "
                        "(universe=%d, zkb=%d, heatmap=%d)",
                        total, universe_purged, zkb_purged, heatmap_purged,
                    )
            except Exception as exc:
                logger.debug("Cache maintenance cycle failed: %s", exc)

    async def _peak_hours_monitor(self) -> None:
        """Warn the pilot 15 min before a historically dangerous hour (#151).

        Runs hourly; uses the constellation threat heatmap to compare the
        upcoming hour's kill rate against the 7-day average.  Fires a warning
        when the ratio exceeds _peak_threshold_multiplier.
        """
        while self.running:
            try:
                import math as _math  # noqa: PLC0415
                from datetime import datetime, timezone  # noqa: PLC0415
                from evealert.tools.threat_heatmap import get_constellation_heatmap  # noqa: PLC0415

                system = self._settings_store.get("server.system", "").strip()
                if not system or system == "Enter a System Name":
                    await asyncio.sleep(300)
                    continue

                heatmap = await get_constellation_heatmap(system, days=7)
                if not heatmap:
                    await asyncio.sleep(3600)
                    continue

                # Aggregate histogram across all systems in the constellation
                combined = [0] * 24
                for entry in heatmap.values():
                    for i, v in enumerate(entry.kill_histogram):
                        combined[i] += v

                total_kills = sum(combined)
                if total_kills == 0:
                    await asyncio.sleep(3600)
                    continue

                avg_per_hour = total_kills / 24
                now_utc = datetime.now(timezone.utc)
                # Check the hour that starts 15 minutes from now
                next_hour = (now_utc.hour + 1) % 24
                next_hour_kills = combined[next_hour]

                if avg_per_hour > 0 and (next_hour_kills / avg_per_hour) >= self._peak_threshold_multiplier:
                    self._ui(
                        self.main.write_message,
                        f"\u26a0 PEAK HOURS APPROACHING: hostile activity at {next_hour:02d}:00 UTC "
                        f"is {int(next_hour_kills / avg_per_hour * 100)}% of daily average "
                        f"({next_hour_kills} kills vs avg {avg_per_hour:.1f}/h). "
                        "Consider docking up.",
                        "yellow",
                    )

                # Sleep until 15 min before the next hour turn
                minutes_to_next = 60 - now_utc.minute
                sleep_secs = max((minutes_to_next - 15) * 60, 60)
                await asyncio.sleep(sleep_secs)
            except Exception as exc:
                logger.debug("Peak hours monitor error: %s", exc)
                await asyncio.sleep(3600)

    async def _thera_monitor(self) -> None:
        """Poll Eve-Scout every 15 min for Thera connections near the configured system."""
        try:
            from evealert.tools.universe import (  # pylint: disable=import-outside-toplevel
                get_universe_cache,
            )
            from evealert.tools.wormhole import (  # pylint: disable=import-outside-toplevel
                find_nearby_thera_connections,
            )

            cache = get_universe_cache()
            system_name = self._settings_store.get("server.system", "").strip()
            system_id = await cache.get_system_id(system_name)
            if not system_id:
                return

            while self.running:
                try:
                    hits = await find_nearby_thera_connections(
                        system_id, self._thera_max_jumps
                    )
                    for conn, dist in hits:
                        jump_word = "jump" if dist == 1 else "jumps"
                        cls = f" [{conn.system_class}]" if conn.system_class else ""
                        self._ui(
                            self.main.write_message,
                            f"Thera: {conn.wh_type} to {conn.system_name}{cls} "
                            f"via {conn.hub_system_name} ({dist} {jump_word} away) "
                            f"— ~{conn.remaining_hours}h left",
                            "yellow",
                        )
                except Exception as exc:
                    logger.debug("Thera monitor error: %s", exc)
                await asyncio.sleep(900)  # 15-minute poll
        except Exception as exc:
            logger.debug("Thera monitor init error: %s", exc)

    async def _run_route_check(self, origin: str, destination: str) -> None:
        """Compute route threat and post results to the log pane."""
        try:
            from evealert.tools.universe import (  # pylint: disable=import-outside-toplevel
                get_universe_cache,
            )
            from evealert.tools.gatecamp import get_active_camps  # noqa: PLC0415

            cache = get_universe_cache()
            self._ui(
                self.main.write_message,
                f"Route: checking {origin} → {destination}...",
                "cyan",
            )
            origin_id = await cache.get_system_id(origin)
            dest_id = await cache.get_system_id(destination)
            if not origin_id or not dest_id:
                self._ui(
                    self.main.write_message,
                    "Route: could not resolve system name(s).",
                    "red",
                )
                return

            # #170: mark legs with an active gate camp as danger regardless
            # of the (hourly-cached) raw zKB kill count -- a fresh camp can
            # outpace that cache. Both confidence tiers count as "active".
            camped_system_ids = {c.system_id for c in get_active_camps(self._r2z2_consumer)}
            # #172: suggest_safer_route() computes the shortest path AND a
            # threat-weighted alternative in one search -- render both.
            suggestion = await cache.suggest_safer_route(
                origin_id, dest_id, camped_system_ids=camped_system_ids
            )
            if suggestion is None:
                self._ui(
                    self.main.write_message,
                    f"Route: no path found to {destination}.",
                    "red",
                )
                return

            self._render_route_legs(destination, "Shortest", suggestion.shortest)
            if suggestion.detoured:
                self._render_route_legs(destination, "Suggested", suggestion.suggested)
        except Exception as exc:
            logger.debug("Route check error: %s", exc)
            self._ui(self.main.write_message, f"Route check failed: {exc}", "red")

    def _render_route_legs(self, destination: str, label: str, legs: list) -> None:
        """Render one route_threat()/suggest_safer_route() leg list to the
        log pane (#170/#172) -- shared by the "Shortest" and "Suggested"
        route renderings in _run_route_check()."""
        hop_count = len(legs)
        danger_hops = [leg for leg in legs if leg.threat_level == "danger"]
        caution_hops = [leg for leg in legs if leg.threat_level == "caution"]
        self._ui(
            self.main.write_message,
            f"{label} route to {destination}: {hop_count} hop(s) — "
            f"{len(danger_hops)} danger / {len(caution_hops)} caution",
            "cyan",
        )
        for leg in legs:
            if leg.threat_level != "safe":
                icon = "⚠" if leg.threat_level == "danger" else "!"
                camp_tag = " [CAMP]" if leg.has_camp else ""
                self._ui(
                    self.main.write_message,
                    f"  {icon} {leg.system_name} ({leg.jumps_from_origin}j) "
                    f"— {leg.kills_last_hour} kill(s)/hr [{leg.threat_level}]{camp_tag}",
                    "red" if leg.threat_level == "danger" else "yellow",
                )

    async def _check_for_update(self) -> None:
        """Non-blocking startup version check against GitHub Releases."""
        try:
            from evealert import __version__  # pylint: disable=import-outside-toplevel
            from evealert.tools.update_checker import (  # pylint: disable=import-outside-toplevel
                check_for_update,
            )

            tag = await check_for_update(__version__)
            if tag:
                self._ui(self.main.notify_update, tag)
        except Exception as exc:
            logger.debug("Update check error: %s", exc)

    async def _fetch_and_report_kills(self, system_name: str) -> None:
        """Background task: fetch Zkillboard data and post results to the log."""
        try:
            from evealert.tools.zkillboard import (  # pylint: disable=import-outside-toplevel
                get_client,
            )

            kills = await get_client().get_recent_kills(system_name, limit=3)
        except Exception as exc:
            logger.debug("Zkillboard lookup failed: %s", exc)
            return

        # Dotlan link embedded on the system name (#210) rather than a
        # separate visible URL.
        system_display = make_link(
            system_name, f"https://dotlan.net/system/{system_name.replace(' ', '_')}"
        )
        if not kills:
            self._ui(
                self.main.write_message,
                f"Intel: No recent kills found for {system_display}",
                "yellow",
            )
            return

        self._ui(
            self.main.write_message,
            f"Intel: Recent kills in {system_display} ({len(kills)})",
            "yellow",
        )
        for k in kills:
            isk_m = k.total_value / 1_000_000
            msg = f"  [{k.kill_time[:16]}] {k.victim_name} ({k.victim_ship}) — {isk_m:.1f}M ISK"
            self._ui(self.main.write_message, msg, "yellow")

    async def _post_automation_webhook(self, alarm_text: str, alarm_type: str) -> None:
        """POST alarm JSON to the user-configured automation webhook URL (#153).

        Payload: {type, text, timestamp} so AutoHotkey / PyAutoGUI scripts can
        listen on localhost and trigger an in-game keyboard macro.
        """
        from evealert.tools.http_common import DEFAULT_HEADERS  # noqa: PLC0415

        payload = {
            "type": alarm_type,
            "text": alarm_text,
            "timestamp": time.time(),
        }
        try:
            import httpx  # noqa: PLC0415

            async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=3.0) as client:
                await client.post(self._automation_webhook_url, json=payload)
        except Exception as exc:
            logger.debug("Automation webhook failed: %s", exc)

        # Also update the /api/alarm/latest slot on the built-in web server
        try:
            from evealert.tools.web_server import set_latest_alarm  # noqa: PLC0415
            set_latest_alarm(payload)
        except Exception:
            pass

    async def send_webhook_message(self, alarm_type: str) -> None:
        """Send Discord webhook notification(s) with template formatting and multi-target support."""
        current_time = time.time()
        if current_time < self.webhook_cooldown_timer:
            logger.info("Webhook is in cooldown period. Message not sent.")
            return

        system = self._settings_store.get("server.system", "")
        try:
            msg = self._webhook_template.format(
                alarm_type=alarm_type,
                system=system,
                time=time.strftime("%H:%M:%S"),
                count=self.statistics.session_alarms,
            )
        except (KeyError, ValueError, IndexError) as exc:
            logger.warning(
                "Webhook template format error (%s) — using fallback message. "
                "Check your webhook template in Settings.",
                exc,
            )
            msg = f"{alarm_type} alarm in {system}"

        # 1. "All events" webhook (server.webhook) — fires for every alarm type
        if self._webhook and not self.webhook_sent:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._webhook.execute, msg)
                self.webhook_cooldown_timer = current_time + WEBHOOK_COOLDOWN
                self.webhook_sent = True
            except Exception as e:
                logger.error("Error sending all-events webhook: %s", e)

        # 2. Per-type webhooks (enemy / faction) with optional min-count gate
        await self._send_typed_webhook(alarm_type, msg)

    async def play_sound(
        self, sound: str, alarm_type: str, client_name: str | None = None
    ) -> None:
        """Play alarm sound with trigger limits and cooldown management.

        *client_name* (#174): when given (an extra multi-client entry),
        trigger-count/cooldown/currently-playing state is keyed by
        (client_name, alarm_type) instead of a bare alarm_type string, so
        one client's cooldown can never suppress another's alarm. The
        primary client passes None, keeping its dict keys byte-identical
        to pre-#174 behavior.

        Known MVP limitation: sounddevice's sd.play() uses a single
        shared default output stream -- two alarms firing at nearly the
        same instant from different clients can cut each other off rather
        than mix. Cooldown/dedup/log/webhook dispatch are still correctly
        independent per client regardless.
        """
        if self.mute:
            return

        if not _SOUNDDEVICE_AVAILABLE:
            self._ui(
                self.main.write_message,
                "Audio disabled: PortAudio not found. On macOS run: brew install portaudio",
                "red",
            )
            return

        key = (client_name, alarm_type) if client_name else alarm_type
        label = f"[{client_name}] {alarm_type}" if client_name else alarm_type

        if key not in self.alarm_trigger_counts:
            self.alarm_trigger_counts[key] = 0
        if key not in self.cooldown_timers:
            self.cooldown_timers[key] = 0

        current_time = time.time()
        if current_time < self.cooldown_timers[key]:
            self._ui(
                self.main.write_message,
                f"{label} Sound is in cooldown period.",
                "red",
            )
            return

        self.alarm_trigger_counts[key] += 1

        if self.alarm_trigger_counts[key] > self.max_sound_triggers:
            # Pick the cooldown limit for this alarm type
            cooldown_limit = (
                self._cooldown_enemy
                if alarm_type == "Enemy"
                else self._cooldown_faction
            )
            self.cooldown_timers[key] = current_time + cooldown_limit
            self.alarm_trigger_counts[key] = 0
            self._ui(
                self.main.write_message,
                f"{label} Sound is now in cooldown for {cooldown_limit} seconds.",
                "red",
            )
            return

        if key not in self.currently_playing_sounds:
            self.currently_playing_sounds[key] = True
            try:
                data, samplerate = sf.read(sound, dtype="int16")

                if data.ndim == 1:
                    data = np.stack([data, data], axis=-1)
                elif data.ndim == 2 and data.shape[1] == 1:
                    data = np.repeat(data, AUDIO_CHANNELS, axis=1)

                data_with_volume = (data * self.volume).astype("int16")

                # Non-blocking play; wait in executor so vision tasks continue
                loop = asyncio.get_running_loop()
                sd.play(data_with_volume, samplerate)
                await loop.run_in_executor(None, sd.wait)
            except Exception as e:
                if self.alarm_trigger_counts.get(key, 0) <= 1:
                    self._ui(
                        self.main.open_error_window,
                        "Error Playing Sound. Check Logs for more information.",
                    )
                logger.exception("Error Playing Sound: %s", e)
            finally:
                self.currently_playing_sounds.pop(key, None)

    def _resolve_enemy_identities(self) -> dict:
        """Resolve each currently-detected enemy icon to a pilot name via OCR
        (#213), throttled so this doesn't run OCR on every 0.1–0.2 s poll.

        Returns ``{quantized_position: name}`` — only for icons OCR could
        confidently attach a name to; an icon absent from the returned dict
        means OCR found nothing for that specific row (caller falls back to
        its position as the identity for dedup purposes).

        Also updates ``self._last_ocr_names`` (flat, deduped list of names
        actually matched to an enemy icon's row — used by _augment_with_esi
        and _build_enemy_alarm_text) as a side effect, exactly once per
        fresh OCR attempt. Deliberately NOT every name OCR finds in the
        captured region (that would include the player's own name and any
        non-hostile pilots sharing the region, e.g. the whole Local
        roster).

        Throttling: re-runs OCR immediately whenever the SET of detected
        icon positions changes (a new arrival must be identified right
        away), otherwise at most once every
        ``_IDENTITY_RESOLVE_MIN_INTERVAL`` seconds. User-facing log lines
        (detected pilots / no backend / invalid region) are only emitted on
        an actual fresh OCR attempt, not on throttle-cache-hits, so a
        sustained multi-pilot engagement doesn't spam the log pane.
        """
        now = time.time()
        current_keys = frozenset(
            self._quantize_point(p) for p in (self._enemy_points or [])
        )
        if (
            current_keys == self._last_identity_keys
            and now - self._last_identity_resolve_time < self._IDENTITY_RESOLVE_MIN_INTERVAL
        ):
            return self._last_enemy_identities  # throttled — reuse cached mapping

        self._last_identity_keys = current_keys
        self._last_identity_resolve_time = now

        if not self._ocr_enabled:
            self._last_enemy_identities = {}
            self._last_ocr_names = []
            return {}

        try:
            from evealert.tools.ocr_local import (  # noqa: PLC0415
                is_ocr_available,
                match_names_to_targets,
                resolve_region,
            )

            if not is_ocr_available():
                self._log_ocr_message(
                    "OCR [alarm]: no backend available (Windows.Media.Ocr / Tesseract) — name detection skipped",
                    "yellow",
                )
                self._last_enemy_identities = {}
                self._last_ocr_names = []
                return {}

            region = resolve_region(
                self._ocr_region, (self.x1, self.y1, self.x2, self.y2)
            )
            if not region:
                self._log_ocr_message(
                    f"OCR [alarm]: region is invalid (ocr_region={self._ocr_region}, "
                    f"alert_region=({self.x1},{self.y1},{self.x2},{self.y2})) — skipped",
                    "yellow",
                )
                self._last_enemy_identities = {}
                self._last_ocr_names = []
                return {}

            # Map each detected icon's quantized position to its ABSOLUTE
            # screen Y (region-local point + Alert Region's own screen
            # origin), so match_names_to_targets can compare it against
            # OCR'd row positions regardless of which region was captured.
            targets = {
                self._quantize_point((x, y)): self.y1 + y
                for (x, y) in (self._enemy_points or [])
            }
            heights = [h for (_w, h) in self.alert_vision.needle_dims if h > 0]
            row_tolerance = (max(heights) if heights else 20) * 0.8

            logger.debug(
                "OCR [alarm]: capturing region %s, targets=%s tol=%.1f",
                region, targets, row_tolerance,
            )
            identities, all_names = match_names_to_targets(region, targets, row_tolerance)
            self._last_enemy_identities = identities
            # Regression fix: _last_ocr_names feeds the alarm headline
            # (_build_enemy_alarm_text) AND the ESI hint-name list
            # (_augment_with_esi's hint_names) -- it must only ever contain
            # names actually matched to an enemy icon's row. Using
            # all_names here (every name OCR found anywhere in the
            # captured region, i.e. the whole Local roster including the
            # player's own name and corp/fleet mates) reported and
            # ESI-queried the entire roster as "the enemy" whenever the
            # region spans more than the enemy's own row.
            matched_names = list(dict.fromkeys(identities.values()))
            self._last_ocr_names = matched_names
            if matched_names:
                self._log_ocr_message(
                    f"OCR [alarm]: identified pilot(s): {', '.join(matched_names)}",
                    "cyan",
                )
            elif all_names:
                self._log_ocr_message(
                    f"OCR [alarm]: found {len(all_names)} name(s) in region but none "
                    f"matched an enemy icon's row (region/tolerance misaligned?): "
                    f"{', '.join(all_names)}",
                    "yellow",
                )
            else:
                self._log_ocr_message(
                    f"OCR [alarm]: capture at {region} returned no names (check region config)",
                    "yellow",
                )
            return identities
        except Exception as exc:
            self._log_ocr_message(f"OCR [alarm]: exception — {exc}", "yellow")
            logger.debug("_resolve_enemy_identities: OCR failed: %s", exc, exc_info=True)
            self._last_enemy_identities = {}
            self._last_ocr_names = []
            return {}

    def _log_ocr_message(self, message: str, color: str) -> None:
        """Log an OCR [alarm] diagnostic line, but only when it differs from
        the last one logged.

        Without this, every fresh (non-throttled) OCR resolve re-printed
        "identified pilot(s): ..." even when the result was identical to
        last time -- e.g. a single stationary pilot re-announced every
        _IDENTITY_RESOLVE_MIN_INTERVAL seconds for as long as they stayed,
        even though nothing about the sighting had changed.
        """
        if message == self._last_ocr_log_message:
            return
        self._last_ocr_log_message = message
        self._ui(self.main.write_message, message, color)

    def _build_enemy_alarm_text(self) -> str:
        """Build the Enemy alarm headline from the pilot name(s) already
        resolved by _resolve_enemy_identities() this cycle (#213 — OCR now
        runs once, before the alarm-fire decision, not here). Falls back to
        the bare headline when OCR is disabled/unavailable or found nothing.
        """
        base = "Enemy Appears!"
        if self._last_ocr_names:
            return f"{base} — {', '.join(self._last_ocr_names)}"
        return base

    async def run(self) -> None:
        """Main alarm-trigger loop."""
        while True:
            if self._settings_store.changed:
                self.load_settings()
                self._settings_store.changed = False

            self.alarm_detected = False

            try:
                if self.faction:
                    self.alarm_detected = True
                    await self.alarm_detection(
                        "Faction Spawn!", self._faction_sound, "Faction"
                    )
                if self.enemy:
                    self.alarm_detected = True
                    # #213: resolve OCR identities first (throttled — see
                    # _resolve_enemy_identities) so dedup can key on pilot
                    # name, not just screen position.
                    enemy_identities = self._resolve_enemy_identities()
                    # Only alarm for a new/re-eligible enemy, not every poll (#100)
                    if self._should_alarm_enemy(enemy_identities):
                        await self.alarm_detection(
                            self._build_enemy_alarm_text(), self._alarm_sound, "Enemy"
                        )

                # #174: extra clients -- independent dedup/cooldown/rearm
                # state, no OCR identity resolution or ESI/threat-score
                # augmentation (primary-client/global-only in this MVP).
                for client in self._extra_clients:
                    if not client.enabled:
                        continue
                    if client.faction:
                        self.alarm_detected = True
                        await self.alarm_detection(
                            "Faction Spawn!", self._faction_sound, "Faction", client.name
                        )
                    if client.enemy:
                        self.alarm_detected = True
                        if self._should_alarm_extra_client_enemy(client):
                            count = len(client.enemy_points) or 1
                            plural = "s" if count != 1 else ""
                            text = f"Enemy Appears! ({count} hostile{plural})"
                            await self.alarm_detection(
                                text, self._alarm_sound, "Enemy", client.name
                            )
            except Exception as e:
                # Log the full traceback so we can diagnose the root cause.
                logger.error("Alert System Error: %s", e, exc_info=True)
                # Show in UI log — include exception type so it's visible in
                # the log pane without needing to open an external log file.
                err_summary = f"{type(e).__name__}: {e}"
                self._ui(
                    self.main.write_message,
                    f"Alert error (engine still running): {err_summary} — check logs.",
                    "red",
                )
                # Do NOT stop the engine for transient errors (webhook template
                # KeyError, brief audio hiccup, etc.).  Back off for one cycle
                # to avoid a tight error loop if the error is persistent.
                await asyncio.sleep(1.0)

            if not self.faction:
                await self.reset_alarm("Faction")
            if not self.enemy:
                await self.reset_alarm("Enemy")
            for client in self._extra_clients:
                if not client.faction:
                    self._reset_extra_client_alarm(client, "Faction")
                if not client.enemy:
                    self._reset_extra_client_alarm(client, "Enemy")

            await asyncio.sleep(
                random.uniform(MAIN_CHECK_SLEEP_MIN, MAIN_CHECK_SLEEP_MAX)
            )
