"""Declarative field registry and apply/save helpers for EVE Alert settings.

Moved from evealert/menu/setting.py in Phase 7 (issue #131).
"""

from collections import namedtuple

from evealert.constants import DEFAULT_COOLDOWN_TIMER
from evealert.hotkeys import DEFAULT_HOTKEYS

# ---------------------------------------------------------------------------
# FieldSpec — drives auto-generation of settings controls in any UI toolkit
# ---------------------------------------------------------------------------
FieldSpec = namedtuple("FieldSpec", "path kind tab section label attr default")

FIELDS: list = [
    # --- Detection tab -------------------------------------------------------
    FieldSpec("dscan.enabled", "bool", "Detection", "D-Scan Monitor",
              "Enable D-scan log monitoring", "dscan_enabled_var", False),
    FieldSpec("dscan.alert_red", "bool", "Detection", "D-Scan Monitor",
              "Alert on RED ships", "dscan_red_var", True),
    FieldSpec("dscan.alert_orange", "bool", "Detection", "D-Scan Monitor",
              "Alert on ORANGE ships", "dscan_orange_var", False),
    FieldSpec("dscan.alert_probes", "bool", "Detection", "D-Scan Monitor",
              "Alert on probes detected", "dscan_probes_var", True),
    FieldSpec("dscan.alert_new_signatures", "bool", "Detection", "D-Scan Monitor",
              "Alert when cosmic signature count increases (possible WH connection)", "dscan_new_sig_var", True),
    # --- Alerts & Sound tab --------------------------------------------------
    FieldSpec("notifications.auto_screenshot", "bool", "Alerts & Sound", "Alarm Options",
              "Auto-screenshot on alarm", "auto_screenshot_var", False),
    FieldSpec("notifications.escalation_threshold", "int", "Alerts & Sound", "Alarm Options",
              "Escalate at N hostiles (0 = off)", "escalation_threshold_entry", 0),
    # Re-arm after sustained presence (#144)
    FieldSpec("alerts.rearm_minutes", "int", "Alerts & Sound", "Alarm Options",
              "Re-alert after N minutes of continuous presence (0 = off)", "rearm_minutes_entry", 0),
    # Automation bridge (#153)
    FieldSpec("automation.enabled", "bool", "Alerts & Sound", "Automation Bridge",
              "POST alarm JSON to webhook_url on each alarm", "automation_enabled_var", False),
    FieldSpec("automation.webhook_url", "str", "Alerts & Sound", "Automation Bridge",
              "Localhost URL to receive alarm POSTs (e.g. http://127.0.0.1:9999/alarm)",
              "automation_url_entry", ""),
    # Text-to-speech (#139)
    FieldSpec("notifications.tts_enabled", "bool", "Alerts & Sound", "Text-to-Speech",
              "Speak alarm details aloud (requires pyttsx3)", "tts_enabled_var", False),
    FieldSpec("notifications.tts_rate", "int", "Alerts & Sound", "Text-to-Speech",
              "Speech rate (words/min, 50–400)", "tts_rate_entry", 175),
    # --- Intel & ESI tab -----------------------------------------------------
    FieldSpec("intelligence.zkillboard_enabled", "bool", "Intel & ESI", "Intelligence",
              "Enable Zkillboard lookup on alarm", "zkillboard_var", False),
    FieldSpec("intelligence.intel_log_enabled", "bool", "Intel & ESI", "Intelligence",
              "Watch EVE intel chat log", "intel_log_var", False),
    FieldSpec("intelligence.intel_log_channel", "str", "Intel & ESI", "Intelligence",
              "Intel Channel", "intel_channel_entry", ""),
    FieldSpec("esi.enabled", "bool", "Intel & ESI", "ESI Augmentation",
              "Show corp/alliance on Enemy alarm", "esi_enabled_var", False),
    FieldSpec("esi.show_corp", "bool", "Intel & ESI", "ESI Augmentation",
              "Show corporation", "esi_corp_var", True),
    FieldSpec("esi.show_alliance", "bool", "Intel & ESI", "ESI Augmentation",
              "Show alliance", "esi_alliance_var", True),
    FieldSpec("esi.alert_flashy", "bool", "Intel & ESI", "ESI Augmentation",
              "Alert on flashy pilots (sec status \u2264 -5)", "esi_flashy_var", False),
    FieldSpec("kos.cva_enabled", "bool", "Intel & ESI", "KOS Checker",
              "Enable CVA KOS API", "kos_cva_var", True),
    FieldSpec("adjacent.enabled", "bool", "Intel & ESI", "Adjacent System Monitor",
              "Monitor kills in neighboring systems", "adjacent_enabled_var", False),
    FieldSpec("adjacent.max_jumps", "int", "Intel & ESI", "Adjacent System Monitor",
              "Max jumps", "adjacent_max_jumps_entry", 3),
    FieldSpec("adjacent.min_kills", "int", "Intel & ESI", "Adjacent System Monitor",
              "Min kills", "adjacent_min_kills_entry", 1),
    FieldSpec("adjacent.poll_interval", "int", "Intel & ESI", "Adjacent System Monitor",
              "Poll interval (s)", "adjacent_poll_entry", 120),
    FieldSpec("adjacent.destination_system", "str", "Intel & ESI", "Adjacent System Monitor",
              "Destination", "adjacent_dest_entry", ""),
    FieldSpec("esi_oauth.client_id", "str", "Intel & ESI", "EVE SSO / ESI OAuth",
              "Client ID", "esi_client_id_entry", ""),
    FieldSpec("esi_oauth.standings_auto_classify", "bool", "Intel & ESI", "EVE SSO / ESI OAuth",
              "Auto-classify standing contacts in Local", "esi_standings_var", False),
    # Standings-aware filter (#147)
    FieldSpec("esi_oauth.standings_filter_blues", "bool", "Intel & ESI", "EVE SSO / ESI OAuth",
              "Filter out allied pilots (standing ≥+5) from threat display", "esi_filter_blues_var", False),
    FieldSpec("esi_oauth.fleet_monitor", "bool", "Intel & ESI", "EVE SSO / ESI OAuth",
              "Display fleet membership on start", "esi_fleet_var", False),
    FieldSpec("esi_oauth.structure_alerts", "bool", "Intel & ESI", "EVE SSO / ESI OAuth",
              "Warn on structure fuel < 7 days", "esi_structure_var", False),
    FieldSpec("ocr.enabled", "bool", "Intel & ESI", "OCR Name Detection",
              "Read pilot names from Local on alarm (needs Tesseract)", "ocr_enabled_var", False),
    FieldSpec("ocr.region.x1", "int", "Intel & ESI", "OCR Name Detection",
              "Region X1 (0 = use alert region)", "ocr_x1_entry", 0),
    FieldSpec("ocr.region.y1", "int", "Intel & ESI", "OCR Name Detection",
              "Region Y1", "ocr_y1_entry", 0),
    FieldSpec("ocr.region.x2", "int", "Intel & ESI", "OCR Name Detection",
              "Region X2", "ocr_x2_entry", 0),
    FieldSpec("ocr.region.y2", "int", "Intel & ESI", "OCR Name Detection",
              "Region Y2", "ocr_y2_entry", 0),
    # --- Notifications tab ---------------------------------------------------
    FieldSpec("push.telegram_token", "str", "Notifications", "Push Notifications",
              "Telegram Token", "telegram_token_entry", ""),
    FieldSpec("push.telegram_chat_id", "str", "Notifications", "Push Notifications",
              "Telegram Chat ID", "telegram_chat_entry", ""),
    FieldSpec("push.pushover_user", "str", "Notifications", "Push Notifications",
              "Pushover User", "pushover_user_entry", ""),
    FieldSpec("push.pushover_token", "str", "Notifications", "Push Notifications",
              "Pushover Token", "pushover_token_entry", ""),
    FieldSpec("push.ntfy_url", "str", "Notifications", "Push Notifications",
              "ntfy.sh URL", "ntfy_url_entry", ""),
    FieldSpec("web_ui.enabled", "bool", "Notifications", "Web Status UI",
              "Enable web status server (localhost)", "web_ui_var", False),
    FieldSpec("web_ui.port", "int", "Notifications", "Web Status UI",
              "Port", "web_ui_port_entry", 8765),
    # --- Wormhole & Fleet tab ------------------------------------------------
    FieldSpec("wormhole.thera_enabled", "bool", "Wormhole & Fleet", "Wormhole Awareness",
              "Monitor Thera connections (Eve-Scout)", "thera_enabled_var", False),
    FieldSpec("wormhole.thera_max_jumps", "int", "Wormhole & Fleet", "Wormhole Awareness",
              "Thera max jumps", "thera_max_jumps_entry", 5),
    FieldSpec("wormhole.wh_drop_enabled", "bool", "Wormhole & Fleet", "Wormhole Awareness",
              "Alert on WH drop pattern", "wh_drop_enabled_var", False),
    FieldSpec("wormhole.wh_drop_threshold", "int", "Wormhole & Fleet", "Wormhole Awareness",
              "Drop threshold (pilots)", "wh_drop_threshold_entry", 3),
    FieldSpec("fleet.composition_enabled", "bool", "Wormhole & Fleet", "Fleet Context",
              "Analyse fleet composition (3+ hostiles)", "fleet_composition_var", False),
    FieldSpec("fleet.killmail_enabled", "bool", "Wormhole & Fleet", "Fleet Context",
              "Notify on tracked character kills/losses", "fleet_killmail_var", False),
    # --- Diagnostics (Alerts & Sound tab) ------------------------------------
    FieldSpec("diagnostics.enabled", "bool", "Alerts & Sound", "Diagnostics",
              "Enable diagnostic (verbose) logging", "diagnostics_enabled_var", False),
]

