import copy
import json
import os
import tempfile
from collections import namedtuple
from typing import TYPE_CHECKING

import customtkinter
from dhooks_lite import Webhook

from evealert.constants import DEFAULT_COOLDOWN_TIMER
from evealert.hotkeys import DEFAULT_HOTKEYS
from evealert.settings.helper import get_settings_path
from evealert.settings.logger import logging

if TYPE_CHECKING:
    from evealert.menu.main import MainMenu

logger = logging.getLogger("menu")

DEFAULT_SETTINGS = {
    "log_level": "INFO",
    "active_profile": "Default",
    "alert_region_1": {"x": 0, "y": 0},
    "alert_region_2": {"x": 0, "y": 0},
    "faction_region_1": {"x": 0, "y": 0},
    "faction_region_2": {"x": 0, "y": 0},
    "detectionscale": {"value": 90},
    "faction_scale": {"value": 90},
    "cooldown_timer": {"value": DEFAULT_COOLDOWN_TIMER},
    "volume": {"value": 100},
    "server": {
        "webhook": "",
        "system": "Enter a System Name",
        "mute": False,
        "webhook_template": "{alarm_type} detected in {system} at {time} (session #{count})",
    },
    "hotkeys": DEFAULT_HOTKEYS,
    "sounds": {
        "alarm": "",  # empty = use bundled alarm.wav
        "faction": "",  # empty = use bundled faction.wav
    },
    "profiles": {},  # {name: {full settings snapshot}}
    "image_thresholds": {},  # {basename: int 0-100 or None} — None means use global
    "intelligence": {
        "zkillboard_enabled": False,  # fetch recent kills on Enemy alarm
        "zkillboard_cooldown": 300,  # seconds between lookups for the same system
        "intel_log_enabled": False,  # tail EVE intel channel chat log
        "intel_log_channel": "",  # partial channel name to watch (e.g. "Intel")
    },
    # Per-type sound cooldowns (seconds before re-alarming after MAX_SOUND_TRIGGERS)
    "cooldown_timer_enemy": {"value": DEFAULT_COOLDOWN_TIMER},
    "cooldown_timer_faction": {"value": DEFAULT_COOLDOWN_TIMER},
    # Webhook notification settings
    "webhooks": {
        "enemy": {"url": "", "min_count": 0},  # URL = "" disables
        "faction": {"url": "", "min_count": 0},
    },
    # ESI character lookup (augments vision alarms with corp/alliance info)
    "esi": {
        "enabled": False,
        "show_corp": True,
        "show_alliance": True,
        "alert_flashy": False,  # v3.1: flag pilots with sec status ≤ -5
    },
    # v3.1: user-configurable threat tier list {name_substring: "red"|"orange"|"yellow"}
    "threat_tiers": {},
    # Plugin system
    "plugins": {
        "enabled": True,  # scan user plugins dir on start
    },
    # Web status UI
    "web_ui": {
        "enabled": False,
        "port": 8765,
    },
    # v3.2: adjacent system awareness
    "adjacent": {
        "enabled": False,
        "max_jumps": 3,
        "poll_interval": 120,  # seconds between polls
        "min_kills": 1,  # minimum kills in 15 min to alert
        "destination_system": "",  # for route threat assessment
    },
    # v3.3: D-scan monitoring
    "dscan": {
        "enabled": False,
        "alert_red": True,  # fire alarm on RED ships
        "alert_orange": False,  # fire alarm on ORANGE ships
        "alert_probes": True,  # fire alarm on probe detection
    },
    # v3.4: KOS checker
    "kos": {
        "cva_enabled": True,  # query CVA KOS API
        "custom_urls": [],  # list of additional KOS API URLs
    },
    # v3.4: local hostile list {name_or_corp_substring: "red"|"orange"|"yellow"}
    # (already in threat_tiers — kos.local_list is a flat list of always-KOS names)
    "kos_list": [],  # list of exact pilot/corp/alliance names that are KOS
    # v3.5: push notifications
    "push": {
        "telegram_token": "",
        "telegram_chat_id": "",
        "pushover_user": "",
        "pushover_token": "",
        "ntfy_url": "",
    },
    # v3.5: auto-screenshot + alarm escalation
    "notifications": {
        "auto_screenshot": False,  # capture alert region on alarm
        "escalation_threshold": 0,  # escalate if hostile count >= this (0 = off)
    },
    # v3.6: wormhole awareness
    "wormhole": {
        "thera_enabled": False,
        "thera_max_jumps": 5,
        "wh_drop_enabled": False,
        "wh_drop_threshold": 3,
    },
    # v3.7: fleet context + killmail tracking
    "fleet": {
        "composition_enabled": False,  # analyse fleet when 3+ hostiles
        "killmail_enabled": False,  # monitor tracked characters for kills
        "tracked_character_ids": [],  # list of ESI character IDs to track
    },
    # v4.0: ESI OAuth
    "esi_oauth": {
        "client_id": "",  # leave blank for built-in public client
        "standings_auto_classify": False,  # auto-tier standing contacts in Local
        "fleet_monitor": False,  # display fleet membership on start
        "structure_alerts": False,  # warn on low-fuel structures
    },
    # v4.1: OCR pilot-name detection on alarm (#98)
    "ocr": {
        "enabled": False,  # off by default; requires Tesseract installed
        "region": {"x1": 0, "y1": 0, "x2": 0, "y2": 0},  # 0s = use alert region
    },
    # v4.2: diagnostic / verbose logging mode
    "diagnostics": {
        "enabled": False,  # when True, all app loggers run at DEBUG level
    },
}


# ---------------------------------------------------------------------------
# Declarative field registry (#107)
#
# Each FieldSpec drives BUILD (create widget in a tab section), APPLY
# (settings dict -> widget) and SAVE (widget -> settings dict) for a simple
# scalar setting, so adding a new toggle/entry touches exactly one place.
# Nested/list/bespoke sections (regions, sliders, sounds, webhooks, threat
# tiers, profiles, kos.custom_urls, fleet.tracked_character_ids, OAuth login)
# stay hand-written in create_menu/apply_settings/save.
# ---------------------------------------------------------------------------
FieldSpec = namedtuple("FieldSpec", "path kind tab section label attr default")

