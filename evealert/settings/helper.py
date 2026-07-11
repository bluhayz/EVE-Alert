"""Helper utilities for EVE Alert application.

Provides resource path resolution and constants.
"""

import sys
from pathlib import Path

from platformdirs import user_config_dir

# Path to application icon
ICON = "img/eve.ico"
ICON_PNG = "img/eve.png"

# Absolute path to the evealert package root
PACKAGE_ROOT = Path(__file__).resolve().parent.parent

# Directory containing the running executable/script (writable location)
EXEC_ROOT = Path(sys.argv[0]).resolve().parent


def get_resource_path(relative_path: str) -> str:
    """Get the absolute path to a resource file.

    For PyInstaller --onefile builds, assets are extracted to sys._MEIPASS
    (a per-run temp directory). For --onedir builds sys._MEIPASS is the app
    directory. In development, resolves from the package root.

    Args:
        relative_path: Path like "sound/alarm.wav" or "img/icon.png"

    Returns:
        Absolute path to the resource file
    """
    if not relative_path:
        raise ValueError("relative_path must be provided")

    relative = Path(relative_path)

    if relative.is_absolute():
        return str(relative)

    # PyInstaller frozen build: use sys._MEIPASS (works for both --onefile
    # and --onedir). Falls back to exe directory if attribute is missing.
    if getattr(sys, "frozen", False):
        base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return str((base_dir / relative).resolve())

    # Development mode: strip 'evealert/' prefix and use PACKAGE_ROOT
    relative_stripped = relative
    if relative.parts and relative.parts[0].lower() == "evealert":
        relative_stripped = Path(*relative.parts[1:])

    resource_path = (PACKAGE_ROOT / relative_stripped).resolve()

    # Fallback to EXEC_ROOT if not found in PACKAGE_ROOT
    if not resource_path.exists():
        resource_path = (EXEC_ROOT / relative_stripped).resolve()

    return str(resource_path)


def get_settings_path() -> str:
    """Return the platform-appropriate path for settings.json.

    - Windows:  %APPDATA%\\evealert\\settings.json
    - macOS:    ~/Library/Application Support/evealert/settings.json
    - Linux:    ~/.config/evealert/settings.json
    """
    config_dir = Path(user_config_dir("evealert"))
    config_dir.mkdir(parents=True, exist_ok=True)
    return str(config_dir / "settings.json")


def get_user_img_path() -> Path:
    """Return the user's writable image directory (alongside settings.json).

    Template images placed here are scanned alongside the bundled images
    so users can add custom detection templates without modifying the install.
    """
    img_dir = Path(user_config_dir("evealert")) / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    return img_dir


def get_user_plugins_path() -> Path:
    """Return the user plugins directory, creating it on first use.

    Drop any ``.py`` file here to have it loaded as a plugin on next start.
    """
    plugins_dir = Path(user_config_dir("evealert")) / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    return plugins_dir
