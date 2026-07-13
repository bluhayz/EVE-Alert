"""Pre-configured space profiles for EVE Alert (#143).

A *profile* is a named collection of settings overrides tuned to a specific
EVE space type.  Applying a profile writes the relevant keys to SettingsStore
and triggers a live reload without restarting the agent.

Built-in profiles
-----------------
nullsec  — Solo/small-gang null-sec ratting: D-scan on, all tiers, 2 min rearm,
           low escalation threshold, zKB + KOS enabled.
wormhole — Wormhole space: D-scan on, probes important, higher rearm cadence,
           TTS enabled, harder escalation threshold.
highsec  — High-sec missioning: minimal alerts, D-scan off, escalation only on
           large gangs, KOS off.

Hotkey cycling (F3) is wired up by the Qt main window — this module exposes
the profile list and apply() function only.
"""

from __future__ import annotations

from typing import Any


# ----- Profile definitions -----------------------------------------------

PROFILES: dict[str, dict[str, Any]] = {
    "nullsec": {
        # Display
        "label": "Null-sec",
        "description": "Solo/small-gang null-sec ratting",
        # Settings to override
        "dscan.enabled":              True,
        "dscan.alert_red":            True,
        "dscan.alert_orange":         True,
        "dscan.alert_probes":         True,
        "notifications.escalation_threshold": 1,
        "alerts.rearm_minutes":       2,
        "notifications.tts_enabled":  False,
        "intelligence.zkillboard_enabled": True,
        "kos.cva_kos_enabled":        True,
    },
    "wormhole": {
        "label": "Wormhole",
        "description": "Wormhole space — probes and cloakies matter",
        "dscan.enabled":              True,
        "dscan.alert_red":            True,
        "dscan.alert_orange":         True,
        "dscan.alert_probes":         True,
        "notifications.escalation_threshold": 1,
        "alerts.rearm_minutes":       5,
        "notifications.tts_enabled":  True,
        "notifications.tts_rate":     175,
        "intelligence.zkillboard_enabled": True,
        "kos.cva_kos_enabled":        False,
    },
    "highsec": {
        "label": "High-sec",
        "description": "High-sec missioning — low-noise mode",
        "dscan.enabled":              False,
        "dscan.alert_red":            True,
        "dscan.alert_orange":         False,
        "dscan.alert_probes":         False,
        "notifications.escalation_threshold": 5,
        "alerts.rearm_minutes":       0,
        "notifications.tts_enabled":  False,
        "intelligence.zkillboard_enabled": False,
        "kos.cva_kos_enabled":        False,
    },
}

# Ordered list of profile keys for F3 cycling
PROFILE_CYCLE: list[str] = ["nullsec", "wormhole", "highsec"]


def apply_profile(profile_key: str) -> str:
    """Apply the named profile to SettingsStore and save.

    Returns the human-readable profile label.

    Raises:
        KeyError: if *profile_key* is not in PROFILES.
    """
    from evealert.settings.store import get_settings_store  # noqa: PLC0415

    if profile_key not in PROFILES:
        raise KeyError(f"Unknown profile: {profile_key!r}")

    store = get_settings_store()
    profile = PROFILES[profile_key]

    for path, value in profile.items():
        if path in ("label", "description"):
            continue
        store.set(path, value)

    store.save()
    return profile["label"]


def next_profile(current: str | None) -> str:
    """Return the next profile key in the cycle (wraps around).

    If *current* is not in the cycle, returns the first profile.
    """
    try:
        idx = PROFILE_CYCLE.index(current)
        return PROFILE_CYCLE[(idx + 1) % len(PROFILE_CYCLE)]
    except (ValueError, TypeError):
        return PROFILE_CYCLE[0]
