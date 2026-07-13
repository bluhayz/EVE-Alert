"""GUI-independent settings persistence for EVE Alert (Phase 0, issue #124).

SettingsStore owns the JSON load/merge/save cycle and the `changed` flag
that AlertAgent polls to know when to reload.  The singleton returned by
get_settings_store() is shared by both the GUI layer (SettingMenu) and
the engine (AlertAgent) so the flag propagates correctly.
"""

import copy
import json
import os
import tempfile

from evealert.settings.helper import get_settings_path
from evealert.settings.logger import logging

logger = logging.getLogger("settings")

# ---------------------------------------------------------------------------
# Re-exported from evealert.menu.setting (authoritative copy lives here)
# ---------------------------------------------------------------------------
# imported lazily below to avoid a circular import at module level
# (setting.py imports constants and hotkeys before this module existed)


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


class SettingsStore:
    """Load, merge, and save settings.json — no GUI dependency.

    One instance is shared app-wide via get_settings_store().  The
    ``changed`` flag is set to True by save() and cleared by the engine
    after it has reloaded its internal state.
    """

    def __init__(self, path=None):
        self._path = str(path) if path is not None else None
        self.changed: bool = False
        self._cache: dict = {}

    def _resolve_path(self) -> str:
        return self._path if self._path is not None else str(get_settings_path())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> dict:
        """Read settings.json, merge with defaults, apply active profile.

        Returns the resolved settings dict and updates the internal cache
        so that ``get()`` reflects the freshly loaded values.
        """
        from evealert.menu.setting import DEFAULT_SETTINGS  # lazy import avoids circular dep

        config_path = self._resolve_path()
        try:
            with open(config_path, encoding="utf-8") as f:
                raw = json.load(f)
            settings = self._merge(raw, DEFAULT_SETTINGS)
        except (OSError, json.JSONDecodeError):
            logger.debug("SettingsStore: cannot read %s — using defaults", config_path)
            settings = copy.deepcopy(DEFAULT_SETTINGS)

        # Apply active profile overlay
        active = settings.get("active_profile", "Default")
        profiles = settings.get("profiles", {})
        if active in profiles:
            for key, value in profiles[active].items():
                if isinstance(value, dict) and isinstance(settings.get(key), dict):
                    settings[key] = {**settings[key], **value}
                else:
                    settings[key] = value

        self._cache = settings
        return settings

    def save(self, settings: dict) -> None:
        """Atomically write settings to disk and set the changed flag."""
        config_path = self._resolve_path()
        try:
            dir_name = os.path.dirname(config_path)
            os.makedirs(dir_name, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=dir_name,
                delete=False,
                suffix=".tmp",
            ) as tmp:
                json.dump(settings, tmp, indent=4)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp.name, config_path)
        except OSError as e:
            logger.error("SettingsStore: failed to save settings: %s", e)
            return
        self._cache = settings
        self.changed = True

    def get(self, path: str, default=None):
        """Read a dotted-path value from the last-loaded (cached) settings."""
        return _get_by_path(self._cache, path, default)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _merge(self, settings: dict, defaults: dict) -> dict:
        """Recursively merge *settings* with *defaults*.

        User-only keys (profiles, per-image thresholds, etc.) are preserved;
        missing default keys are deep-copied in.  A default value of ``{}``
        does NOT wipe a populated sub-dict (fixes issues #99/#108).
        """
        merged: dict = {}
        for key in set(defaults) | set(settings):
            if key in defaults and key in settings:
                dval, sval = defaults[key], settings[key]
                if isinstance(dval, dict) and isinstance(sval, dict):
                    merged[key] = self._merge(sval, dval)
                else:
                    merged[key] = sval
            elif key in settings:
                merged[key] = settings[key]
            else:
                merged[key] = copy.deepcopy(defaults[key])
        return merged


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: SettingsStore | None = None


def get_settings_store() -> SettingsStore:
    """Return the shared SettingsStore singleton (created on first call)."""
    global _store
    if _store is None:
        _store = SettingsStore()
    return _store


def reset_settings_store(path=None) -> SettingsStore:
    """Replace the singleton — used in tests to inject a temp-file path."""
    global _store
    _store = SettingsStore(path)
    return _store