FIELDS: list = [
    # --- Detection tab ---------------------------------------------------
    FieldSpec(
        "dscan.enabled",
        "bool",
        "Detection",
        "D-Scan Monitor",
        "Enable D-scan log monitoring",
        "dscan_enabled_var",
        False,
    ),
    FieldSpec(
        "dscan.alert_red",
        "bool",
        "Detection",
        "D-Scan Monitor",
        "Alert on RED ships",
        "dscan_red_var",
        True,
    ),
    FieldSpec(
        "dscan.alert_orange",
        "bool",
        "Detection",
        "D-Scan Monitor",
        "Alert on ORANGE ships",
        "dscan_orange_var",
        False,
    ),
    FieldSpec(
        "dscan.alert_probes",
        "bool",
        "Detection",
        "D-Scan Monitor",
        "Alert on probes detected",
        "dscan_probes_var",
        True,
    ),
    # --- Alerts & Sound tab ----------------------------------------------
    FieldSpec(
        "notifications.auto_screenshot",
        "bool",
        "Alerts & Sound",
        "Alarm Options",
        "Auto-screenshot on alarm",
        "auto_screenshot_var",
        False,
    ),
    FieldSpec(
        "notifications.escalation_threshold",
        "int",
        "Alerts & Sound",
        "Alarm Options",
        "Escalate at N hostiles (0 = off)",
        "escalation_threshold_entry",
        0,
    ),
    # --- Intel & ESI tab -------------------------------------------------
    FieldSpec(
        "intelligence.zkillboard_enabled",
        "bool",
        "Intel & ESI",
        "Intelligence",
        "Enable Zkillboard lookup on alarm",
        "zkillboard_var",
        False,
    ),
    FieldSpec(
        "intelligence.intel_log_enabled",
        "bool",
        "Intel & ESI",
        "Intelligence",
        "Watch EVE intel chat log",
        "intel_log_var",
        False,
    ),
    FieldSpec(
        "intelligence.intel_log_channel",
        "str",
        "Intel & ESI",
        "Intelligence",
        "Intel Channel",
        "intel_channel_entry",
        "",
    ),
    FieldSpec(
        "esi.enabled",
        "bool",
        "Intel & ESI",
        "ESI Augmentation",
        "Show corp/alliance on Enemy alarm",
        "esi_enabled_var",
        False,
    ),
    FieldSpec(
        "esi.show_corp",
        "bool",
        "Intel & ESI",
        "ESI Augmentation",
        "Show corporation",
        "esi_corp_var",
        True,
    ),
    FieldSpec(
        "esi.show_alliance",
        "bool",
        "Intel & ESI",
        "ESI Augmentation",
        "Show alliance",
        "esi_alliance_var",
        True,
    ),
    FieldSpec(
        "esi.alert_flashy",
        "bool",
        "Intel & ESI",
        "ESI Augmentation",
        "Alert on flashy pilots (sec status \u2264 -5)",
        "esi_flashy_var",
        False,
    ),
    FieldSpec(
        "kos.cva_enabled",
        "bool",
        "Intel & ESI",
        "KOS Checker",
        "Enable CVA KOS API",
        "kos_cva_var",
        True,
    ),
    FieldSpec(
        "adjacent.enabled",
        "bool",
        "Intel & ESI",
        "Adjacent System Monitor",
        "Monitor kills in neighboring systems",
        "adjacent_enabled_var",
        False,
    ),
    FieldSpec(
        "adjacent.max_jumps",
        "int",
        "Intel & ESI",
        "Adjacent System Monitor",
        "Max jumps",
        "adjacent_max_jumps_entry",
        3,
    ),
    FieldSpec(
        "adjacent.min_kills",
        "int",
        "Intel & ESI",
        "Adjacent System Monitor",
        "Min kills",
        "adjacent_min_kills_entry",
        1,
    ),
    FieldSpec(
        "adjacent.poll_interval",
        "int",
        "Intel & ESI",
        "Adjacent System Monitor",
        "Poll interval (s)",
        "adjacent_poll_entry",
        120,
    ),
    FieldSpec(
        "adjacent.destination_system",
        "str",
        "Intel & ESI",
        "Adjacent System Monitor",
        "Destination",
        "adjacent_dest_entry",
        "",
    ),
    FieldSpec(
        "esi_oauth.client_id",
        "str",
        "Intel & ESI",
        "EVE SSO / ESI OAuth",
        "Client ID",
        "esi_client_id_entry",
        "",
    ),
    FieldSpec(
        "esi_oauth.standings_auto_classify",
        "bool",
        "Intel & ESI",
        "EVE SSO / ESI OAuth",
        "Auto-classify standing contacts in Local",
        "esi_standings_var",
        False,
    ),
    FieldSpec(
        "esi_oauth.fleet_monitor",
        "bool",
        "Intel & ESI",
        "EVE SSO / ESI OAuth",
        "Display fleet membership on start",
        "esi_fleet_var",
        False,
    ),
    FieldSpec(
        "esi_oauth.structure_alerts",
        "bool",
        "Intel & ESI",
        "EVE SSO / ESI OAuth",
        "Warn on structure fuel < 7 days",
        "esi_structure_var",
        False,
    ),
    FieldSpec(
        "ocr.enabled",
        "bool",
        "Intel & ESI",
        "OCR Name Detection",
        "Read pilot names from Local on alarm (needs Tesseract)",
        "ocr_enabled_var",
        False,
    ),
    FieldSpec(
        "ocr.region.x1",
        "int",
        "Intel & ESI",
        "OCR Name Detection",
        "Region X1 (0 = use alert region)",
        "ocr_x1_entry",
        0,
    ),
    FieldSpec(
        "ocr.region.y1",
        "int",
        "Intel & ESI",
        "OCR Name Detection",
        "Region Y1",
        "ocr_y1_entry",
        0,
    ),
    FieldSpec(
        "ocr.region.x2",
        "int",
        "Intel & ESI",
        "OCR Name Detection",
        "Region X2",
        "ocr_x2_entry",
        0,
    ),
    FieldSpec(
        "ocr.region.y2",
        "int",
        "Intel & ESI",
        "OCR Name Detection",
        "Region Y2",
        "ocr_y2_entry",
        0,
    ),
    # --- Notifications tab -----------------------------------------------
    FieldSpec(
        "push.telegram_token",
        "str",
        "Notifications",
        "Push Notifications",
        "Telegram Token",
        "telegram_token_entry",
        "",
    ),
    FieldSpec(
        "push.telegram_chat_id",
        "str",
        "Notifications",
        "Push Notifications",
        "Telegram Chat ID",
        "telegram_chat_entry",
        "",
    ),
    FieldSpec(
        "push.pushover_user",
        "str",
        "Notifications",
        "Push Notifications",
        "Pushover User",
        "pushover_user_entry",
        "",
    ),
    FieldSpec(
        "push.pushover_token",
        "str",
        "Notifications",
        "Push Notifications",
        "Pushover Token",
        "pushover_token_entry",
        "",
    ),
    FieldSpec(
        "push.ntfy_url",
        "str",
        "Notifications",
        "Push Notifications",
        "ntfy.sh URL",
        "ntfy_url_entry",
        "",
    ),
    FieldSpec(
        "web_ui.enabled",
        "bool",
        "Notifications",
        "Web Status UI",
        "Enable web status server (localhost)",
        "web_ui_var",
        False,
    ),
    FieldSpec(
        "web_ui.port",
        "int",
        "Notifications",
        "Web Status UI",
        "Port",
        "web_ui_port_entry",
        8765,
    ),
    # --- Wormhole & Fleet tab --------------------------------------------
    FieldSpec(
        "wormhole.thera_enabled",
        "bool",
        "Wormhole & Fleet",
        "Wormhole Awareness",
        "Monitor Thera connections (Eve-Scout)",
        "thera_enabled_var",
        False,
    ),
    FieldSpec(
        "wormhole.thera_max_jumps",
        "int",
        "Wormhole & Fleet",
        "Wormhole Awareness",
        "Thera max jumps",
        "thera_max_jumps_entry",
        5,
    ),
    FieldSpec(
        "wormhole.wh_drop_enabled",
        "bool",
        "Wormhole & Fleet",
        "Wormhole Awareness",
        "Alert on WH drop pattern",
        "wh_drop_enabled_var",
        False,
    ),
    FieldSpec(
        "wormhole.wh_drop_threshold",
        "int",
        "Wormhole & Fleet",
        "Wormhole Awareness",
        "Drop threshold (pilots)",
        "wh_drop_threshold_entry",
        3,
    ),
    FieldSpec(
        "fleet.composition_enabled",
        "bool",
        "Wormhole & Fleet",
        "Fleet Context",
        "Analyse fleet composition (3+ hostiles)",
        "fleet_composition_var",
        False,
    ),
    FieldSpec(
        "fleet.killmail_enabled",
        "bool",
        "Wormhole & Fleet",
        "Fleet Context",
        "Notify on tracked character kills/losses",
        "fleet_killmail_var",
        False,
    ),
    # --- Diagnostics section (Alerts & Sound tab) -------------------------
    FieldSpec(
        "diagnostics.enabled",
        "bool",
        "Alerts & Sound",
        "Diagnostics",
        "Enable diagnostic (verbose) logging",
        "diagnostics_enabled_var",
        False,
    ),
]

# Tab display order for the CTkTabview.
TAB_ORDER = [
    "Detection",
    "Alerts & Sound",
    "Intel & ESI",
    "Notifications",
    "Wormhole & Fleet",
]


def _set_by_path(settings: dict, path: str, value) -> None:
    """Set a dotted-path leaf, creating intermediate dicts as needed."""
    parts = path.split(".")
    node = settings
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _get_by_path(settings: dict, path: str, default=None):
    node = settings
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


