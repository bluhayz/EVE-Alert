import asyncio
import logging
import os
import random
import time
from typing import Callable

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
from evealert.tools.vision import Vision
from evealert.tools.windowscapture import WindowCapture

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
        # Quantized enemy center -> last-alarm epoch time
        self._seen_enemies: dict = {}

        # Long-running asyncio task handles (cancelled in stop(), #102)
        self.vision_t = None
        self.vision_faction_t = None
        self.alert_t = None
        self._thera_task = None
        self._sov_task = None
        self._esi_standings_task = None

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
        self._intel_log_channel = ""
        self._intel_watcher = None  # IntelWatcher instance

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

        # v3.3: D-scan monitor
        self._dscan_enabled = False
        self._dscan_alert_red = True
        self._dscan_alert_orange = False
        self._dscan_alert_probes = True
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
        self._esi_fleet_monitor = False
        self._esi_structure_alerts = False
        self._esi_standings_cache: dict = {}  # character_id → standing float

        # v4.1: OCR pilot-name detection (#98)
        self._ocr_enabled = False
        self._ocr_region = (0, 0, 0, 0)

        # v4.2: diagnostic verbose-logging mode
        self._diagnostics_enabled = False

        self.load_settings()
        self._load_plugins()
        self._validate_audio_files()

    # ------------------------------------------------------------------
    # Thread safety helper
    # ------------------------------------------------------------------

    def _ui(self, fn: Callable, *args, **kwargs) -> None:
        """Route GUI calls through UIBridge (bridge-aware shim for legacy call sites).

        Detects the known GUI functions and delegates to self._bridge so the
        engine never calls Tk directly.  Unknown callables are dispatched via
        self.main.after(0, ...) as a safe fallback.
        """
        if fn is self.main.write_message:
            text = args[0] if args else kwargs.get("text", "")
            color = args[1] if len(args) > 1 else kwargs.get("color", "normal")
            self._bridge.log(text, color)
        elif fn in (self.main.update_alert_button, self.main.update_faction_button):
            self._bridge.refresh_region_toggles()
        elif fn is self.main.open_error_window:
            msg = args[0] if args else ""
            self._bridge.show_error(msg)
        else:
            self.main.after(0, lambda: fn(*args, **kwargs))

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

    def start(self) -> bool:
        """Start the detection engine in this (daemon) thread."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        self.loop.run_until_complete(self.vision_check())
        if self.check:
            self.vision_t = self.loop.create_task(self.vision_thread())
            self.vision_faction_t = self.loop.create_task(self.vision_faction_thread())
            self.alert_t = self.loop.create_task(self.run())

            # Start intel log watcher if configured
            if self._intel_log_enabled and self._intel_log_channel:
                from evealert.tools.intel_watcher import (  # pylint: disable=import-outside-toplevel
                    IntelWatcher,
                )

                self._intel_watcher = IntelWatcher(
                    channel_pattern=self._intel_log_channel,
                    callback=self._on_intel_line,
                )
                self.loop.create_task(self._intel_watcher.run())

            self.running = True
            self._ui(self.main.write_message, "System: EVE Alert started.", "green")
            # Fire background update check — non-blocking, silent on failure
            self.loop.create_task(self._check_for_update())
            # v3.2: pipe/pocket classification + sovereignty display (one-shot at start)
            self.loop.create_task(self._display_system_info())
            # v3.2: adjacent system kill monitor
            if self._adjacent_enabled:
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
        ):
            if task is not None and not task.done():
                task.cancel()
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
            self._intel_watcher,
        ):
            if monitor is not None:
                monitor.stop()
        # Cancel raw asyncio tasks and stop the loop on the loop's own thread.
        if self.loop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(self._shutdown_loop)
        try:
            from evealert.tools.plugin_loader import (  # pylint: disable=import-outside-toplevel
                get_plugin_manager,
            )

            get_plugin_manager().call("on_stop")
        except Exception:
            pass
        self._web_server = None
        self._neighbor_monitor = None
        self._dscan_watcher = None
        self._killmail_monitor = None
        self._intel_watcher = None
        self.wincap.close()
        self.currently_playing_sounds.clear()
        self.alarm_trigger_counts.clear()
        self.cooldown_timers.clear()
        self.alert_vision.debug_mode = False
        self.alert_vision_faction.debug_mode_faction = False
        self._ui(self.main.update_alert_button)
        self._ui(self.main.update_faction_button)
        # Persist stats on every stop so totals survive crashes / forced exits
        save_lifetime_stats(self.statistics)
        save_session_report(self.statistics, time.time())

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

            # D-scan monitor settings
            ds = settings.get("dscan", {})
            self._dscan_enabled = bool(ds.get("enabled", False))
            self._dscan_alert_red = bool(ds.get("alert_red", True))
            self._dscan_alert_orange = bool(ds.get("alert_orange", False))
            self._dscan_alert_probes = bool(ds.get("alert_probes", True))

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
        screenshot, _ = self.wincap.get_screenshot_value(
            self.y1, self.x1, self.x2, self.y2
        )
        if screenshot is not None:
            self.check = True
        else:
            self._ui(self.main.write_message, "Wrong Alert Settings.", "red")
            self.check = False

    async def vision_thread(self) -> None:
        """Continuously check for enemy detection in the alert region."""
        while True:
            screenshot, _ = self.wincap.get_screenshot_value(
                self.y1, self.x1, self.x2, self.y2
            )
            if screenshot is not None:
                enemy = self.alert_vision.find(
                    screenshot, self.detection, self.image_thresholds
                )
                # Retain match centers for per-enemy dedup (#100). Set both
                # together (no await between) so run() sees a consistent pair.
                self._enemy_points = list(enemy)
                self.enemy = bool(enemy)
            else:
                self._enemy_points = []
                self.enemy = False
                self._ui(self.main.write_message, "Wrong Alert Settings.", "red")
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
                    screenshot_faction, self.detection_faction, self.image_thresholds
                )
                self.faction = bool(faction)
            else:
                # Reset flag on capture failure so stale True doesn't loop alarms
                self.faction = False
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

        if self._webhook and alarm_type == "Enemy" and self.webhook_sent:
            try:
                reset_msg = (
                    f"Alarm cleared in {self._settings_store.get('server.system', '')}"
                )
                self._webhook.execute(reset_msg)
            except Exception as e:
                logger.error("Error sending reset webhook: %s", e)
            self.webhook_sent = False

    @staticmethod
    def _quantize_point(point, grid: int = 20) -> tuple:
        """Snap an (x, y) match center to a coarse grid so sub-pixel jitter
        between frames maps to the same enemy identity (#100)."""
        x, y = point
        return (int(x) // grid, int(y) // grid)

    def _should_alarm_enemy(self) -> bool:
        """Return True only when a genuinely new enemy has appeared, or an
        already-seen enemy's cooldown window has elapsed. Prevents the alarm
        (stats/sound/webhook/plugins/push) from re-firing on every poll while
        the same enemy stays on screen (#100).
        """
        now = time.time()
        cooldown = max(int(self._cooldown_enemy), 1)
        keys = {self._quantize_point(p) for p in (self._enemy_points or [])}
        if not keys:
            # self.enemy was True but no points — treat as one anonymous enemy.
            keys = {(-1, -1)}

        trigger = any(
            key not in self._seen_enemies or (now - self._seen_enemies[key]) >= cooldown
            for key in keys
        )

        # Prune the seen-set to enemies still on screen (preserve timestamps).
        self._seen_enemies = {key: self._seen_enemies.get(key, 0.0) for key in keys}
        if trigger:
            for key in keys:
                self._seen_enemies[key] = now
        return trigger

    async def alarm_detection(
        self, alarm_text: str, sound: str = ALARM_SOUND, alarm_type: str = "Enemy"
    ) -> None:
        """Trigger an alarm: log message, statistics, sound, webhook."""
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
        await self.play_sound(sound, alarm_type)
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
        # Also runs when OCR name detection is on, so OCR'd names flow into the
        # KOS / ESI / Zkillboard pipeline (#98).
        if alarm_type == "Enemy" and (self._esi_enabled or self._ocr_enabled):
            asyncio.ensure_future(self._augment_with_esi())

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
            hook.execute(msg)
        except Exception as e:
            logger.error("Error sending %s webhook: %s", alarm_type, e)

    def _on_intel_line(self, line: str) -> None:
        """Called from IntelWatcher for each new chat-log line.

        Posts the line to the GUI log on the main Tkinter thread.
        Only lines that look like player chat (not system messages) are forwarded.
        """
        # EVE chat log system messages start with "  " (two spaces) or specific tokens
        stripped = line.strip()
        # Skip empty lines and EVE session-header lines (start with "--")
        if not stripped or stripped.startswith("-------"):
            return
        self._ui(self.main.write_message, f"Intel: {stripped}", "cyan")
        # Notify plugins
        try:
            from evealert.tools.plugin_loader import (  # pylint: disable=import-outside-toplevel
                get_plugin_manager,
            )

            get_plugin_manager().call("on_intel", line=stripped)
        except Exception as exc:
            logger.debug("Plugin on_intel hook failed: %s", exc)

    async def _augment_with_esi(self) -> None:
        """Background task: enriched ESI + Zkillboard pilot intel on Enemy alarm.

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

            chatlog_dir = get_eve_chatlog_dir()

            # Gather Local names from the chat log (best-effort — may be absent).
            names: list = []
            local_log = find_intel_log(chatlog_dir, "Local") if chatlog_dir else None
            if local_log is not None:
                try:
                    with open(local_log, encoding="utf-8", errors="replace") as fh:
                        lines = fh.readlines()[-50:]
                    names = extract_joining_characters(lines)
                except OSError:
                    names = []

            # v4.1: OCR the configured region and merge detected names (#98).
            if self._ocr_enabled:
                try:
                    from evealert.tools.ocr_local import (  # pylint: disable=import-outside-toplevel
                        read_local_names,
                        resolve_region,
                    )

                    region = resolve_region(
                        self._ocr_region, (self.x1, self.y1, self.x2, self.y2)
                    )
                    ocr_names = read_local_names(region) if region else []
                    if ocr_names:
                        self._ui(
                            self.main.write_message,
                            f"OCR detected: {', '.join(ocr_names)}",
                            "cyan",
                        )
                        for name in ocr_names:
                            if name not in names:
                                names.append(name)
                except Exception as exc:
                    logger.debug("OCR name detection failed: %s", exc)

            if not names:
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
            results = await client.lookup_many(names[:5])
            if not results:
                return

            # v3.7: fleet composition analysis (3+ hostiles)
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

            for info in results:
                # ── Threat tier check ────────────────────────────────────────
                tier = None
                for substr, t in self._threat_tiers.items():
                    if (
                        substr.lower() in info.name.lower()
                        or substr.lower() in (info.corporation_name or "").lower()
                        or substr.lower() in (info.alliance_name or "").lower()
                    ):
                        tier = t
                        break

                # ── Build header line ─────────────────────────────────────────
                tier_prefix = {
                    "red": "⚠ [KOS-RED]",
                    "orange": "⚠ [HOSTILE]",
                    "yellow": "[CAUTION]",
                }.get(tier or "", "")

                parts = [f"  {tier_prefix} {info.name}".strip()]
                if self._esi_show_corp and info.corporation_name:
                    parts.append(f"[{info.corporation_name}]")
                if self._esi_show_alliance and info.alliance_name:
                    parts.append(f"<{info.alliance_name}>")

                # age and corp history
                if info.age_days >= 0:
                    age_str = f"{info.age_days}d old"
                    corps_str = f"{info.corp_history_count} corp(s)"
                    parts.append(f"— {age_str}, {corps_str}")

                line_colour = (
                    "red"
                    if tier == "red"
                    else "yellow" if tier in ("orange", "yellow") else "cyan"
                )
                self._ui(self.main.write_message, " ".join(parts), line_colour)

                # ── Flashy security status ────────────────────────────────────
                if self._esi_alert_flashy and info.security_status <= -5.0:
                    self._ui(
                        self.main.write_message,
                        f"    ⚠ FLASHY: {info.name} (sec: {info.security_status:.1f}) — attackable in low-sec",
                        "red",
                    )

                # ── Cyno-alt heuristic ────────────────────────────────────────
                if info.age_days < 30:
                    self._ui(
                        self.main.write_message,
                        f"    ⚠ YOUNG PILOT: {info.name} ({info.age_days}d old) — possible cyno/scout alt",
                        "yellow",
                    )

                # ── Zkillboard kill profile ───────────────────────────────────
                try:
                    zkb = await client.get_zkillboard_profile(info.character_id)
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
                except Exception as exc:
                    logger.debug("Zkillboard profile augmentation failed: %s", exc)

                # ── KOS check (v3.4) ──────────────────────────────────────────
                try:
                    from evealert.tools.kos_checker import (  # pylint: disable=import-outside-toplevel
                        get_kos_checker,
                    )

                    kos_checker = get_kos_checker(
                        cva_enabled=self._kos_cva_enabled,
                        api_urls=self._kos_custom_urls,
                    )
                    kos_result = await kos_checker.check(
                        info.name,
                        info.corporation_name or "",
                        info.alliance_name or "",
                    )
                    if kos_result:
                        _any_kos = True
                        _kos_tier_label = kos_result.label
                        self._ui(
                            self.main.write_message,
                            f"    ⚠ KOS ({kos_result.source}): {info.name} — {kos_result.label}",
                            "red",
                        )
                except Exception as exc:
                    logger.debug("KOS check failed: %s", exc)

                # ── ESI standings auto-classify (v4.0) ───────────────────────
                if self._esi_standings_classify and self._esi_standings_cache:
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
                adjacent_kills=self._neighbor_monitor.last_kill_count
                    if self._neighbor_monitor and hasattr(self._neighbor_monitor, "last_kill_count") else 0,
                is_cyno=ShipThreatClass.CYNO in self._dscan_last_classes,
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

        # Build a human-readable label for the class
        class_labels = {
            ShipThreatClass.TACKLE:      "TACKLE — get out NOW",
            ShipThreatClass.DICTOR:      "DICTOR — bubble incoming",
            ShipThreatClass.FORCE_RECON: "FORCE RECON — cloaked threat",
            ShipThreatClass.COVERT_OPS:  "COVERT OPS — scanning",
            ShipThreatClass.CYNO:        "CYNO — capital drop imminent",
            ShipThreatClass.COMBAT:      "combat ship",
        }
        class_label = class_labels.get(threat_class, "") if threat_class else ""
        suffix = f" [{class_label}]" if class_label else ""

        if tier == "red" and self._dscan_alert_red:
            self._ui(self.main.write_message, f"D-SCAN RED: {name}{suffix}", "red")
        elif tier == "orange" and self._dscan_alert_orange:
            self._ui(self.main.write_message, f"D-SCAN ORANGE: {name}{suffix}", "yellow")

    def _on_dscan_probe(self) -> None:
        """Called when probes are detected on D-scan."""
        if self._dscan_alert_probes:
            self._ui(
                self.main.write_message,
                "D-SCAN: PROBES DETECTED — someone is scanning!",
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

            legs = await cache.route_threat(origin_id, dest_id)
            if legs is None:
                self._ui(
                    self.main.write_message,
                    f"Route: no path found to {destination}.",
                    "red",
                )
                return

            hop_count = len(legs)
            danger_hops = [l for l in legs if l.threat_level == "danger"]
            caution_hops = [l for l in legs if l.threat_level == "caution"]
            self._ui(
                self.main.write_message,
                f"Route to {destination}: {hop_count} hop(s) — "
                f"{len(danger_hops)} danger / {len(caution_hops)} caution",
                "cyan",
            )
            for leg in legs:
                if leg.threat_level != "safe":
                    icon = "⚠" if leg.threat_level == "danger" else "!"
                    self._ui(
                        self.main.write_message,
                        f"  {icon} {leg.system_name} ({leg.jumps_from_origin}j) "
                        f"— {leg.kills_last_hour} kill(s)/hr [{leg.threat_level}]",
                        "red" if leg.threat_level == "danger" else "yellow",
                    )
        except Exception as exc:
            logger.debug("Route check error: %s", exc)
            self._ui(self.main.write_message, f"Route check failed: {exc}", "red")

    async def _check_for_update(self) -> None:
        """Non-blocking startup version check against GitHub Releases."""
        try:
            from evealert import __version__  # pylint: disable=import-outside-toplevel
            from evealert.tools.update_checker import (  # pylint: disable=import-outside-toplevel
                check_for_update,
            )

            tag = await check_for_update(__version__)
            if tag:
                url = "https://github.com/bluhayz/EVE-Alert/releases/latest"
                self._ui(
                    self.main.write_message,
                    f"Update available: {tag} — {url}",
                    "yellow",
                )
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

        if not kills:
            self._ui(
                self.main.write_message,
                f"Intel: No recent kills found for {system_name}.",
                "yellow",
            )
            return

        self._ui(
            self.main.write_message,
            f"Intel: Recent kills in {system_name} ({len(kills)}):",
            "yellow",
        )
        for k in kills:
            isk_m = k.total_value / 1_000_000
            msg = f"  [{k.kill_time[:16]}] {k.victim_name} ({k.victim_ship}) — {isk_m:.1f}M ISK"
            self._ui(self.main.write_message, msg, "yellow")

    async def send_webhook_message(self, alarm_type: str) -> None:
        """Send Discord webhook notification(s) with template formatting and multi-target support."""
        current_time = time.time()
        if current_time < self.webhook_cooldown_timer:
            logger.info("Webhook is in cooldown period. Message not sent.")
            return

        system = self._settings_store.get("server.system", "")
        msg = self._webhook_template.format(
            alarm_type=alarm_type,
            system=system,
            time=time.strftime("%H:%M:%S"),
            count=self.statistics.session_alarms,
        )

        # 1. "All events" webhook (server.webhook) — fires for every alarm type
        if self._webhook and not self.webhook_sent:
            try:
                self._webhook.execute(msg)
                self.webhook_cooldown_timer = current_time + WEBHOOK_COOLDOWN
                self.webhook_sent = True
            except Exception as e:
                logger.error("Error sending all-events webhook: %s", e)

        # 2. Per-type webhooks (enemy / faction) with optional min-count gate
        await self._send_typed_webhook(alarm_type, msg)

    async def play_sound(self, sound: str, alarm_type: str) -> None:
        """Play alarm sound with trigger limits and cooldown management."""
        if self.mute:
            return

        if not _SOUNDDEVICE_AVAILABLE:
            self._ui(
                self.main.write_message,
                "Audio disabled: PortAudio not found. On macOS run: brew install portaudio",
                "red",
            )
            return

        if alarm_type not in self.alarm_trigger_counts:
            self.alarm_trigger_counts[alarm_type] = 0
        if alarm_type not in self.cooldown_timers:
            self.cooldown_timers[alarm_type] = 0

        current_time = time.time()
        if current_time < self.cooldown_timers[alarm_type]:
            self._ui(
                self.main.write_message,
                f"{alarm_type} Sound is in cooldown period.",
                "red",
            )
            return

        self.alarm_trigger_counts[alarm_type] += 1

        if self.alarm_trigger_counts[alarm_type] > self.max_sound_triggers:
            # Pick the cooldown limit for this alarm type
            cooldown_limit = (
                self._cooldown_enemy
                if alarm_type == "Enemy"
                else self._cooldown_faction
            )
            self.cooldown_timers[alarm_type] = current_time + cooldown_limit
            self.alarm_trigger_counts[alarm_type] = 0
            self._ui(
                self.main.write_message,
                f"{alarm_type} Sound is now in cooldown for {cooldown_limit} seconds.",
                "red",
            )
            return

        if alarm_type not in self.currently_playing_sounds:
            self.currently_playing_sounds[alarm_type] = True
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
                if self.alarm_trigger_counts.get(alarm_type, 0) <= 1:
                    self._ui(
                        self.main.open_error_window,
                        "Error Playing Sound. Check Logs for more information.",
                    )
                logger.exception("Error Playing Sound: %s", e)
            finally:
                self.currently_playing_sounds.pop(alarm_type, None)

    def _build_enemy_alarm_text(self) -> str:
        """Build the Enemy alarm headline, including OCR'd pilot names when available.

        Captures the configured Local-chat screen region via OCR (Tesseract)
        so the alarm headline reads 'Enemy Appears! — Bad Pilot, Other Pilot'
        instead of firing a plain message and logging names separately.
        Falls back to 'Enemy Appears!' when OCR is disabled or unavailable.
        """
        base = "Enemy Appears!"
        if not self._ocr_enabled:
            return base
        try:
            from evealert.tools.ocr_local import (  # noqa: PLC0415
                read_local_names,
                resolve_region,
            )

            region = resolve_region(
                self._ocr_region, (self.x1, self.y1, self.x2, self.y2)
            )
            if region:
                names = read_local_names(region)
                if names:
                    return f"{base} — {', '.join(names)}"
        except Exception as exc:
            logger.debug("_build_enemy_alarm_text: OCR failed: %s", exc)
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
                    # Only alarm for a new/re-eligible enemy, not every poll (#100)
                    if self._should_alarm_enemy():
                        await self.alarm_detection(
                            self._build_enemy_alarm_text(), self._alarm_sound, "Enemy"
                        )
            except Exception as e:
                logger.error("Alert System Error: %s", e, exc_info=True)
                self.stop()
                self._ui(
                    self.main.write_message, "Alert system error — check logs.", "red"
                )
                return

            if not self.faction:
                await self.reset_alarm("Faction")
            if not self.enemy:
                await self.reset_alarm("Enemy")

            await asyncio.sleep(
                random.uniform(MAIN_CHECK_SLEEP_MIN, MAIN_CHECK_SLEEP_MAX)
            )