TAB_ORDER = [
    "Detection",
    "Alerts & Sound",
    "Intel & ESI",
    "Notifications",
    "Wormhole & Fleet",
]


# ---------------------------------------------------------------------------
# Registry apply/save helpers (toolkit-agnostic, used by tests + Qt dialog)
# ---------------------------------------------------------------------------

def apply_registry_fields(obj, settings: dict) -> None:
    """Populate registry-backed widget attributes on *obj* from *settings*.

    *obj* must have attributes named by ``spec.attr`` that respond to:
      - ``.set(value)`` for bool fields
      - ``.delete(0, END); .insert(0, str(value))`` for int/str fields
    """
    from evealert.settings.store import _get_by_path  # noqa: PLC0415
    for spec in FIELDS:
        widget = getattr(obj, spec.attr, None)
        if widget is None:
            continue
        value = _get_by_path(settings, spec.path, spec.default)
        if spec.kind == "bool":
            widget.set(bool(value))
        else:
            widget.delete(0, "end")
            widget.insert(0, str(value))


def save_registry_fields(obj, settings_out: dict) -> None:
    """Write registry-backed widget values from *obj* into *settings_out*.

    *obj* must have attributes named by ``spec.attr`` with a ``.get()`` method.
    """
    from evealert.settings.store import _set_by_path  # noqa: PLC0415
    for spec in FIELDS:
        widget = getattr(obj, spec.attr, None)
        if widget is None:
            continue
        if spec.kind == "bool":
            value = bool(widget.get())
        elif spec.kind == "int":
            raw = str(widget.get()).strip()
            value = int(raw) if raw.lstrip("-").isdigit() else int(spec.default)
        else:
            value = str(widget.get()).strip()
        _set_by_path(settings_out, spec.path, value)
