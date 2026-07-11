"""Cross-platform EVE Online window detection.

Attempts to locate the EVE Online client window and return its screen
bounds so the alert regions can be pre-populated without manual selection.
"""

import logging
import platform
import re
from typing import Optional, Tuple

logger = logging.getLogger("tools")

# Window title fragments to search for (EVE client uses different titles
# depending on login state and client version)
_EVE_TITLE_FRAGMENTS = [
    "EVE Online",
    "EVE - Logged in",
    "EVE - ",  # catches "EVE - <character name>"
]

WindowBounds = Tuple[int, int, int, int]  # (left, top, width, height)


def find_eve_window() -> Optional[WindowBounds]:
    """Return (left, top, width, height) of the EVE Online window.

    Returns None if the EVE client window is not found or if the platform
    is not supported.
    """
    system = platform.system()
    if system == "Windows":
        return _find_windows()
    if system == "Darwin":
        return _find_macos()
    logger.debug("EVE window auto-detect not supported on %s", system)
    return None


def _find_windows() -> Optional[WindowBounds]:
    """Windows implementation using pygetwindow."""
    try:
        import pygetwindow as gw  # pylint: disable=import-outside-toplevel
    except ImportError:
        logger.warning(
            "pygetwindow not installed — EVE auto-detect unavailable. "
            "Install with: pip install pygetwindow"
        )
        return None

    for fragment in _EVE_TITLE_FRAGMENTS:
        try:
            windows = gw.getWindowsWithTitle(fragment)
            for win in windows:
                if win.width > 0 and win.height > 0:
                    logger.debug(
                        "Found EVE window: '%s' at (%d,%d) %dx%d",
                        win.title,
                        win.left,
                        win.top,
                        win.width,
                        win.height,
                    )
                    return win.left, win.top, win.width, win.height
        except Exception as e:
            logger.debug("pygetwindow search error: %s", e)
    return None


def _find_macos() -> Optional[WindowBounds]:
    """macOS implementation using osascript."""
    import subprocess  # pylint: disable=import-outside-toplevel

    # Try each possible process name
    for process_name in ("EVE Online", "EVE"):
        script = f"""
        tell application "System Events"
            if exists process "{process_name}" then
                set win to first window of process "{process_name}"
                set pos to position of win
                set sz to size of win
                return (item 1 of pos) & "," & (item 2 of pos) & "," & (item 1 of sz) & "," & (item 2 of sz)
            end if
        end tell
        """
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = result.stdout.strip()
            if output:
                parts = [
                    int(x.strip()) for x in re.split(r"[,\s]+", output) if x.strip()
                ]
                if len(parts) == 4:
                    left, top, width, height = parts
                    logger.debug(
                        "Found EVE window via osascript at (%d,%d) %dx%d",
                        left,
                        top,
                        width,
                        height,
                    )
                    return left, top, width, height
        except Exception as e:
            logger.debug("osascript EVE detection error: %s", e)

    return None
