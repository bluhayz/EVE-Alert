from typing import TYPE_CHECKING, Optional, Tuple

import mss
import numpy as np

from evealert.settings.logger import logging

if TYPE_CHECKING:
    from evealert.menu.main import MainMenu

logger = logging.getLogger("tools")


class WindowCapture:
    """Handles screen capture for specified regions.

    The mss instance is created lazily on first use inside the alert thread.
    mss uses OS-level handles (DirectX on Windows) that are bound to the
    thread that created them, so it must NOT be created in the GUI thread
    and then used in the background alert thread.
    """

    def __init__(self, mainmenu: "MainMenu"):
        self.main = mainmenu
        self._sct: Optional[mss.base.MSSBase] = None

    def _get_sct(self) -> mss.base.MSSBase:
        """Return the mss instance, creating it in this thread on first call."""
        if self._sct is None:
            self._sct = mss.mss()
        return self._sct

    def close(self) -> None:
        """Release the underlying mss resources."""
        if self._sct is not None:
            self._sct.close()
            self._sct = None

    def get_screenshot_value(
        self, y1: int, x1: int, x2: int, y2: int
    ) -> Tuple[Optional[np.ndarray], Optional[mss.screenshot.ScreenShot]]:
        """Capture a screenshot of the specified region.

        Args:
            y1: Top coordinate
            x1: Left coordinate
            x2: Right coordinate (exclusive)
            y2: Bottom coordinate (exclusive)

        Returns:
            Tuple of (numpy_array, raw_screenshot) or (None, None) on error
        """
        monitor = {"top": y1, "left": x1, "width": x2 - x1, "height": y2 - y1}
        try:
            screenshot = self._get_sct().grab(monitor)
        except Exception as e:
            logger.error("Screenshot capture failed: %s", e)
            return None, None

        # Convert directly to NumPy array and keep only RGB channels
        img_array = np.array(screenshot)[:, :, :3]  # Drop alpha channel

        return img_array, screenshot
