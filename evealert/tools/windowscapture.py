from typing import Optional, Protocol, Tuple

import mss
import numpy as np

from evealert.settings.logger import logging

logger = logging.getLogger("tools")


class CaptureBackend(Protocol):
    """Common interface every screen-capture backend implements (#176).

    grab() must return a BGR (no alpha) numpy array for *monitor* -- the
    exact shape/channel-order mss has always produced, since Vision.find()
    doesn't know or care which backend supplied the frame. Returning None
    means "no frame available this call" (a genuine, non-error condition
    for dxcam, whose Desktop Duplication API can report "nothing changed
    since the last grab").
    """

    def grab(self, monitor: dict) -> Optional[np.ndarray]: ...

    def close(self) -> None: ...


class MssBackend:
    """The original (pre-#176) capture path: mss, a GDI screenshotter.

    The mss instance is created lazily on first use inside the alert
    thread. mss uses OS-level handles (DirectX on Windows) that are bound
    to the thread that created them, so it must NOT be created in the GUI
    thread and then used in the background alert thread.
    """

    def __init__(self) -> None:
        self._sct: Optional[mss.base.MSSBase] = None

    def _get_sct(self) -> mss.base.MSSBase:
        if self._sct is None:
            self._sct = mss.mss()
        return self._sct

    def grab(self, monitor: dict) -> Optional[np.ndarray]:
        screenshot = self._get_sct().grab(monitor)
        return np.array(screenshot)[:, :, :3]  # drop alpha

    def close(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None


class DxcamBackend:
    """Windows Desktop Duplication API capture via the optional `dxcam`
    package (#176) -- typically 3-10x faster than mss's GDI path at high
    capture rates, the right backend for multi-client (#174).

    Quirks handled here:
    - dxcam.create(output_color="BGR") matches mss's channel order/shape
      exactly, so Vision.find() sees identical arrays regardless of
      backend.
    - camera.grab() returns None when the Desktop Duplication API reports
      no change since the last grab (not an error) -- the last successful
      full frame is cached and reused for the crop in that case, so a
      static screen behaves the same as a fresh identical capture would
      (and composes naturally with vision.py's own frame-change cache,
      #175).
    - dxcam's region coordinates are relative to the OUTPUT (monitor) the
      camera was created for, unlike mss's virtual-desktop-absolute
      coordinates. This backend targets output_idx=0 (primary monitor) --
      EVE windows on a secondary monitor may capture the wrong area. The
      "auto"/"dxcam" capture_backend setting exists precisely so a user
      who hits this can force `detection.capture_backend: "mss"`.
    """

    def __init__(self) -> None:
        self._camera = None
        self._last_frame: Optional[np.ndarray] = None

    def _get_camera(self):
        if self._camera is None:
            import dxcam  # noqa: PLC0415 -- optional dependency, [capture-dx] extra

            self._camera = dxcam.create(output_idx=0, output_color="BGR")
        return self._camera

    def grab(self, monitor: dict) -> Optional[np.ndarray]:
        camera = self._get_camera()
        frame = camera.grab()
        if frame is None:
            frame = self._last_frame
        else:
            self._last_frame = frame
        if frame is None:
            return None

        top, left = monitor["top"], monitor["left"]
        bottom, right = top + monitor["height"], left + monitor["width"]
        return frame[top:bottom, left:right]

    def close(self) -> None:
        if self._camera is not None:
            try:
                self._camera.stop()
            except Exception as exc:
                logger.debug("dxcam camera stop failed: %s", exc)
            self._camera = None
        self._last_frame = None


def _try_create_dxcam_backend() -> Optional[DxcamBackend]:
    """Construct a DxcamBackend and confirm dxcam actually imports/inits
    successfully, or return None. Import/init failures (package not
    installed, no compatible GPU/driver, non-Windows) are all treated the
    same way: fall back to mss rather than crash (#176 acceptance
    criterion)."""
    backend = DxcamBackend()
    try:
        backend._get_camera()  # noqa: SLF001 -- forces the lazy import/init now
    except Exception as exc:
        logger.debug("dxcam backend unavailable: %s", exc)
        return None
    return backend


class WindowCapture:
    """Handles screen capture for specified regions.

    Delegates to a swappable CaptureBackend (#176: mss or dxcam). The
    backend is created lazily on first use inside the alert thread --
    same thread-affinity requirement mss always had, and dxcam's Desktop
    Duplication handles are thread-affine too.
    """

    def __init__(self, mainmenu=None, backend: str = "mss"):
        # mainmenu kept for API compat; not used
        self.main = mainmenu
        self._backend_name = backend
        self._backend: Optional[CaptureBackend] = None
        self._warned_fallback = False

    def set_backend(self, backend: str) -> None:
        """Reconfigure which capture backend to use (#176), e.g. on a
        settings hot-reload. Closes the previous backend first so its
        OS-level handles don't leak; the new one is created lazily on the
        next capture call, in the alert thread."""
        if backend == self._backend_name and self._backend is not None:
            return
        if self._backend is not None:
            self._backend.close()
            self._backend = None
        self._backend_name = backend

    def _get_backend(self) -> CaptureBackend:
        if self._backend is not None:
            return self._backend

        if self._backend_name in ("dxcam", "auto"):
            dxcam_backend = _try_create_dxcam_backend()
            if dxcam_backend is not None:
                self._backend = dxcam_backend
                return self._backend
            if not self._warned_fallback:
                level = logger.warning if self._backend_name == "dxcam" else logger.info
                level("Capture backend 'dxcam' unavailable -- using mss instead.")
                self._warned_fallback = True

        self._backend = MssBackend()
        return self._backend

    def close(self) -> None:
        """Release the underlying capture backend's resources."""
        if self._backend is not None:
            self._backend.close()
            self._backend = None

    def get_screenshot_value(
        self, y1: int, x1: int, x2: int, y2: int
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """Capture a screenshot of the specified region.

        Args:
            y1: Top coordinate
            x1: Left coordinate
            x2: Right coordinate (exclusive)
            y2: Bottom coordinate (exclusive)

        Returns:
            Tuple of (numpy_array, numpy_array) or (None, None) on error.
            Both slots are the same array -- kept as a 2-tuple for
            backward API compatibility with existing call sites, all of
            which discard the second value.
        """
        monitor = {"top": y1, "left": x1, "width": x2 - x1, "height": y2 - y1}
        try:
            img_array = self._get_backend().grab(monitor)
        except Exception as e:
            logger.error("Screenshot capture failed: %s", e)
            return None, None

        if img_array is None:
            return None, None
        return img_array, img_array