class SettingMenu:
    """Setting menu for the Alert System."""

    def __init__(self, main: "MainMenu"):
        self.main = main
        self.open = False
        self.default = DEFAULT_SETTINGS
        self.changed = False
        self._window_created = False

        # BooleanVar/StringVar don't depend on a window so can be created here
        self.play_alarm = customtkinter.BooleanVar()
        self.log_level_var = customtkinter.StringVar(value="INFO")

    def _ensure_window(self) -> None:
        """Create the CTkToplevel and all widgets on first call (lazy init)."""
        if self._window_created:
            return
        self._window_created = True

        self.setting_window = customtkinter.CTkToplevel(self.main)
        self.setting_window.title("Settings")
        self.setting_window.withdraw()

        self.create_menu()

    @property
    def is_changed(self):
        """Returns True if the settings have been changed."""
        return self.changed

    @property
    def is_open(self):
        """Returns True if the settings window is open."""
        return self.open

    def load_settings(self):
        config_path = get_settings_path()
        try:
            with open(config_path, encoding="utf-8") as config_file:
                settings = json.load(config_file)
                settings = self.merge_settings_with_defaults(settings)
        except (OSError, json.JSONDecodeError):
            logger.debug(
                "Setting Menu: Error reading settings file. Using default settings."
            )
            settings = self.default.copy()

        # If there is an active profile, overlay its values onto the base settings
        active = settings.get("active_profile", "Default")
        profiles = settings.get("profiles", {})
        if active in profiles:
            profile_data = profiles[active]
            for key, value in profile_data.items():
                if isinstance(value, dict) and isinstance(settings.get(key), dict):
                    settings[key] = {**settings[key], **value}
                else:
                    settings[key] = value

        # Widget mutation must run on the main Tkinter thread. AlertAgent.run()
        # and vision_check() call load_settings() from the alert daemon thread,
        # so dispatch the UI sync via after(0) rather than mutating widgets
        # cross-thread (Tkinter is not thread-safe — see #114 / COCO Rule 1).
        # The read+merge above is pure, so the returned dict is still available
        # synchronously to non-UI callers (e.g. AlertAgent).
        if self._window_created:
            self.main.after(0, lambda s=settings: self.apply_settings(s))
        return settings

    def merge_settings_with_defaults(self, settings, defaults=None):
        """Merge loaded settings with defaults recursively.

        Preserves user-only keys that have no counterpart in
        DEFAULT_SETTINGS (saved profiles, per-image thresholds, kos_list
        entries, custom threat tiers, …) and fills in default keys missing
        from the loaded settings. Crucially, a default value of ``{}`` no
        longer wipes the user's populated sub-dict — the previous
        implementation iterated only over ``defaults`` and returned ``{}``
        for any key whose default was an empty dict, silently deleting all
        data stored under it (see issues #99/#108).
        """
        if defaults is None:
            defaults = self.default

        merged_settings = {}
        for key in set(defaults) | set(settings):
            if key in defaults and key in settings:
                dval = defaults[key]
                sval = settings[key]
                if isinstance(dval, dict) and isinstance(sval, dict):
                    # Recursively merge nested dictionaries
                    merged_settings[key] = self.merge_settings_with_defaults(sval, dval)
                else:
                    merged_settings[key] = sval
            elif key in settings:
                # User-only key with no default (e.g. a saved profile or a
                # per-image threshold entry) — keep it verbatim.
                merged_settings[key] = settings[key]
            else:
                # Default-only key (e.g. a newly added feature block) —
                # deep-copy so callers can't mutate DEFAULT_SETTINGS.
                merged_settings[key] = copy.deepcopy(defaults[key])

        return merged_settings

    def _activate_webhook(self, webhookurl):
        """Activate the webhook URL."""
        try:
            required_prefix = "https://discord.com/api/webhooks/"
            if not webhookurl.startswith(required_prefix):
                raise ValueError(f"It must start with '{required_prefix}'.")
            self.main.webhook = Webhook(
                webhookurl,
                username="Gneuten",
                avatar_url="https://cdn.discordapp.com/avatars/990582360103870495/410d536127874481b9771b9eb9aa8104.png",
            )
            return True
        except ValueError as e:
            logger.error("Invalid webhook URL: %s", e)
            self.main.webhook = None
            return False
        except Exception as e:
            logger.error("Error activating webhook: %s", e)
            self.main.webhook = None
            return False

    def apply_settings(self, settings):
        self._ensure_window()
        try:
            # Profile selector — refresh dropdown values and selected profile
            profiles = settings.get("profiles", {})
            active = settings.get("active_profile", "Default")
            profile_names = list(profiles.keys()) or ["Default"]
            if active not in profile_names:
                profile_names.insert(0, active)
            self.profile_dropdown.configure(values=profile_names)
            self.profile_var.set(active)

            self.log_level_var.set(settings.get("log_level", "INFO"))

            self.alert_region_x_first.delete(0, customtkinter.END)
            self.alert_region_x_first.insert(0, settings["alert_region_1"]["x"])

            self.alert_region_y_first.delete(0, customtkinter.END)
            self.alert_region_y_first.insert(0, settings["alert_region_1"]["y"])

            self.alert_region_x_second.delete(0, customtkinter.END)
            self.alert_region_x_second.insert(0, settings["alert_region_2"]["x"])
            self.alert_region_y_second.delete(0, customtkinter.END)
            self.alert_region_y_second.insert(0, settings["alert_region_2"]["y"])

            self.faction_region_x_first.delete(0, customtkinter.END)
            self.faction_region_x_first.insert(0, settings["faction_region_1"]["x"])
            self.faction_region_y_first.delete(0, customtkinter.END)
            self.faction_region_y_first.insert(0, settings["faction_region_1"]["y"])

            self.faction_region_x_second.delete(0, customtkinter.END)
            self.faction_region_x_second.insert(0, settings["faction_region_2"]["x"])
            self.faction_region_y_second.delete(0, customtkinter.END)
            self.faction_region_y_second.insert(0, settings["faction_region_2"]["y"])

            self.detectionscale.set(settings["detectionscale"]["value"])
            self.slider_event(settings["detectionscale"]["value"])
            self.faction_scale.set(settings["faction_scale"]["value"])
            self.factionslider_event(settings["faction_scale"]["value"])

            self.cooldown_timer.delete(0, customtkinter.END)
            self.cooldown_timer.insert(0, settings["cooldown_timer"]["value"])

            self.cooldown_timer_enemy.delete(0, customtkinter.END)
            self.cooldown_timer_enemy.insert(
                0,
                settings.get("cooldown_timer_enemy", {}).get(
                    "value", DEFAULT_COOLDOWN_TIMER
                ),
            )
            self.cooldown_timer_faction_entry.delete(0, customtkinter.END)
            self.cooldown_timer_faction_entry.insert(
                0,
                settings.get("cooldown_timer_faction", {}).get(
                    "value", DEFAULT_COOLDOWN_TIMER
                ),
            )

            self.volume_scale.set(settings["volume"]["value"])
            self.volumeslider_event(settings["volume"]["value"])

            self.system_name.delete(0, customtkinter.END)
            self.system_name.insert(0, settings["server"]["system"])

            self.webhook.delete(0, customtkinter.END)

            # Check if the webhook URL is valid
            if settings["server"]["webhook"]:
                self._activate_webhook(settings["server"]["webhook"])

            self.webhook.insert(0, settings["server"]["webhook"])
            self.play_alarm.set(settings["server"]["mute"])

            # Webhook template
            self.webhook_template_entry.delete(0, customtkinter.END)
            self.webhook_template_entry.insert(
                0,
                settings["server"].get(
                    "webhook_template", DEFAULT_SETTINGS["server"]["webhook_template"]
                ),
            )

            # Per-type webhook targets
            webhooks = settings.get("webhooks", {})
            self.enemy_webhook_entry.delete(0, customtkinter.END)
            self.enemy_webhook_entry.insert(0, webhooks.get("enemy", {}).get("url", ""))
            self.enemy_webhook_mincount.delete(0, customtkinter.END)
            self.enemy_webhook_mincount.insert(
                0, str(webhooks.get("enemy", {}).get("min_count", 0))
            )
            self.faction_webhook_entry.delete(0, customtkinter.END)
            self.faction_webhook_entry.insert(
                0, webhooks.get("faction", {}).get("url", "")
            )
            self.faction_webhook_mincount.delete(0, customtkinter.END)
            self.faction_webhook_mincount.insert(
                0, str(webhooks.get("faction", {}).get("min_count", 0))
            )

            # Hotkeys
            hotkeys = settings.get("hotkeys", {})
            self.hotkey_alert_entry.delete(0, customtkinter.END)
            self.hotkey_alert_entry.insert(0, hotkeys.get("alert_region", "f1"))
            self.hotkey_faction_entry.delete(0, customtkinter.END)
            self.hotkey_faction_entry.insert(0, hotkeys.get("faction_region", "f2"))

            # Sounds
            sounds = settings.get("sounds", {})
            self._alarm_sound_path = sounds.get("alarm", "")
            self._faction_sound_path = sounds.get("faction", "")
            self._update_sound_labels()

            # Threat tiers (bespoke — dict of name -> tier)
            tiers = settings.get("threat_tiers", {})
            self._threat_tiers_data = dict(tiers)
            self._refresh_threat_tiers_list()

            # Registry-driven scalar fields (intelligence/esi/dscan/kos.cva/
            # push/notifications/wormhole/fleet/web_ui/adjacent/esi_oauth)
            self._apply_registry_fields(settings)

            # List-valued fields (bespoke)
            kos = settings.get("kos", {})
            self.kos_custom_entry.delete(0, customtkinter.END)
            self.kos_custom_entry.insert(0, ", ".join(kos.get("custom_urls", [])))
            fleet = settings.get("fleet", {})
            self.fleet_char_ids_entry.delete(0, customtkinter.END)
            char_ids = fleet.get("tracked_character_ids", [])
            self.fleet_char_ids_entry.insert(0, ", ".join(str(c) for c in char_ids))

        except KeyError as e:
            logger.exception(e)
            self.main.write_message(
                "Setting Menu: Error reading settings file. read logs for more information",
                "red",
            )

    def save_settings(self, settings=None):
        if settings is None:
            settings = self.default

        config_path = get_settings_path()
        # Atomic write: write to a temp file then replace, so a crash
        # mid-write never corrupts the settings file.
        try:
            dir_name = os.path.dirname(config_path)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=dir_name, delete=False, suffix=".tmp"
            ) as tmp_file:
                json.dump(settings, tmp_file, indent=4)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_file.name, config_path)
        except OSError as e:
            logger.error("Failed to save settings: %s", e)
            return

        self.apply_settings(settings)
        self.changed = True

    def _read_saved_settings(self):
        """Read + merge the on-disk settings WITHOUT applying to the UI or
        overlaying the active profile.

        Used as the base for :meth:`save` so that keys which have no
        Settings-window widget (``profiles``, ``image_thresholds``,
        ``active_profile``, ``kos_list``, …) are preserved rather than
        reset to their defaults on every save (issues #99/#108).
        """
        config_path = get_settings_path()
        try:
            with open(config_path, encoding="utf-8") as config_file:
                return self.merge_settings_with_defaults(json.load(config_file))
        except (OSError, json.JSONDecodeError):
            return copy.deepcopy(self.default)

    def save(self):
        """Save settings to disk only (does not apply to running system)."""
        try:
            # Start from the persisted settings (not DEFAULT_SETTINGS) so
            # non-widget keys — saved profiles, per-image thresholds,
            # active profile, kos_list — survive the save.
            settings = self._read_saved_settings()
            settings.update(
                {
                    "log_level": self.log_level_var.get(),
                    "alert_region_1": {
                        "x": int(self.alert_region_x_first.get()),
                        "y": int(self.alert_region_y_first.get()),
                    },
                    "alert_region_2": {
                        "x": int(self.alert_region_x_second.get()),
                        "y": int(self.alert_region_y_second.get()),
                    },
                    "faction_region_1": {
                        "x": int(self.faction_region_x_first.get()),
                        "y": int(self.faction_region_y_first.get()),
                    },
                    "faction_region_2": {
                        "x": int(self.faction_region_x_second.get()),
                        "y": int(self.faction_region_y_second.get()),
                    },
                    "detectionscale": {"value": int(self.detectionscale.get())},
                    "faction_scale": {"value": int(self.faction_scale.get())},
                    "cooldown_timer": {"value": int(self.cooldown_timer.get())},
                    "cooldown_timer_enemy": {
                        "value": int(
                            self.cooldown_timer_enemy.get() or DEFAULT_COOLDOWN_TIMER
                        )
                    },
                    "cooldown_timer_faction": {
                        "value": int(
                            self.cooldown_timer_faction_entry.get()
                            or DEFAULT_COOLDOWN_TIMER
                        )
                    },
                    "volume": {"value": int(self.volume_scale.get())},
                    "server": {
                        "webhook": self.webhook.get(),
                        "system": self.system_name.get(),
                        "mute": self.play_alarm.get(),
                        "webhook_template": self.webhook_template_entry.get().strip()
                        or DEFAULT_SETTINGS["server"]["webhook_template"],
                    },
                    "hotkeys": {
                        "alert_region": self.hotkey_alert_entry.get().strip().lower()
                        or "f1",
                        "faction_region": self.hotkey_faction_entry.get()
                        .strip()
                        .lower()
                        or "f2",
                    },
                    "sounds": {
                        "alarm": getattr(self, "_alarm_sound_path", ""),
                        "faction": getattr(self, "_faction_sound_path", ""),
                    },
                    "webhooks": {
                        "enemy": {
                            "url": self.enemy_webhook_entry.get().strip(),
                            "min_count": int(self.enemy_webhook_mincount.get() or 0),
                        },
                        "faction": {
                            "url": self.faction_webhook_entry.get().strip(),
                            "min_count": int(self.faction_webhook_mincount.get() or 0),
                        },
                    },
                    "threat_tiers": dict(getattr(self, "_threat_tiers_data", {})),
                }
            )
            # Registry-driven scalar fields (intelligence/esi/dscan/kos.cva/
            # push/notifications/wormhole/fleet/web_ui/adjacent/esi_oauth).
            self._save_registry_fields(settings)
            # List-valued fields (bespoke)
            settings.setdefault("kos", {})["custom_urls"] = [
                u.strip() for u in self.kos_custom_entry.get().split(",") if u.strip()
            ]
            settings.setdefault("fleet", {})["tracked_character_ids"] = [
                int(c.strip())
                for c in self.fleet_char_ids_entry.get().split(",")
                if c.strip().isdigit()
            ]

            self.save_settings(settings)
            self.main.write_message("Settings: Saved to disk.", "green")
        except ValueError as e:
            self.main.write_message(
                "Setting Menu: Error saving settings. Please check the values.", "red"
            )
            logger.error(e)

    def apply_settings_runtime(self):
        """Apply settings to the running system without restart."""
        try:
            # Validate settings first
            # pylint: disable=import-outside-toplevel
            from evealert.settings.validator import ConfigValidator

            detection_scale = int(self.detectionscale.get())
            faction_scale = int(self.faction_scale.get())
            cooldown = int(self.cooldown_timer.get())
            volume = int(self.volume_scale.get())
            mute = self.play_alarm.get()

            # Validate detection scales
            is_valid, error = ConfigValidator.validate_detection_scale(detection_scale)
            if not is_valid:
                self.main.write_message(f"Validation Error: {error}", "red")
                return

            is_valid, error = ConfigValidator.validate_detection_scale(faction_scale)
            if not is_valid:
                self.main.write_message(f"Validation Error: {error}", "red")
                return

            # Validate cooldown
            is_valid, error = ConfigValidator.validate_cooldown_timer(cooldown)
            if not is_valid:
                self.main.write_message(f"Validation Error: {error}", "red")
                return

            # Apply to AlertAgent if running
            if self.main.alert:
                self.main.alert.detection = detection_scale
                self.main.alert.detection_faction = faction_scale
                self.main.alert.cooldowntimer = cooldown
                self.main.alert.volume = volume / 100.0  # Convert to 0.0-1.0
                self.main.alert.mute = mute

                # Update webhook if changed
                webhook_url = self.webhook.get()
                if webhook_url:
                    self._activate_webhook(webhook_url)
                else:
                    self.main.webhook = None

                self.main.write_message("Settings: Applied to running system.", "green")
                logger.info(
                    "Runtime settings applied: detection=%d, faction_scale=%d, cooldown=%d, mute=%s",
                    detection_scale,
                    faction_scale,
                    cooldown,
                    mute,
                )
            else:
                self.main.write_message(
                    "Settings: No running system to apply to.", "yellow"
                )

        except ValueError as e:
            self.main.write_message(
                "Setting Menu: Invalid values. Please check your input.", "red"
            )
            logger.error("Runtime settings apply error: %s", e)

    def _open_threshold_editor(self) -> None:
        """Open the per-image threshold editor window."""
        from evealert.menu.threshold_editor import (  # pylint: disable=import-outside-toplevel
            ThresholdEditorWindow,
        )

        ThresholdEditorWindow(self.main)

    # ------------------------------------------------------------------
    # Threat tier list helpers
    # ------------------------------------------------------------------

    def _refresh_threat_tiers_list(self) -> None:
        """Rebuild the scrollable threat tiers list from self._threat_tiers_data."""
        for widget in self.threat_tiers_list.winfo_children():
            widget.destroy()
        self._threat_tier_rows = []
        self._selected_tier_key = None

        for name, tier in sorted(self._threat_tiers_data.items()):
            row = customtkinter.CTkFrame(self.threat_tiers_list)
            row.pack(fill="x", pady=1)
            colour = {"red": "#8B0000", "orange": "#804000", "yellow": "#806000"}.get(
                tier, "#333"
            )
            customtkinter.CTkLabel(
                row,
                text=f"[{tier.upper()}]",
                width=65,
                fg_color=colour,
                corner_radius=4,
            ).pack(side="left", padx=(4, 6))
            lbl = customtkinter.CTkLabel(row, text=name, anchor="w")
            lbl.pack(side="left", fill="x", expand=True)
            lbl.bind("<Button-1>", lambda e, k=name: self._select_tier_row(k))
            row.bind("<Button-1>", lambda e, k=name: self._select_tier_row(k))
            self._threat_tier_rows.append((name, row))

    def _select_tier_row(self, key: str) -> None:
        self._selected_tier_key = key

    def _add_threat_tier(self) -> None:
        name = self.threat_tier_name_entry.get().strip()
        tier = self.threat_tier_level_var.get()
        if not name:
            return
        self._threat_tiers_data[name] = tier
        self.threat_tier_name_entry.delete(0, customtkinter.END)
        self._refresh_threat_tiers_list()

    def _remove_threat_tier(self) -> None:
        key = self._selected_tier_key
        if key and key in self._threat_tiers_data:
            del self._threat_tiers_data[key]
            self._refresh_threat_tiers_list()

    # ------------------------------------------------------------------
    # Route check helper (v3.2)
    # ------------------------------------------------------------------

    def _export_diagnostics_bundle(self) -> None:
        """Create a zip bundle of logs + redacted settings and report its path."""
        try:
            from evealert.settings.diagnostics import (  # pylint: disable=import-outside-toplevel
                create_bundle,
            )

            settings = self._read_saved_settings()
            bundle_path = create_bundle(settings)
            self.main.write_message(f"Diagnostics bundle saved: {bundle_path}", "cyan")
            # Best-effort: open the containing folder so the user can find the file
            import subprocess  # noqa: E401  pylint: disable=import-outside-toplevel,multiple-imports
            import sys

            try:
                if sys.platform == "win32":
                    subprocess.Popen(["explorer", "/select,", str(bundle_path)])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", "-R", str(bundle_path)])
                else:
                    subprocess.Popen(["xdg-open", str(bundle_path.parent)])
            except Exception:
                pass  # Folder reveal is best-effort; the path is in the message
        except Exception as exc:
            logger.error("Failed to create diagnostics bundle: %s", exc)
            self.main.write_message(f"Diagnostics bundle failed: {exc}", "red")

    # ------------------------------------------------------------------

    def _check_route(self) -> None:
        """Trigger an async route threat assessment from the current system to the destination."""
        if not (
            hasattr(self.main, "alert")
            and self.main.alert
            and self.main.alert.is_running
        ):
            self.main.write_message("Route check: start detection first.", "yellow")
            return
        dest = self.adjacent_dest_entry.get().strip()
        if not dest:
            self.main.write_message(
                "Route check: enter a destination system.", "yellow"
            )
            return
        origin = self.system_name.get().strip()
        if not origin or origin == "Enter a System Name":
            self.main.write_message(
                "Route check: configure your current system in Settings.", "yellow"
            )
            return
        # Schedule the route check on the alert event loop
        loop = self.main.alert.loop
        if loop and loop.is_running():
            import asyncio  # pylint: disable=import-outside-toplevel

            asyncio.run_coroutine_threadsafe(
                self.main.alert._run_route_check(origin, dest), loop
            )

    # ------------------------------------------------------------------
    # Profile management helpers
    # ------------------------------------------------------------------

    def _current_settings_snapshot(self) -> dict:
        """Return a dict of the current UI values for profile storage."""
        return {
            "alert_region_1": {
                "x": int(self.alert_region_x_first.get()),
                "y": int(self.alert_region_y_first.get()),
            },
            "alert_region_2": {
                "x": int(self.alert_region_x_second.get()),
                "y": int(self.alert_region_y_second.get()),
            },
            "faction_region_1": {
                "x": int(self.faction_region_x_first.get()),
                "y": int(self.faction_region_y_first.get()),
            },
            "faction_region_2": {
                "x": int(self.faction_region_x_second.get()),
                "y": int(self.faction_region_y_second.get()),
            },
            "detectionscale": {"value": int(self.detectionscale.get())},
            "faction_scale": {"value": int(self.faction_scale.get())},
            "cooldown_timer": {"value": int(self.cooldown_timer.get())},
            "volume": {"value": int(self.volume_scale.get())},
            "server": {
                "webhook": self.webhook.get(),
                "system": self.system_name.get(),
                "mute": self.play_alarm.get(),
            },
            "hotkeys": {
                "alert_region": self.hotkey_alert_entry.get().strip().lower() or "f1",
                "faction_region": self.hotkey_faction_entry.get().strip().lower()
                or "f2",
            },
            "sounds": {
                "alarm": getattr(self, "_alarm_sound_path", ""),
                "faction": getattr(self, "_faction_sound_path", ""),
            },
        }

    def _save_profile(self) -> None:
        """Save the current UI values to the selected profile."""
        settings = self.load_settings()
        name = self.profile_var.get().strip()
        if not name:
            return
        profiles = settings.setdefault("profiles", {})
        profiles[name] = self._current_settings_snapshot()
        settings["active_profile"] = name
        self.save_settings(settings)
        self.main.write_message(f"Profile '{name}' saved.", "green")

    def _new_profile(self) -> None:
        """Prompt for a name and save current values as a new profile."""
        dialog = customtkinter.CTkInputDialog(
            text="Enter profile name:", title="New Profile"
        )
        name = dialog.get_input()
        if not name or not name.strip():
            return
        name = name.strip()
        settings = self.load_settings()
        profiles = settings.setdefault("profiles", {})
        profiles[name] = self._current_settings_snapshot()
        settings["active_profile"] = name
        self.save_settings(settings)
        self.main.write_message(f"Profile '{name}' created.", "green")

    def _load_profile(self) -> None:
        """Load the selected profile and apply it to the UI."""
        name = self.profile_var.get()
        settings = self.load_settings()
        if name not in settings.get("profiles", {}):
            self.main.write_message(f"Profile '{name}' not found.", "red")
            return
        settings["active_profile"] = name
        self.save_settings(settings)
        self.main.write_message(f"Profile '{name}' loaded.", "green")

    def _delete_profile(self) -> None:
        """Delete the selected profile (cannot delete last profile)."""
        name = self.profile_var.get()
        settings = self.load_settings()
        profiles = settings.get("profiles", {})
        if name not in profiles:
            return
        if len(profiles) <= 1:
            self.main.write_message("Cannot delete the last profile.", "red")
            return
        del profiles[name]
        # Switch active profile to the first remaining one
        settings["active_profile"] = next(iter(profiles))
        self.save_settings(settings)
        self.main.write_message(f"Profile '{name}' deleted.", "green")

    # ------------------------------------------------------------------
    # Sound library helpers
    # ------------------------------------------------------------------

    def _update_sound_labels(self) -> None:
        """Refresh the sound filename labels after a browse or clear."""
        if not self._window_created:
            return
        alarm_path = getattr(self, "_alarm_sound_path", "")
        faction_path = getattr(self, "_faction_sound_path", "")
        self.alarm_sound_label.configure(
            text=os.path.basename(alarm_path) if alarm_path else "Default (alarm.wav)"
        )
        self.faction_sound_label.configure(
            text=(
                os.path.basename(faction_path)
                if faction_path
                else "Default (faction.wav)"
            )
        )

    def _browse_alarm_sound(self) -> None:
        """Open a file dialog to pick a custom alarm WAV."""
        import tkinter.filedialog  # pylint: disable=import-outside-toplevel

        path = tkinter.filedialog.askopenfilename(
            title="Select Alarm Sound",
            filetypes=[("WAV files", "*.wav"), ("All files", "*.*")],
        )
        if path:
            self._alarm_sound_path = path
            self._update_sound_labels()

    def _clear_alarm_sound(self) -> None:
        self._alarm_sound_path = ""
        self._update_sound_labels()

    def _browse_faction_sound(self) -> None:
        """Open a file dialog to pick a custom faction WAV."""
        import tkinter.filedialog  # pylint: disable=import-outside-toplevel

        path = tkinter.filedialog.askopenfilename(
            title="Select Faction Sound",
            filetypes=[("WAV files", "*.wav"), ("All files", "*.*")],
        )
        if path:
            self._faction_sound_path = path
            self._update_sound_labels()

    def _clear_faction_sound(self) -> None:
        self._faction_sound_path = ""
        self._update_sound_labels()

    # ------------------------------------------------------------------
    # ESI OAuth helpers (v4.0)
    # ------------------------------------------------------------------

    def _esi_refresh_status(self) -> None:
        """Update the login status label from the EsiAuth singleton."""
        try:
            from evealert.tools.esi_auth import (  # pylint: disable=import-outside-toplevel
                get_esi_auth,
            )

            auth = get_esi_auth()
            if auth.is_authenticated:
                self._esi_status_label.configure(
                    text=f"Logged in as: {auth.character_name}", text_color="green"
                )
            else:
                self._esi_status_label.configure(
                    text="Not logged in", text_color="gray"
                )
        except Exception:
            pass

    def _esi_login(self) -> None:
        """Start the EVE SSO login flow in the background."""
        try:
            import threading  # pylint: disable=import-outside-toplevel

            from evealert.tools.esi_auth import (  # pylint: disable=import-outside-toplevel
                get_esi_auth,
            )

            auth = get_esi_auth(client_id=self.esi_client_id_entry.get().strip())

            def _run_login():
                import asyncio  # pylint: disable=import-outside-toplevel

                try:
                    loop = asyncio.new_event_loop()
                    result = loop.run_until_complete(auth.login())
                    loop.close()
                    if result:
                        self._esi_refresh_status()
                except Exception as exc:
                    logger.debug("ESI login error: %s", exc)

            threading.Thread(target=_run_login, daemon=True).start()
            self._esi_status_label.configure(
                text="Browser opened — authorise in EVE Online...", text_color="yellow"
            )
        except Exception as exc:
            logger.debug("ESI login start error: %s", exc)

    def _esi_logout(self) -> None:
        """Log out and clear the stored ESI token."""
        try:
            from evealert.tools.esi_auth import (  # pylint: disable=import-outside-toplevel
                get_esi_auth,
            )

            get_esi_auth().logout()
            self._esi_refresh_status()
        except Exception as exc:
            logger.debug("ESI logout error: %s", exc)

    def clean_up(self):
        """Cleans up the settings window."""
        if self.is_open:
            self.open = False
            self.main.mainmenu_buttons.setting_menu.configure(
                fg_color="#1f538d", hover_color="#14375e"
            )
            if self._window_created:
                self.setting_window.withdraw()

    # ------------------------------------------------------------------
    # Registry-driven scalar fields (#107)
    # ------------------------------------------------------------------

    def _build_registry_section(
        self, parent, tab: str, section: str, start_row: int
    ) -> int:
        """Build all FIELDS matching (tab, section) onto *parent* starting at
        *start_row*. Returns the next free row. Widgets are stored under their
        FieldSpec.attr so apply/save keep working by attribute name."""
        specs = [f for f in FIELDS if f.tab == tab and f.section == section]
        row = start_row
        for spec in specs:
            if spec.kind == "bool":
                var = customtkinter.BooleanVar(value=bool(spec.default))
                setattr(self, spec.attr, var)
                customtkinter.CTkCheckBox(parent, text=spec.label, variable=var).grid(
                    row=row, column=0, columnspan=3, padx=(20, 4), sticky="w", pady=3
                )
            else:  # int / str -> label + entry
                customtkinter.CTkLabel(
                    parent, text=f"{spec.label}:", justify="left"
                ).grid(row=row, column=0, padx=(20, 4), sticky="e")
                entry = customtkinter.CTkEntry(parent, width=260)
                setattr(self, spec.attr, entry)
                entry.grid(row=row, column=1, columnspan=2, sticky="w", pady=2)
            row += 1
        return row

    def _apply_registry_fields(self, settings: dict) -> None:
        """Populate registry-backed widgets from *settings* (settings -> widget)."""
        for spec in FIELDS:
            widget = getattr(self, spec.attr, None)
            if widget is None:
                continue
            value = _get_by_path(settings, spec.path, spec.default)
            if spec.kind == "bool":
                widget.set(bool(value))
            else:
                widget.delete(0, customtkinter.END)
                widget.insert(0, str(value))

    def _save_registry_fields(self, settings: dict) -> None:
        """Write registry-backed widget values into *settings* (widget -> settings)."""
        for spec in FIELDS:
            widget = getattr(self, spec.attr, None)
            if widget is None:
                continue
            if spec.kind == "bool":
                value = bool(widget.get())
            elif spec.kind == "int":
                raw = str(widget.get()).strip()
                value = int(raw) if raw.lstrip("-").isdigit() else int(spec.default)
            else:  # str
                value = str(widget.get()).strip()
            _set_by_path(settings, spec.path, value)

    def create_menu(self):
        """Build the settings window: profile header, tabbed scrollable body,
        and a persistent Save/Apply/Close footer (#107)."""

        win = self.setting_window
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(1, weight=1)  # tabview row expands

        def _hdr(parent, text, row):
            customtkinter.CTkLabel(
                parent, text=text, font=customtkinter.CTkFont(weight="bold")
            ).grid(row=row, column=0, columnspan=4, pady=(10, 0), sticky="w", padx=20)

        # ── Header: profile bar ──────────────────────────────────────────
        header = customtkinter.CTkFrame(win)
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))

        customtkinter.CTkLabel(header, text="Profile:").grid(
            row=0, column=0, padx=(10, 4), sticky="e"
        )
        self.profile_var = customtkinter.StringVar(value="Default")
        self.profile_dropdown = customtkinter.CTkOptionMenu(
            header, variable=self.profile_var, values=["Default"], width=150
        )
        self.profile_dropdown.grid(row=0, column=1, padx=(0, 4))
        customtkinter.CTkButton(
            header, text="Save", width=60, command=self._save_profile
        ).grid(row=0, column=2, padx=2)
        customtkinter.CTkButton(
            header, text="New...", width=60, command=self._new_profile
        ).grid(row=0, column=3, padx=2)
        customtkinter.CTkButton(
            header, text="Load", width=60, command=self._load_profile
        ).grid(row=0, column=4, padx=2)
        customtkinter.CTkButton(
            header, text="Delete", width=60, command=self._delete_profile
        ).grid(row=0, column=5, padx=2)

        # (log_level is now surfaced via self.log_level_var dropdown in the Diagnostics section)

        # ── Tabview with a scrollable frame per tab ──────────────────────
        self.tabview = customtkinter.CTkTabview(win, width=680, height=560)
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=10, pady=0)
        self._tab_frames = {}
        for name in TAB_ORDER:
            self.tabview.add(name)
            frame = customtkinter.CTkScrollableFrame(self.tabview.tab(name))
            frame.pack(fill="both", expand=True)
            self._tab_frames[name] = frame

        # ==================================================================
        # Tab: Detection
        # ==================================================================
        det = self._tab_frames["Detection"]
        r = 0
        _hdr(det, "Detection Regions", r)
        r += 1
        self.label_x_axis = customtkinter.CTkLabel(det, text="X-Axis")
        self.label_y_axis = customtkinter.CTkLabel(det, text="Y-Axis")
        self.label_x_axis.grid(row=r, column=1)
        self.label_y_axis.grid(row=r, column=2)
        r += 1
        self.alert_region_label_1 = customtkinter.CTkLabel(
            det, text="Alert Region Left Upper Corner:", justify="left"
        )
        self.alert_region_x_first = customtkinter.CTkEntry(det)
        self.alert_region_y_first = customtkinter.CTkEntry(det)
        self.alert_region_label_1.grid(row=r, column=0, padx=20, sticky="e")
        self.alert_region_x_first.grid(row=r, column=1)
        self.alert_region_y_first.grid(row=r, column=2)
        r += 1
        self.alert_region_label_2 = customtkinter.CTkLabel(
            det, text="Alert Region Right Lower Corner:", justify="left"
        )
        self.alert_region_x_second = customtkinter.CTkEntry(det)
        self.alert_region_y_second = customtkinter.CTkEntry(det)
        self.alert_region_label_2.grid(row=r, column=0, padx=20, sticky="e")
        self.alert_region_x_second.grid(row=r, column=1)
        self.alert_region_y_second.grid(row=r, column=2)
        r += 1
        self.faction_region_label_1 = customtkinter.CTkLabel(
            det, text="Faction Region Left Upper Corner:", justify="left"
        )
        self.faction_region_x_first = customtkinter.CTkEntry(det)
        self.faction_region_y_first = customtkinter.CTkEntry(det)
        self.faction_region_label_1.grid(row=r, column=0, padx=20, sticky="e")
        self.faction_region_x_first.grid(row=r, column=1)
        self.faction_region_y_first.grid(row=r, column=2)
        r += 1
        self.faction_region_label_2 = customtkinter.CTkLabel(
            det, text="Faction Region Right Lower Corner:", justify="left"
        )
        self.faction_region_x_second = customtkinter.CTkEntry(det)
        self.faction_region_y_second = customtkinter.CTkEntry(det)
        self.faction_region_label_2.grid(row=r, column=0, padx=20, sticky="e")
        self.faction_region_x_second.grid(row=r, column=1)
        self.faction_region_y_second.grid(row=r, column=2)
        r += 1

        _hdr(det, "Detection Thresholds", r)
        r += 1
        self.slider_label = customtkinter.CTkLabel(det, text="Detection Threshold")
        self.detectionscale = customtkinter.DoubleVar()
        self.slider = customtkinter.CTkSlider(
            det,
            from_=1,
            to=100,
            orientation="horizontal",
            number_of_steps=99,
            variable=self.detectionscale,
            command=self.slider_event,
        )
        self.empty_label_1 = customtkinter.CTkLabel(det, text=self.slider.get())
        self.slider_label.grid(row=r, column=0)
        self.slider.grid(row=r, column=1)
        self.empty_label_1.grid(row=r, column=2)
        customtkinter.CTkButton(
            det,
            text="Per-Image Thresholds...",
            width=180,
            command=self._open_threshold_editor,
        ).grid(row=r, column=3, padx=(8, 0))
        r += 1
        self.faction_slider_label = customtkinter.CTkLabel(
            det, text="Faction Detection Threshold"
        )
        self.faction_scale = customtkinter.DoubleVar()
        self.slider2 = customtkinter.CTkSlider(
            det,
            from_=1,
            to=100,
            orientation="horizontal",
            number_of_steps=99,
            variable=self.faction_scale,
            command=self.factionslider_event,
        )
        self.empty_label_2 = customtkinter.CTkLabel(det, text=self.slider2.get())
        self.faction_slider_label.grid(row=r, column=0)
        self.slider2.grid(row=r, column=1)
        self.empty_label_2.grid(row=r, column=2)
        r += 1

        _hdr(det, "Cooldown Timers", r)
        r += 1
        self.cooldown_timer_label = customtkinter.CTkLabel(
            det, text="Cooldown Timer:", justify="left"
        )
        self.cooldown_timer = customtkinter.CTkEntry(det)
        self.cooldown_timer_text = customtkinter.CTkLabel(
            det, text="Seconds", justify="left"
        )
        self.cooldown_timer_label.grid(row=r, column=0, padx=20, sticky="e")
        self.cooldown_timer.grid(row=r, column=1)
        self.cooldown_timer_text.grid(row=r, column=2)
        r += 1
        self.cooldown_enemy_label = customtkinter.CTkLabel(
            det, text="Enemy Cooldown:", justify="left"
        )
        self.cooldown_timer_enemy = customtkinter.CTkEntry(det, width=70)
        self.cooldown_enemy_label.grid(row=r, column=0, padx=(20, 4), sticky="e")
        self.cooldown_timer_enemy.grid(row=r, column=1, sticky="w", padx=(0, 4))
        r += 1
        self.cooldown_faction_label = customtkinter.CTkLabel(
            det, text="Faction Cooldown:", justify="left"
        )
        self.cooldown_timer_faction_entry = customtkinter.CTkEntry(det, width=70)
        self.cooldown_faction_label.grid(row=r, column=0, padx=(20, 4), sticky="e")
        self.cooldown_timer_faction_entry.grid(row=r, column=1, sticky="w", padx=(0, 4))
        r += 1

        _hdr(det, "Threat Tiers", r)
        r += 1
        customtkinter.CTkLabel(det, text="Name/Corp/Alliance:", justify="left").grid(
            row=r, column=0, padx=(20, 4), sticky="e"
        )
        self.threat_tier_name_entry = customtkinter.CTkEntry(det, width=180)
        self.threat_tier_name_entry.grid(row=r, column=1, sticky="w")
        self.threat_tier_level_var = customtkinter.StringVar(value="red")
        self.threat_tier_level_menu = customtkinter.CTkOptionMenu(
            det,
            variable=self.threat_tier_level_var,
            values=["red", "orange", "yellow"],
            width=90,
        )
        self.threat_tier_level_menu.grid(row=r, column=2, padx=(4, 20), sticky="w")
        r += 1
        threat_btn_frame = customtkinter.CTkFrame(det)
        threat_btn_frame.grid(
            row=r, column=0, columnspan=3, padx=20, pady=(4, 0), sticky="w"
        )
        customtkinter.CTkButton(
            threat_btn_frame, text="Add", width=70, command=self._add_threat_tier
        ).pack(side="left", padx=(0, 4))
        customtkinter.CTkButton(
            threat_btn_frame,
            text="Remove Selected",
            width=130,
            fg_color="#8B0000",
            hover_color="#660000",
            command=self._remove_threat_tier,
        ).pack(side="left")
        r += 1
        self.threat_tiers_list = customtkinter.CTkScrollableFrame(det, height=80)
        self.threat_tiers_list.grid(
            row=r, column=0, columnspan=3, padx=20, pady=(4, 0), sticky="ew"
        )
        self._threat_tiers_data = {}
        self._threat_tier_rows = []
        self._selected_tier_key = None
        r += 1

        _hdr(det, "D-Scan Monitor", r)
        r += 1
        r = self._build_registry_section(det, "Detection", "D-Scan Monitor", r)

        # ==================================================================
        # Tab: Alerts & Sound
        # ==================================================================
        snd = self._tab_frames["Alerts & Sound"]
        r = 0
        _hdr(snd, "Volume", r)
        r += 1
        self.volume_slider_label = customtkinter.CTkLabel(snd, text="Volume")
        self.volume_scale = customtkinter.DoubleVar()
        self.volume_slider = customtkinter.CTkSlider(
            snd,
            from_=0,
            to=100,
            orientation="horizontal",
            number_of_steps=100,
            variable=self.volume_scale,
            command=self.volumeslider_event,
        )
        self.empty_label_3 = customtkinter.CTkLabel(
            snd, text=f"{int(self.volume_slider.get())}%"
        )
        self.volume_slider_label.grid(row=r, column=0)
        self.volume_slider.grid(row=r, column=1)
        self.empty_label_3.grid(row=r, column=2)
        r += 1

        _hdr(snd, "System", r)
        r += 1
        self.system_name_label = customtkinter.CTkLabel(
            snd, text="System Name:", justify="left"
        )
        self.system_name = customtkinter.CTkEntry(snd)
        self.play_alarm_checkbox = customtkinter.CTkCheckBox(
            snd, text="Mute Alarm", variable=self.play_alarm
        )
        self.system_name_label.grid(row=r, column=0, padx=(20, 4), sticky="e")
        self.system_name.grid(row=r, column=1)
        self.play_alarm_checkbox.grid(row=r, column=2, padx=(8, 0))
        r += 1

        _hdr(snd, "Test Audio", r)
        r += 1
        self.test_alarm_button = customtkinter.CTkButton(
            snd, text="Test Alarm Sound", command=self.test_alarm_sound
        )
        self.test_faction_button = customtkinter.CTkButton(
            snd, text="Test Faction Sound", command=self.test_faction_sound
        )
        self.test_alarm_button.grid(row=r, column=0, pady=(4, 0), padx=(20, 4))
        self.test_faction_button.grid(row=r, column=1, pady=(4, 0))
        r += 1

        _hdr(snd, "Custom Sounds (WAV files)", r)
        r += 1
        self.browse_alarm_button = customtkinter.CTkButton(
            snd, text="Browse Alarm...", width=120, command=self._browse_alarm_sound
        )
        self.alarm_sound_label = customtkinter.CTkLabel(
            snd, text="Default", justify="left", width=180
        )
        self.browse_alarm_button.grid(row=r, column=0, padx=(20, 4), sticky="e")
        self.alarm_sound_label.grid(row=r, column=1, columnspan=2, sticky="w")
        r += 1
        self.browse_faction_button = customtkinter.CTkButton(
            snd, text="Browse Faction...", width=120, command=self._browse_faction_sound
        )
        self.faction_sound_label = customtkinter.CTkLabel(
            snd, text="Default", justify="left", width=180
        )
        self.browse_faction_button.grid(row=r, column=0, padx=(20, 4), sticky="e")
        self.faction_sound_label.grid(row=r, column=1, columnspan=2, sticky="w")
        r += 1

        _hdr(snd, "Hotkeys (e.g. f1, f2, esc, g)", r)
        r += 1
        self.hotkey_alert_label = customtkinter.CTkLabel(
            snd, text="Alert Region Key:", justify="left"
        )
        self.hotkey_alert_entry = customtkinter.CTkEntry(snd, width=80)
        self.hotkey_alert_label.grid(row=r, column=0, padx=(20, 4), sticky="e")
        self.hotkey_alert_entry.grid(row=r, column=1, sticky="w")
        r += 1
        self.hotkey_faction_label = customtkinter.CTkLabel(
            snd, text="Faction Region Key:", justify="left"
        )
        self.hotkey_faction_entry = customtkinter.CTkEntry(snd, width=80)
        self.hotkey_faction_label.grid(row=r, column=0, padx=(20, 4), sticky="e")
        self.hotkey_faction_entry.grid(row=r, column=1, sticky="w")
        r += 1

        _hdr(snd, "Alarm Options", r)
        r += 1
        r = self._build_registry_section(snd, "Alerts & Sound", "Alarm Options", r)

        _hdr(snd, "Diagnostics", r)
        r += 1
        r = self._build_registry_section(snd, "Alerts & Sound", "Diagnostics", r)
        # Log level dropdown (bespoke — fixed choices, not a free-form entry)
        customtkinter.CTkLabel(snd, text="Log Level:", justify="left").grid(
            row=r, column=0, padx=(20, 4), sticky="e"
        )
        self.log_level_menu = customtkinter.CTkOptionMenu(
            snd,
            values=["DEBUG", "INFO", "WARNING", "ERROR"],
            variable=self.log_level_var,
            width=120,
        )
        self.log_level_menu.grid(row=r, column=1, sticky="w", padx=(0, 4))
        r += 1
        # Export button
        self.export_diag_button = customtkinter.CTkButton(
            snd,
            text="Export Diagnostics Bundle",
            command=self._export_diagnostics_bundle,
            width=200,
        )
        self.export_diag_button.grid(
            row=r, column=0, columnspan=2, padx=20, pady=(4, 0), sticky="w"
        )
        r += 1
        # Log path info label
        from evealert.settings.logger import (  # pylint: disable=import-outside-toplevel
            get_log_dir,
        )

        self._diag_log_path_label = customtkinter.CTkLabel(
            snd,
            text=f"Logs: {get_log_dir()}",
            text_color="gray",
            wraplength=420,
            justify="left",
        )
        self._diag_log_path_label.grid(
            row=r, column=0, columnspan=3, padx=20, pady=(0, 4), sticky="w"
        )
        r += 1

        # ====================================================================
        # Tab: Intel & ESI
        # ==================================================================
        intel = self._tab_frames["Intel & ESI"]
        r = 0
        _hdr(intel, "Intelligence", r)
        r += 1
        r = self._build_registry_section(intel, "Intel & ESI", "Intelligence", r)

        _hdr(intel, "ESI Augmentation", r)
        r += 1
        r = self._build_registry_section(intel, "Intel & ESI", "ESI Augmentation", r)

        _hdr(intel, "KOS Checker", r)
        r += 1
        r = self._build_registry_section(intel, "Intel & ESI", "KOS Checker", r)
        customtkinter.CTkLabel(intel, text="Custom KOS URLs:", justify="left").grid(
            row=r, column=0, padx=(20, 4), sticky="e"
        )
        self.kos_custom_entry = customtkinter.CTkEntry(
            intel, width=340, placeholder_text="comma-separated URLs"
        )
        self.kos_custom_entry.grid(row=r, column=1, columnspan=2, sticky="w")
        r += 1

        _hdr(intel, "Adjacent System Monitor", r)
        r += 1
        r = self._build_registry_section(
            intel, "Intel & ESI", "Adjacent System Monitor", r
        )
        self.adjacent_check_route_btn = customtkinter.CTkButton(
            intel, text="Check Route", width=110, command=self._check_route
        )
        self.adjacent_check_route_btn.grid(
            row=r, column=0, columnspan=2, padx=20, pady=(2, 0), sticky="w"
        )
        r += 1

        _hdr(intel, "EVE SSO / ESI OAuth", r)
        r += 1
        r = self._build_registry_section(intel, "Intel & ESI", "EVE SSO / ESI OAuth", r)
        self._esi_status_label = customtkinter.CTkLabel(
            intel, text="Not logged in", text_color="gray"
        )
        self._esi_status_label.grid(
            row=r, column=0, columnspan=2, padx=(20, 4), sticky="w", pady=2
        )
        customtkinter.CTkButton(
            intel, text="Login with EVE", command=self._esi_login
        ).grid(row=r, column=2, padx=(4, 20), pady=2)
        customtkinter.CTkButton(
            intel, text="Logout", command=self._esi_logout, fg_color="gray"
        ).grid(row=r, column=3, padx=(0, 20), pady=2)
        r += 1

        _hdr(intel, "OCR Name Detection", r)
        r += 1
        r = self._build_registry_section(intel, "Intel & ESI", "OCR Name Detection", r)

        # ==================================================================
        # Tab: Notifications
        # ==================================================================
        notif = self._tab_frames["Notifications"]
        r = 0
        _hdr(notif, "Webhook", r)
        r += 1
        self.webhook_label = customtkinter.CTkLabel(
            notif, text="Webhook (all):", justify="left"
        )
        self.webhook = customtkinter.CTkEntry(notif, width=300)
        self.webhook_label.grid(row=r, column=0, padx=(20, 4), sticky="e")
        self.webhook.grid(row=r, column=1, columnspan=2, sticky="w")
        r += 1
        self.webhook_template_label = customtkinter.CTkLabel(
            notif, text="Msg Template:", justify="left"
        )
        self.webhook_template_entry = customtkinter.CTkEntry(notif, width=340)
        self.webhook_template_label.grid(row=r, column=0, padx=(20, 4), sticky="e")
        self.webhook_template_entry.grid(
            row=r, column=1, columnspan=2, padx=(0, 20), sticky="w"
        )
        r += 1
        self.enemy_webhook_label = customtkinter.CTkLabel(
            notif, text="Enemy Webhook:", justify="left"
        )
        self.enemy_webhook_entry = customtkinter.CTkEntry(notif, width=260)
        self.enemy_mincount_label = customtkinter.CTkLabel(
            notif, text="Min#:", justify="left"
        )
        self.enemy_webhook_mincount = customtkinter.CTkEntry(notif, width=40)
        self.enemy_webhook_label.grid(row=r, column=0, padx=(20, 4), sticky="e")
        self.enemy_webhook_entry.grid(row=r, column=1, sticky="w")
        self.enemy_mincount_label.grid(row=r, column=2, sticky="e")
        self.enemy_webhook_mincount.grid(row=r, column=3, sticky="w")
        r += 1
        self.faction_webhook_label = customtkinter.CTkLabel(
            notif, text="Faction Webhook:", justify="left"
        )
        self.faction_webhook_entry = customtkinter.CTkEntry(notif, width=260)
        self.faction_mincount_label = customtkinter.CTkLabel(
            notif, text="Min#:", justify="left"
        )
        self.faction_webhook_mincount = customtkinter.CTkEntry(notif, width=40)
        self.faction_webhook_label.grid(row=r, column=0, padx=(20, 4), sticky="e")
        self.faction_webhook_entry.grid(row=r, column=1, sticky="w")
        self.faction_mincount_label.grid(row=r, column=2, sticky="e")
        self.faction_webhook_mincount.grid(row=r, column=3, sticky="w")
        r += 1

        _hdr(notif, "Push Notifications", r)
        r += 1
        r = self._build_registry_section(
            notif, "Notifications", "Push Notifications", r
        )

        _hdr(notif, "Web Status UI", r)
        r += 1
        r = self._build_registry_section(notif, "Notifications", "Web Status UI", r)

        # ==================================================================
        # Tab: Wormhole & Fleet
        # ==================================================================
        whf = self._tab_frames["Wormhole & Fleet"]
        r = 0
        _hdr(whf, "Wormhole Awareness", r)
        r += 1
        r = self._build_registry_section(
            whf, "Wormhole & Fleet", "Wormhole Awareness", r
        )

        _hdr(whf, "Fleet Context", r)
        r += 1
        r = self._build_registry_section(whf, "Wormhole & Fleet", "Fleet Context", r)
        customtkinter.CTkLabel(whf, text="Tracked char IDs:", justify="left").grid(
            row=r, column=0, padx=(20, 4), sticky="e"
        )
        self.fleet_char_ids_entry = customtkinter.CTkEntry(
            whf, width=300, placeholder_text="comma-separated ESI character IDs"
        )
        self.fleet_char_ids_entry.grid(row=r, column=1, columnspan=2, sticky="w")
        r += 1

        # ── Footer: Save / Apply / Close (always visible) ────────────────
        footer = customtkinter.CTkFrame(win)
        footer.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 10))
        self.save_button = customtkinter.CTkButton(
            footer, text="Save", command=self.save
        )
        self.apply_button = customtkinter.CTkButton(
            footer, text="Apply", command=self.apply_settings_runtime
        )
        self.close_button = customtkinter.CTkButton(
            footer, text="Close", command=self.clean_up
        )
        self.save_button.grid(row=0, column=0, pady=8, padx=8)
        self.apply_button.grid(row=0, column=1, pady=8, padx=8)
        self.close_button.grid(row=0, column=2, pady=8, padx=8)

        # Refresh login status if already authenticated
        self._esi_refresh_status()

        self.setting_window.protocol("WM_DELETE_WINDOW", self.clean_up)

    def open_menu(self):
        """Opens the settings window."""
        if not self.is_open:
            self._ensure_window()
            # Re-apply persisted settings to the freshly-referenced widgets so
            # saved values (incl. intelligence checkboxes) show correctly after
            # an app restart, not the widget constructor defaults (#99/#108).
            self.load_settings()
            self.open = True
            self.main.mainmenu_buttons.setting_menu.configure(
                fg_color="#fa0202", hover_color="#bd291e"
            )

            config_menu_x = self.main.winfo_x()
            config_menu_y = self.main.winfo_y()
            config_menu_width = self.main.winfo_width()

            # Resizable window with a sane minimum; the tabview + per-tab
            # scrollable frames handle overflow, so no fixed tall geometry that
            # clips the footer off-screen (#107).
            config_window_width = 760
            config_window_height = 720
            self.setting_window.minsize(700, 500)
            self.setting_window.resizable(True, True)

            raw_x = config_menu_x + config_menu_width + 10
            raw_y = config_menu_y + 10

            # Clamp to screen bounds so the popup never opens off-screen.
            screen_w = self.main.winfo_screenwidth()
            screen_h = self.main.winfo_screenheight()
            window_x = max(10, min(raw_x, screen_w - config_window_width - 10))
            window_y = max(10, min(raw_y, screen_h - config_window_height - 10))

            self.setting_window.geometry(
                f"{config_window_width}x{config_window_height}+{window_x}+{window_y}"
            )
            self.setting_window.deiconify()
        else:
            self.clean_up()

    def slider_event(self, slider_value):
        self.empty_label_1.configure(text=slider_value)

    def factionslider_event(self, slider_value):
        self.empty_label_2.configure(text=slider_value)

    def volumeslider_event(self, slider_value):
        self.empty_label_3.configure(text=f"{int(slider_value)}%")

    def test_alarm_sound(self):
        """Test alarm sound playback."""
        try:
            import numpy as np  # pylint: disable=import-outside-toplevel
            import sounddevice as sd  # pylint: disable=import-outside-toplevel
            import soundfile as sf  # pylint: disable=import-outside-toplevel

            # pylint: disable=import-outside-toplevel
            from evealert.constants import AUDIO_CHANNELS
            from evealert.manager.alertmanager import ALARM_SOUND

            # Check if muted
            if self.play_alarm.get():
                self.main.write_message(
                    "Audio Test: Alarm is muted. Uncheck 'Mute Alarm' to test.",
                    "yellow",
                )
                return

            self.main.write_message("Audio Test: Playing alarm sound...", "green")

            # Play sound directly using sounddevice
            try:
                # Read audio data with soundfile
                data, samplerate = sf.read(ALARM_SOUND, dtype="int16")

                # Check data shape and adjust channels if necessary
                if data.ndim == 1:
                    # Convert Mono -> Stereo
                    data = np.stack([data, data], axis=-1)
                elif data.ndim == 2 and data.shape[1] == 1:
                    # (N, 1) -> (N, AUDIO_CHANNELS)
                    data = np.repeat(data, AUDIO_CHANNELS, axis=1)

                # Play the audio data (blocking)
                sd.play(data, samplerate)
                sd.wait()  # Wait for playback to finish

                self.main.write_message("Audio Test: Alarm sound completed.", "green")
            except FileNotFoundError:
                self.main.write_message(
                    f"Audio Test: Sound file not found: {ALARM_SOUND}", "red"
                )
            except Exception as e:
                self.main.write_message(
                    f"Audio Test: Error playing sound. {str(e)}", "red"
                )
                logger.exception("Error testing alarm sound: %s", e)

        except Exception as e:
            self.main.write_message(f"Audio Test: Error. {str(e)}", "red")
            logger.exception("Error in test_alarm_sound: %s", e)

    def test_faction_sound(self):
        """Test faction sound playback."""
        try:
            import numpy as np  # pylint: disable=import-outside-toplevel
            import sounddevice as sd  # pylint: disable=import-outside-toplevel
            import soundfile as sf  # pylint: disable=import-outside-toplevel

            # pylint: disable=import-outside-toplevel
            from evealert.constants import AUDIO_CHANNELS
            from evealert.manager.alertmanager import FACTION_SOUND

            # Check if muted
            if self.play_alarm.get():
                self.main.write_message(
                    "Audio Test: Alarm is muted. Uncheck 'Mute Alarm' to test.",
                    "yellow",
                )
                return

            self.main.write_message("Audio Test: Playing faction sound...", "green")

            # Play sound directly using sounddevice
            try:
                # Read audio data with soundfile
                data, samplerate = sf.read(FACTION_SOUND, dtype="int16")

                # Check data shape and adjust channels if necessary
                if data.ndim == 1:
                    # Convert Mono -> Stereo
                    data = np.stack([data, data], axis=-1)
                elif data.ndim == 2 and data.shape[1] == 1:
                    # (N, 1) -> (N, AUDIO_CHANNELS)
                    data = np.repeat(data, AUDIO_CHANNELS, axis=1)

                # Apply volume (convert 0-100 to 0.0-1.0)
                volume = self.volume_scale.get() / 100.0
                data_with_volume = (data * volume).astype("int16")

                # Play the audio data (blocking)
                sd.play(data_with_volume, samplerate)
                sd.wait()  # Wait for playback to finish

                self.main.write_message("Audio Test: Faction sound completed.", "green")
            except FileNotFoundError:
                self.main.write_message(
                    f"Audio Test: Sound file not found: {FACTION_SOUND}", "red"
                )
            except Exception as e:
                self.main.write_message(
                    f"Audio Test: Error playing sound. {str(e)}", "red"
                )
                logger.exception("Error testing faction sound: %s", e)

        except Exception as e:
            self.main.write_message(f"Audio Test: Error. {str(e)}", "red")
            logger.exception("Error in test_faction_sound: %s", e)
