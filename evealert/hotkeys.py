"""Hotkey configuration utilities for EVE Alert.

Provides parsing and display helpers for keyboard shortcuts stored as
plain strings in settings.json (e.g. "f1", "f2", "esc", "a").
"""

from pynput import keyboard


def _key(name: str):
    """Return the pynput Key constant, or None if not available on this platform."""
    return getattr(keyboard.Key, name, None)


# Mapping from user-readable string to pynput Key constant.
# Values may be None on platforms where a key doesn't exist.
_SPECIAL_KEY_MAP: dict = {
    k: v
    for k, v in {
        "f1": _key("f1"),
        "f2": _key("f2"),
        "f3": _key("f3"),
        "f4": _key("f4"),
        "f5": _key("f5"),
        "f6": _key("f6"),
        "f7": _key("f7"),
        "f8": _key("f8"),
        "f9": _key("f9"),
        "f10": _key("f10"),
        "f11": _key("f11"),
        "f12": _key("f12"),
        "esc": _key("esc"),
        "escape": _key("esc"),
        "enter": _key("enter"),
        "return": _key("enter"),
        "space": _key("space"),
        "tab": _key("tab"),
        "backspace": _key("backspace"),
        "delete": _key("delete"),
        "del": _key("delete"),
        "insert": _key("insert"),
        "home": _key("home"),
        "end": _key("end"),
        "up": _key("up"),
        "down": _key("down"),
        "left": _key("left"),
        "right": _key("right"),
        "page_up": _key("page_up"),
        "page_down": _key("page_down"),
        "pageup": _key("page_up"),
        "pagedown": _key("page_down"),
        "ctrl": _key("ctrl"),
        "shift": _key("shift"),
        "alt": _key("alt"),
    }.items()
    if v is not None  # drop keys unavailable on this platform
}

# Default hotkeys shipped with the app
DEFAULT_HOTKEYS: dict[str, str] = {
    "alert_region":  "f1",
    "faction_region": "f2",
    "profile_cycle":  "f3",
    "status_readout": "f4",
}


def parse_hotkey(key_str: str):
    """Convert a settings string to the pynput key object for comparison.

    Returns a pynput Key or KeyCode, or None if the string is empty/invalid.
    """
    if not key_str:
        return None
    s = key_str.strip().lower()
    if s in _SPECIAL_KEY_MAP:
        return _SPECIAL_KEY_MAP[s]
    # Single character keys
    if len(s) == 1:
        return keyboard.KeyCode.from_char(s)
    return None


def key_matches(pynput_key, key_str: str) -> bool:
    """Return True if the pynput event key matches the configured key string."""
    if not key_str:
        return False
    target = parse_hotkey(key_str)
    if target is None:
        return False
    return pynput_key == target
