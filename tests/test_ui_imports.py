"""Smoke-import every evealert.ui module to catch SyntaxErrors and bad imports.

Uses the offscreen Qt platform so no display is needed in CI.
"""

import importlib
import os

import pytest

# Required so PySide6 doesn't try to open a real display
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# All importable (non-dunder) modules under evealert/ui/
_UI_MODULES = [
    "app",
    "bug_reporter",
    "config_dialog",
    "hotkey_edit",
    "image_manager",
    "log_pane",
    "main_window",
    "notification_wizard",
    "onboarding_wizard",
    "profile_manager",
    "qt_bridge",
    "region_overlay",
    "settings_dialog",
    "statistics_window",
    "theme",
    "threshold_editor",
    "tray",
    "update_dialog",
]


@pytest.mark.parametrize("mod", _UI_MODULES)
def test_ui_module_imports(mod):
    """Each UI module must import without error (catches SyntaxError, bad imports)."""
    importlib.import_module(f"evealert.ui.{mod}")
