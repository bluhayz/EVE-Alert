import json
import os
import tempfile
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
}


class SettingMenu:
    """Setting menu for the Alert System."""

    def __init__(self, main: "MainMenu"):
        self.main = main
        self.open = False
        self.default = DEFAULT_SETTINGS
        self.changed = False
        self._window_created = False

        # BooleanVar doesn't depend on a window so it can be created here
        self.play_alarm = customtkinter.BooleanVar()

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

        # Only sync UI if the window has been created
        if self._window_created:
            self.apply_settings(settings)
        return settings

    def merge_settings_with_defaults(self, settings, defaults=None):
        """Merge the loaded settings with the default settings recursively."""
        if defaults is None:
            defaults = self.default

        merged_settings = defaults.copy()
        for key, value in defaults.items():
            if key in settings:
                if isinstance(value, dict) and isinstance(settings[key], dict):
                    # Recursively merge nested dictionaries
                    merged_settings[key] = self.merge_settings_with_defaults(
                        settings[key], value
                    )
                else:
                    merged_settings[key] = settings[key]
            else:
                # Fill missing keys with default values
                merged_settings[key] = value

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

            self.logging.delete(0, customtkinter.END)
            self.logging.insert(0, settings["log_level"])

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

            # Intelligence
            intel = settings.get("intelligence", {})
            self.zkillboard_var.set(bool(intel.get("zkillboard_enabled", False)))
            self.intel_log_var.set(bool(intel.get("intel_log_enabled", False)))
            self.intel_channel_entry.delete(0, customtkinter.END)
            self.intel_channel_entry.insert(0, intel.get("intel_log_channel", ""))

            # ESI
            esi = settings.get("esi", {})
            self.esi_enabled_var.set(bool(esi.get("enabled", False)))
            self.esi_corp_var.set(bool(esi.get("show_corp", True)))
            self.esi_alliance_var.set(bool(esi.get("show_alliance", True)))
            self.esi_flashy_var.set(bool(esi.get("alert_flashy", False)))

            # Threat tiers
            tiers = settings.get("threat_tiers", {})
            self._threat_tiers_data = dict(tiers)
            self._refresh_threat_tiers_list()

            # Web UI
            web = settings.get("web_ui", {})
            self.web_ui_var.set(bool(web.get("enabled", False)))
            self.web_ui_port_entry.delete(0, customtkinter.END)
            self.web_ui_port_entry.insert(0, str(web.get("port", 8765)))

            # Adjacent system monitor
            adj = settings.get("adjacent", {})
            self.adjacent_enabled_var.set(bool(adj.get("enabled", False)))
            self.adjacent_max_jumps_entry.delete(0, customtkinter.END)
            self.adjacent_max_jumps_entry.insert(0, str(adj.get("max_jumps", 3)))
            self.adjacent_poll_entry.delete(0, customtkinter.END)
            self.adjacent_poll_entry.insert(0, str(adj.get("poll_interval", 120)))
            self.adjacent_min_kills_entry.delete(0, customtkinter.END)
            self.adjacent_min_kills_entry.insert(0, str(adj.get("min_kills", 1)))
            self.adjacent_dest_entry.delete(0, customtkinter.END)
            self.adjacent_dest_entry.insert(0, adj.get("destination_system", ""))

            # D-scan monitor
            ds = settings.get("dscan", {})
            self.dscan_enabled_var.set(bool(ds.get("enabled", False)))
            self.dscan_red_var.set(bool(ds.get("alert_red", True)))
            self.dscan_orange_var.set(bool(ds.get("alert_orange", False)))
            self.dscan_probes_var.set(bool(ds.get("alert_probes", True)))

            # KOS settings
            kos = settings.get("kos", {})
            self.kos_cva_var.set(bool(kos.get("cva_enabled", True)))
            self.kos_custom_entry.delete(0, customtkinter.END)
            self.kos_custom_entry.insert(0, ", ".join(kos.get("custom_urls", [])))

            # Push notifications
            push = settings.get("push", {})
            self.telegram_token_entry.delete(0, customtkinter.END)
            self.telegram_token_entry.insert(0, push.get("telegram_token", ""))
            self.telegram_chat_entry.delete(0, customtkinter.END)
            self.telegram_chat_entry.insert(0, push.get("telegram_chat_id", ""))
            self.pushover_user_entry.delete(0, customtkinter.END)
            self.pushover_user_entry.insert(0, push.get("pushover_user", ""))
            self.pushover_token_entry.delete(0, customtkinter.END)
            self.pushover_token_entry.insert(0, push.get("pushover_token", ""))
            self.ntfy_url_entry.delete(0, customtkinter.END)
            self.ntfy_url_entry.insert(0, push.get("ntfy_url", ""))

            # Notification options
            notif = settings.get("notifications", {})
            self.auto_screenshot_var.set(bool(notif.get("auto_screenshot", False)))
            self.escalation_threshold_entry.delete(0, customtkinter.END)
            self.escalation_threshold_entry.insert(
                0, str(notif.get("escalation_threshold", 0))
            )

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

    def save(self):
        """Save settings to disk only (does not apply to running system)."""
        try:
            settings = DEFAULT_SETTINGS.copy()
            settings.update(
                {
                    "log_level": self.logging.get(),
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
                    "intelligence": {
                        "zkillboard_enabled": self.zkillboard_var.get(),
                        "zkillboard_cooldown": DEFAULT_SETTINGS["intelligence"][
                            "zkillboard_cooldown"
                        ],
                        "intel_log_enabled": self.intel_log_var.get(),
                        "intel_log_channel": self.intel_channel_entry.get().strip(),
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
                    "esi": {
                        "enabled": self.esi_enabled_var.get(),
                        "show_corp": self.esi_corp_var.get(),
                        "show_alliance": self.esi_alliance_var.get(),
                        "alert_flashy": self.esi_flashy_var.get(),
                    },
                    "threat_tiers": dict(getattr(self, "_threat_tiers_data", {})),
                    "plugins": {
                        "enabled": True,  # always on; no UI toggle needed
                    },
                    "web_ui": {
                        "enabled": self.web_ui_var.get(),
                        "port": int(self.web_ui_port_entry.get().strip() or 8765),
                    },
                    "adjacent": {
                        "enabled": self.adjacent_enabled_var.get(),
                        "max_jumps": int(
                            self.adjacent_max_jumps_entry.get().strip() or 3
                        ),
                        "poll_interval": int(
                            self.adjacent_poll_entry.get().strip() or 120
                        ),
                        "min_kills": int(
                            self.adjacent_min_kills_entry.get().strip() or 1
                        ),
                        "destination_system": self.adjacent_dest_entry.get().strip(),
                    },
                    "dscan": {
                        "enabled": self.dscan_enabled_var.get(),
                        "alert_red": self.dscan_red_var.get(),
                        "alert_orange": self.dscan_orange_var.get(),
                        "alert_probes": self.dscan_probes_var.get(),
                    },
                    "kos": {
                        "cva_enabled": self.kos_cva_var.get(),
                        "custom_urls": [
                            u.strip()
                            for u in self.kos_custom_entry.get().split(",")
                            if u.strip()
                        ],
                    },
                    "push": {
                        "telegram_token": self.telegram_token_entry.get().strip(),
                        "telegram_chat_id": self.telegram_chat_entry.get().strip(),
                        "pushover_user": self.pushover_user_entry.get().strip(),
                        "pushover_token": self.pushover_token_entry.get().strip(),
                        "ntfy_url": self.ntfy_url_entry.get().strip(),
                    },
                    "notifications": {
                        "auto_screenshot": self.auto_screenshot_var.get(),
                        "escalation_threshold": int(
                            self.escalation_threshold_entry.get().strip() or 0
                        ),
                    },
                }
            )
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

    def clean_up(self):
        """Cleans up the settings window."""
        if self.is_open:
            self.open = False
            self.main.mainmenu_buttons.setting_menu.configure(
                fg_color="#1f538d", hover_color="#14375e"
            )
            if self._window_created:
                self.setting_window.withdraw()

    def create_menu(self):
        """Load the settings from the settings file."""

        # Use a separate frame for the menu
        self.menu_frame = customtkinter.CTkFrame(self.setting_window)
        self.menu_frame.pack(side="left", padx=20, pady=20)

        # Row -1: Detection Profiles
        profile_label = customtkinter.CTkLabel(self.menu_frame, text="Profile:")
        self.profile_var = customtkinter.StringVar(value="Default")
        self.profile_dropdown = customtkinter.CTkOptionMenu(
            self.menu_frame,
            variable=self.profile_var,
            values=["Default"],
            width=150,
        )
        save_profile_btn = customtkinter.CTkButton(
            self.menu_frame, text="Save", width=60, command=self._save_profile
        )
        new_profile_btn = customtkinter.CTkButton(
            self.menu_frame, text="New...", width=60, command=self._new_profile
        )
        load_profile_btn = customtkinter.CTkButton(
            self.menu_frame, text="Load", width=60, command=self._load_profile
        )
        delete_profile_btn = customtkinter.CTkButton(
            self.menu_frame, text="Delete", width=60, command=self._delete_profile
        )
        profile_label.grid(row=0, column=0, padx=(20, 4), sticky="e")
        self.profile_dropdown.grid(row=0, column=1, padx=(0, 4))
        save_profile_btn.grid(row=0, column=2, padx=2)
        new_profile_btn.grid(row=0, column=3, padx=2)
        load_profile_btn.grid(row=0, column=4, padx=2)
        delete_profile_btn.grid(row=0, column=5, padx=2)

        separator = customtkinter.CTkFrame(self.menu_frame, height=2, fg_color="gray40")
        separator.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(4, 8), padx=10)

        self.logging = customtkinter.CTkEntry(self.menu_frame)

        # 1 Row - Init
        self.label_x_axis = customtkinter.CTkLabel(self.menu_frame, text="X-Axis")
        self.label_y_axis = customtkinter.CTkLabel(self.menu_frame, text="Y-Axis")

        # 2 Row - Init
        # Alert Region Position 1
        self.alert_region_label_1 = customtkinter.CTkLabel(
            self.menu_frame, text="Alert Region Left Upper Corner:", justify="left"
        )
        self.alert_region_x_first = customtkinter.CTkEntry(self.menu_frame)
        self.alert_region_y_first = customtkinter.CTkEntry(self.menu_frame)

        # 3 Row - Init
        # Alert Region Position 2
        self.alert_region_label_2 = customtkinter.CTkLabel(
            self.menu_frame, text="Alert Region Right Lower Corner:", justify="left"
        )
        self.alert_region_x_second = customtkinter.CTkEntry(self.menu_frame)
        self.alert_region_y_second = customtkinter.CTkEntry(self.menu_frame)

        # 4 Row - Init
        # Alert Region Position 1
        self.faction_region_label_1 = customtkinter.CTkLabel(
            self.menu_frame, text="Faction Region Left Upper Corner:", justify="left"
        )
        self.faction_region_x_first = customtkinter.CTkEntry(self.menu_frame)
        self.faction_region_y_first = customtkinter.CTkEntry(self.menu_frame)

        # 5 Row - Init
        # Alert Region Position 2
        self.faction_region_label_2 = customtkinter.CTkLabel(
            self.menu_frame, text="Faction Region Right Lower Corner:", justify="left"
        )
        self.faction_region_x_second = customtkinter.CTkEntry(self.menu_frame)
        self.faction_region_y_second = customtkinter.CTkEntry(self.menu_frame)

        # Row 6 - Init
        # Slider
        self.slider_label = customtkinter.CTkLabel(
            self.menu_frame, text="Detection Threshold"
        )
        self.detectionscale = customtkinter.DoubleVar()
        self.slider = customtkinter.CTkSlider(
            self.menu_frame,
            from_=1,
            to=100,
            orientation="horizontal",
            number_of_steps=99,
            variable=self.detectionscale,
            command=self.slider_event,
        )

        # Row 7 - Init
        # Slider
        self.faction_slider_label = customtkinter.CTkLabel(
            self.menu_frame, text="Faction Detection Threshold"
        )
        self.faction_scale = customtkinter.DoubleVar()
        self.slider2 = customtkinter.CTkSlider(
            self.menu_frame,
            from_=1,
            to=100,
            orientation="horizontal",
            number_of_steps=99,
            variable=self.faction_scale,
            command=self.factionslider_event,
        )

        # Row 8 - Init
        # Volume Slider
        self.volume_slider_label = customtkinter.CTkLabel(
            self.menu_frame, text="Volume"
        )
        self.volume_scale = customtkinter.DoubleVar()
        self.volume_slider = customtkinter.CTkSlider(
            self.menu_frame,
            from_=0,
            to=100,
            orientation="horizontal",
            number_of_steps=100,
            variable=self.volume_scale,
            command=self.volumeslider_event,
        )

        self.cooldown_timer_label = customtkinter.CTkLabel(
            self.menu_frame, text="Cooldown Timer:", justify="left"
        )
        self.cooldown_timer = customtkinter.CTkEntry(self.menu_frame)
        self.cooldown_timer_text = customtkinter.CTkLabel(
            self.menu_frame, text="Seconds", justify="left"
        )

        # Per-type cooldown entries
        self.cooldown_enemy_label = customtkinter.CTkLabel(
            self.menu_frame, text="Enemy Cooldown:", justify="left"
        )
        self.cooldown_timer_enemy = customtkinter.CTkEntry(self.menu_frame, width=70)
        self.cooldown_faction_label = customtkinter.CTkLabel(
            self.menu_frame, text="Faction Cooldown:", justify="left"
        )
        self.cooldown_timer_faction_entry = customtkinter.CTkEntry(
            self.menu_frame, width=70
        )

        self.save_button = customtkinter.CTkButton(
            self.menu_frame, text="Save", command=self.save
        )

        self.apply_button = customtkinter.CTkButton(
            self.menu_frame, text="Apply", command=self.apply_settings_runtime
        )

        self.close_button = customtkinter.CTkButton(
            self.menu_frame, text="Close", command=self.clean_up
        )

        self.empty_label_1 = customtkinter.CTkLabel(
            self.menu_frame, text=self.slider.get()
        )

        self.empty_label_2 = customtkinter.CTkLabel(
            self.menu_frame, text=self.slider2.get()
        )

        self.empty_label_3 = customtkinter.CTkLabel(
            self.menu_frame, text=f"{int(self.volume_slider.get())}%"
        )

        self.webhook_label = customtkinter.CTkLabel(
            self.menu_frame, text="Webhook (all):", justify="left"
        )
        self.webhook = customtkinter.CTkEntry(self.menu_frame)

        # Webhook message template
        self.webhook_template_label = customtkinter.CTkLabel(
            self.menu_frame, text="Msg Template:", justify="left"
        )
        self.webhook_template_entry = customtkinter.CTkEntry(self.menu_frame, width=340)

        # Per-type webhook targets
        self.enemy_webhook_label = customtkinter.CTkLabel(
            self.menu_frame, text="Enemy Webhook:", justify="left"
        )
        self.enemy_webhook_entry = customtkinter.CTkEntry(self.menu_frame, width=260)
        self.enemy_mincount_label = customtkinter.CTkLabel(
            self.menu_frame, text="Min#:", justify="left"
        )
        self.enemy_webhook_mincount = customtkinter.CTkEntry(self.menu_frame, width=40)
        self.faction_webhook_label = customtkinter.CTkLabel(
            self.menu_frame, text="Faction Webhook:", justify="left"
        )
        self.faction_webhook_entry = customtkinter.CTkEntry(self.menu_frame, width=260)
        self.faction_mincount_label = customtkinter.CTkLabel(
            self.menu_frame, text="Min#:", justify="left"
        )
        self.faction_webhook_mincount = customtkinter.CTkEntry(
            self.menu_frame, width=40
        )
        self.system_name_label = customtkinter.CTkLabel(
            self.menu_frame, text="System Name:", justify="left"
        )
        self.system_name = customtkinter.CTkEntry(self.menu_frame)

        self.play_alarm_checkbox = customtkinter.CTkCheckBox(
            self.menu_frame, text="Mute Alarm", variable=self.play_alarm
        )

        self.test_alarm_button = customtkinter.CTkButton(
            self.menu_frame, text="Test Alarm Sound", command=self.test_alarm_sound
        )

        self.test_faction_button = customtkinter.CTkButton(
            self.menu_frame, text="Test Faction Sound", command=self.test_faction_sound
        )

        # Init Visuals

        self.label_x_axis.grid(row=2, column=1)
        self.label_y_axis.grid(row=2, column=2)

        # Alert Region 1 Visual
        self.alert_region_label_1.grid(row=3, column=0, padx=20)
        self.alert_region_x_first.grid(row=3, column=1)
        self.alert_region_y_first.grid(row=3, column=2)

        # Alert Region 2 Visual
        self.alert_region_label_2.grid(row=4, column=0, padx=20)
        self.alert_region_x_second.grid(row=4, column=1, padx=20)
        self.alert_region_y_second.grid(row=4, column=2, padx=20)

        # Faction Region 1 Visual
        self.faction_region_label_1.grid(row=5, column=0, padx=20)
        self.faction_region_x_first.grid(row=5, column=1, padx=20)
        self.faction_region_y_first.grid(row=5, column=2, padx=20)

        # Faction Region 2 Visual
        self.faction_region_label_2.grid(row=6, column=0, padx=20)
        self.faction_region_x_second.grid(row=6, column=1, padx=20)
        self.faction_region_y_second.grid(row=6, column=2, padx=20)

        # Cooldown
        self.cooldown_timer_label.grid(row=7, column=0, padx=20)
        self.cooldown_timer.grid(row=7, column=1, padx=20)
        self.cooldown_timer_text.grid(row=7, column=2)

        # Per-type cooldowns
        self.cooldown_enemy_label.grid(row=8, column=0, padx=(20, 4), sticky="e")
        self.cooldown_timer_enemy.grid(row=8, column=1, sticky="w", padx=(0, 4))
        self.cooldown_faction_label.grid(row=9, column=0, padx=(20, 4), sticky="e")
        self.cooldown_timer_faction_entry.grid(row=9, column=1, sticky="w", padx=(0, 4))

        # Detection Threshold Slider
        self.empty_label_1.grid(row=10, column=2)
        self.slider_label.grid(row=10, column=0)
        self.slider.grid(row=10, column=1)

        # Per-image threshold editor button
        threshold_btn = customtkinter.CTkButton(
            self.menu_frame,
            text="Per-Image Thresholds...",
            width=180,
            command=self._open_threshold_editor,
        )
        threshold_btn.grid(row=10, column=3, padx=(8, 0))

        # Faction Threshold Slider
        self.empty_label_2.grid(row=11, column=2)
        self.faction_slider_label.grid(row=11, column=0)
        self.slider2.grid(row=11, column=1)

        # Volume Slider
        self.empty_label_3.grid(row=12, column=2)
        self.volume_slider_label.grid(row=12, column=0)
        self.volume_slider.grid(row=12, column=1)

        # Webhook (all events)
        self.webhook_label.grid(row=13, column=0)
        self.webhook.grid(row=13, column=1)

        # Webhook message template
        self.webhook_template_label.grid(row=14, column=0, padx=(20, 4), sticky="e")
        self.webhook_template_entry.grid(
            row=14, column=1, columnspan=2, padx=(0, 20), sticky="w"
        )

        # Per-type webhook targets
        self.enemy_webhook_label.grid(row=15, column=0, padx=(20, 4), sticky="e")
        self.enemy_webhook_entry.grid(row=15, column=1, sticky="w")
        self.enemy_mincount_label.grid(row=15, column=2, sticky="w")
        self.enemy_webhook_mincount.grid(row=15, column=3, sticky="w")
        self.faction_webhook_label.grid(row=16, column=0, padx=(20, 4), sticky="e")
        self.faction_webhook_entry.grid(row=16, column=1, sticky="w")
        self.faction_mincount_label.grid(row=16, column=2, sticky="w")
        self.faction_webhook_mincount.grid(row=16, column=3, sticky="w")

        # System Name
        self.system_name_label.grid(row=17, column=0)
        self.system_name.grid(row=17, column=1)
        self.play_alarm_checkbox.grid(row=17, column=2)

        # Test Audio Buttons
        self.test_alarm_button.grid(row=18, column=0, pady=(10, 0))
        self.test_faction_button.grid(row=18, column=1, pady=(10, 0))

        # Sound library — browse buttons for custom sounds
        sound_section_label = customtkinter.CTkLabel(
            self.menu_frame, text="Custom Sounds (WAV files):", justify="left"
        )
        sound_section_label.grid(
            row=19, column=0, columnspan=3, pady=(10, 0), sticky="w", padx=20
        )

        self.alarm_sound_label = customtkinter.CTkLabel(
            self.menu_frame, text="Default", justify="left", width=180
        )
        self.browse_alarm_button = customtkinter.CTkButton(
            self.menu_frame,
            text="Browse Alarm...",
            width=120,
            command=self._browse_alarm_sound,
        )

        self.faction_sound_label = customtkinter.CTkLabel(
            self.menu_frame, text="Default", justify="left", width=180
        )
        self.browse_faction_button = customtkinter.CTkButton(
            self.menu_frame,
            text="Browse Faction...",
            width=120,
            command=self._browse_faction_sound,
        )

        self.browse_alarm_button.grid(row=20, column=0, padx=(20, 4), sticky="e")
        self.alarm_sound_label.grid(row=20, column=1, columnspan=2, sticky="w")
        self.browse_faction_button.grid(row=21, column=0, padx=(20, 4), sticky="e")
        self.faction_sound_label.grid(row=21, column=1, columnspan=2, sticky="w")

        # Hotkey section
        hotkey_section_label = customtkinter.CTkLabel(
            self.menu_frame, text="Hotkeys (e.g. f1, f2, esc, g)", justify="left"
        )
        hotkey_section_label.grid(
            row=22, column=0, columnspan=3, pady=(10, 0), sticky="w", padx=20
        )

        self.hotkey_alert_label = customtkinter.CTkLabel(
            self.menu_frame, text="Alert Region Key:", justify="left"
        )
        self.hotkey_alert_entry = customtkinter.CTkEntry(self.menu_frame, width=80)

        self.hotkey_faction_label = customtkinter.CTkLabel(
            self.menu_frame, text="Faction Region Key:", justify="left"
        )
        self.hotkey_faction_entry = customtkinter.CTkEntry(self.menu_frame, width=80)

        self.hotkey_alert_label.grid(row=23, column=0, padx=(20, 4), sticky="e")
        self.hotkey_alert_entry.grid(row=23, column=1, padx=(0, 20))
        self.hotkey_faction_label.grid(row=24, column=0, padx=(20, 4), sticky="e")
        self.hotkey_faction_entry.grid(row=24, column=1, padx=(0, 20))

        # Intelligence section
        intel_section_label = customtkinter.CTkLabel(
            self.menu_frame,
            text="Intelligence",
            font=customtkinter.CTkFont(weight="bold"),
        )
        intel_section_label.grid(
            row=25, column=0, columnspan=3, pady=(10, 0), sticky="w", padx=20
        )

        self.zkillboard_var = customtkinter.BooleanVar(value=False)
        self.zkillboard_check = customtkinter.CTkCheckBox(
            self.menu_frame,
            text="Enable Zkillboard lookup on alarm",
            variable=self.zkillboard_var,
        )
        self.zkillboard_check.grid(
            row=26, column=0, columnspan=2, padx=(20, 4), sticky="w", pady=4
        )

        self.intel_log_var = customtkinter.BooleanVar(value=False)
        self.intel_log_check = customtkinter.CTkCheckBox(
            self.menu_frame,
            text="Watch EVE intel chat log",
            variable=self.intel_log_var,
        )
        self.intel_log_check.grid(
            row=27, column=0, columnspan=2, padx=(20, 4), sticky="w", pady=4
        )

        intel_channel_label = customtkinter.CTkLabel(
            self.menu_frame, text="Intel Channel:", justify="left"
        )
        self.intel_channel_entry = customtkinter.CTkEntry(self.menu_frame, width=160)
        intel_channel_label.grid(row=28, column=0, padx=(20, 4), sticky="e")
        self.intel_channel_entry.grid(row=28, column=1, padx=(0, 20))

        # ESI section
        esi_section_label = customtkinter.CTkLabel(
            self.menu_frame,
            text="ESI Augmentation",
            font=customtkinter.CTkFont(weight="bold"),
        )
        esi_section_label.grid(
            row=29, column=0, columnspan=3, pady=(10, 0), sticky="w", padx=20
        )

        self.esi_enabled_var = customtkinter.BooleanVar(value=False)
        self.esi_enabled_check = customtkinter.CTkCheckBox(
            self.menu_frame,
            text="Show corp/alliance on Enemy alarm",
            variable=self.esi_enabled_var,
        )
        self.esi_enabled_check.grid(
            row=30, column=0, columnspan=2, padx=(20, 4), sticky="w", pady=4
        )

        self.esi_corp_var = customtkinter.BooleanVar(value=True)
        self.esi_corp_check = customtkinter.CTkCheckBox(
            self.menu_frame, text="Show corporation", variable=self.esi_corp_var
        )
        self.esi_corp_check.grid(row=31, column=0, padx=(20, 4), sticky="w", pady=2)

        self.esi_alliance_var = customtkinter.BooleanVar(value=True)
        self.esi_alliance_check = customtkinter.CTkCheckBox(
            self.menu_frame, text="Show alliance", variable=self.esi_alliance_var
        )
        self.esi_alliance_check.grid(row=31, column=1, padx=(4, 20), sticky="w", pady=2)

        self.esi_flashy_var = customtkinter.BooleanVar(value=False)
        self.esi_flashy_check = customtkinter.CTkCheckBox(
            self.menu_frame,
            text="Alert on flashy pilots (sec status \u2264 -5)",
            variable=self.esi_flashy_var,
        )
        self.esi_flashy_check.grid(
            row=32, column=0, columnspan=2, padx=(20, 4), sticky="w", pady=2
        )

        # Threat Tiers section
        threat_section_label = customtkinter.CTkLabel(
            self.menu_frame,
            text="Threat Tiers",
            font=customtkinter.CTkFont(weight="bold"),
        )
        threat_section_label.grid(
            row=33, column=0, columnspan=3, pady=(10, 0), sticky="w", padx=20
        )

        customtkinter.CTkLabel(
            self.menu_frame, text="Name/Corp/Alliance:", justify="left"
        ).grid(row=34, column=0, padx=(20, 4), sticky="e")
        self.threat_tier_name_entry = customtkinter.CTkEntry(self.menu_frame, width=180)
        self.threat_tier_name_entry.grid(row=34, column=1, sticky="w")

        self.threat_tier_level_var = customtkinter.StringVar(value="red")
        self.threat_tier_level_menu = customtkinter.CTkOptionMenu(
            self.menu_frame,
            variable=self.threat_tier_level_var,
            values=["red", "orange", "yellow"],
            width=90,
        )
        self.threat_tier_level_menu.grid(row=34, column=2, padx=(4, 20), sticky="w")

        threat_btn_frame = customtkinter.CTkFrame(self.menu_frame)
        threat_btn_frame.grid(
            row=35, column=0, columnspan=3, padx=20, pady=(4, 0), sticky="w"
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

        self.threat_tiers_list = customtkinter.CTkScrollableFrame(
            self.menu_frame, height=80
        )
        self.threat_tiers_list.grid(
            row=36, column=0, columnspan=3, padx=20, pady=(4, 0), sticky="ew"
        )
        self._threat_tiers_data: dict = {}
        self._threat_tier_rows: list = []
        self._selected_tier_key: str | None = None

        # Web Status UI section
        web_section_label = customtkinter.CTkLabel(
            self.menu_frame,
            text="Web Status UI",
            font=customtkinter.CTkFont(weight="bold"),
        )
        web_section_label.grid(
            row=37, column=0, columnspan=3, pady=(10, 0), sticky="w", padx=20
        )

        self.web_ui_var = customtkinter.BooleanVar(value=False)
        self.web_ui_check = customtkinter.CTkCheckBox(
            self.menu_frame,
            text="Enable web status server (localhost)",
            variable=self.web_ui_var,
        )
        self.web_ui_check.grid(
            row=38, column=0, columnspan=2, padx=(20, 4), sticky="w", pady=4
        )

        web_port_label = customtkinter.CTkLabel(
            self.menu_frame, text="Port:", justify="left"
        )
        self.web_ui_port_entry = customtkinter.CTkEntry(self.menu_frame, width=70)
        web_port_label.grid(row=39, column=0, padx=(20, 4), sticky="e")
        self.web_ui_port_entry.grid(row=39, column=1, sticky="w", padx=(0, 20))

        # Adjacent System Monitor section
        adj_section_label = customtkinter.CTkLabel(
            self.menu_frame,
            text="Adjacent System Monitor",
            font=customtkinter.CTkFont(weight="bold"),
        )
        adj_section_label.grid(
            row=40, column=0, columnspan=3, pady=(10, 0), sticky="w", padx=20
        )

        self.adjacent_enabled_var = customtkinter.BooleanVar(value=False)
        self.adjacent_enabled_check = customtkinter.CTkCheckBox(
            self.menu_frame,
            text="Monitor kills in neighboring systems",
            variable=self.adjacent_enabled_var,
        )
        self.adjacent_enabled_check.grid(
            row=41, column=0, columnspan=2, padx=(20, 4), sticky="w", pady=4
        )

        # max jumps + min kills on same row
        customtkinter.CTkLabel(self.menu_frame, text="Max jumps:", justify="left").grid(
            row=42, column=0, padx=(20, 4), sticky="e"
        )
        self.adjacent_max_jumps_entry = customtkinter.CTkEntry(
            self.menu_frame, width=50
        )
        self.adjacent_max_jumps_entry.grid(row=42, column=1, sticky="w")

        customtkinter.CTkLabel(self.menu_frame, text="Min kills:", justify="left").grid(
            row=42, column=2, padx=(10, 4), sticky="e"
        )
        self.adjacent_min_kills_entry = customtkinter.CTkEntry(
            self.menu_frame, width=50
        )
        self.adjacent_min_kills_entry.grid(row=42, column=3, sticky="w")

        customtkinter.CTkLabel(
            self.menu_frame, text="Poll interval (s):", justify="left"
        ).grid(row=43, column=0, padx=(20, 4), sticky="e")
        self.adjacent_poll_entry = customtkinter.CTkEntry(self.menu_frame, width=70)
        self.adjacent_poll_entry.grid(row=43, column=1, sticky="w")

        customtkinter.CTkLabel(
            self.menu_frame, text="Destination:", justify="left"
        ).grid(row=44, column=0, padx=(20, 4), sticky="e")
        self.adjacent_dest_entry = customtkinter.CTkEntry(
            self.menu_frame, width=180, placeholder_text="e.g. Jita"
        )
        self.adjacent_dest_entry.grid(row=44, column=1, columnspan=2, sticky="w")
        self.adjacent_check_route_btn = customtkinter.CTkButton(
            self.menu_frame, text="Check Route", width=110, command=self._check_route
        )
        self.adjacent_check_route_btn.grid(row=44, column=3, padx=(4, 20))

        # D-scan Monitor section
        dscan_section_label = customtkinter.CTkLabel(
            self.menu_frame,
            text="D-Scan Monitor",
            font=customtkinter.CTkFont(weight="bold"),
        )
        dscan_section_label.grid(
            row=45, column=0, columnspan=3, pady=(10, 0), sticky="w", padx=20
        )

        self.dscan_enabled_var = customtkinter.BooleanVar(value=False)
        customtkinter.CTkCheckBox(
            self.menu_frame,
            text="Enable D-scan log monitoring",
            variable=self.dscan_enabled_var,
        ).grid(row=46, column=0, columnspan=2, padx=(20, 4), sticky="w", pady=4)

        self.dscan_red_var = customtkinter.BooleanVar(value=True)
        customtkinter.CTkCheckBox(
            self.menu_frame, text="Alert on RED ships", variable=self.dscan_red_var
        ).grid(row=47, column=0, padx=(20, 4), sticky="w", pady=2)

        self.dscan_orange_var = customtkinter.BooleanVar(value=False)
        customtkinter.CTkCheckBox(
            self.menu_frame,
            text="Alert on ORANGE ships",
            variable=self.dscan_orange_var,
        ).grid(row=47, column=1, padx=(4, 20), sticky="w", pady=2)

        self.dscan_probes_var = customtkinter.BooleanVar(value=True)
        customtkinter.CTkCheckBox(
            self.menu_frame,
            text="Alert on probes detected",
            variable=self.dscan_probes_var,
        ).grid(row=48, column=0, columnspan=2, padx=(20, 4), sticky="w", pady=2)

        # KOS Checker section
        customtkinter.CTkLabel(
            self.menu_frame,
            text="KOS Checker",
            font=customtkinter.CTkFont(weight="bold"),
        ).grid(row=49, column=0, columnspan=3, pady=(10, 0), sticky="w", padx=20)

        self.kos_cva_var = customtkinter.BooleanVar(value=True)
        customtkinter.CTkCheckBox(
            self.menu_frame, text="Enable CVA KOS API", variable=self.kos_cva_var
        ).grid(row=50, column=0, columnspan=2, padx=(20, 4), sticky="w", pady=4)

        customtkinter.CTkLabel(
            self.menu_frame, text="Custom KOS URLs:", justify="left"
        ).grid(row=51, column=0, padx=(20, 4), sticky="e")
        self.kos_custom_entry = customtkinter.CTkEntry(
            self.menu_frame, width=340, placeholder_text="comma-separated URLs"
        )
        self.kos_custom_entry.grid(row=51, column=1, columnspan=2, sticky="w")

        # Push Notifications section
        customtkinter.CTkLabel(
            self.menu_frame,
            text="Push Notifications",
            font=customtkinter.CTkFont(weight="bold"),
        ).grid(row=52, column=0, columnspan=3, pady=(10, 0), sticky="w", padx=20)

        push_fields = [
            ("Telegram Token:", "telegram_token_entry", 53),
            ("Telegram Chat ID:", "telegram_chat_entry", 54),
            ("Pushover User:", "pushover_user_entry", 55),
            ("Pushover Token:", "pushover_token_entry", 56),
            ("ntfy.sh URL:", "ntfy_url_entry", 57),
        ]
        for label_text, attr, row_n in push_fields:
            customtkinter.CTkLabel(
                self.menu_frame, text=label_text, justify="left"
            ).grid(row=row_n, column=0, padx=(20, 4), sticky="e")
            entry = customtkinter.CTkEntry(self.menu_frame, width=280)
            entry.grid(row=row_n, column=1, columnspan=2, sticky="w")
            setattr(self, attr, entry)

        customtkinter.CTkLabel(
            self.menu_frame,
            text="Alarm Options",
            font=customtkinter.CTkFont(weight="bold"),
        ).grid(row=58, column=0, columnspan=3, pady=(10, 0), sticky="w", padx=20)

        self.auto_screenshot_var = customtkinter.BooleanVar(value=False)
        customtkinter.CTkCheckBox(
            self.menu_frame,
            text="Auto-screenshot on alarm",
            variable=self.auto_screenshot_var,
        ).grid(row=59, column=0, columnspan=2, padx=(20, 4), sticky="w", pady=4)

        customtkinter.CTkLabel(
            self.menu_frame, text="Escalate at N hostiles:", justify="left"
        ).grid(row=60, column=0, padx=(20, 4), sticky="e")
        self.escalation_threshold_entry = customtkinter.CTkEntry(
            self.menu_frame, width=60
        )
        self.escalation_threshold_entry.grid(row=60, column=1, sticky="w")
        customtkinter.CTkLabel(self.menu_frame, text="(0 = off)", justify="left").grid(
            row=60, column=2, padx=(4, 20), sticky="w"
        )

        # Save / Apply / Close
        self.save_button.grid(row=61, column=0, pady=10)
        self.apply_button.grid(row=61, column=1, pady=10)
        self.close_button.grid(row=61, column=2, pady=10)

        self.setting_window.protocol("WM_DELETE_WINDOW", self.clean_up)

    def open_menu(self):
        """Opens the settings window."""
        if not self.is_open:
            self._ensure_window()
            self.open = True
            self.main.mainmenu_buttons.setting_menu.configure(
                fg_color="#fa0202", hover_color="#bd291e"
            )

            config_menu_x = self.main.winfo_x()
            config_menu_y = self.main.winfo_y()
            config_menu_width = self.main.winfo_width()
            config_menu_height = self.main.winfo_height()

            config_window_width = 650
            config_window_height = 1200

            raw_x = config_menu_x + config_menu_width + 10
            raw_y = config_menu_y + config_menu_height + 40

            # Clamp to screen bounds so popup never opens off-screen
            screen_w = self.main.winfo_screenwidth()
            screen_h = self.main.winfo_screenheight()
            window_x = min(raw_x, screen_w - config_window_width - 10)
            window_y = min(max(raw_y, 10), screen_h - config_window_height - 10)

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
