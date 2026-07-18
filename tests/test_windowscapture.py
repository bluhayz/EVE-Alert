"""Tests for evealert.tools.windowscapture (#176, v7.2) -- the
CaptureBackend abstraction (MssBackend / DxcamBackend) and WindowCapture's
backend selection, hot-swap, and clean-fallback behavior.

dxcam is an optional, Windows-only dependency not installed in this test
environment -- DxcamBackend tests inject a fake module into sys.modules
so the deferred `import dxcam` inside _get_camera() resolves to a mock
without needing the real package.
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from evealert.tools.windowscapture import (
    DxcamBackend,
    MssBackend,
    WindowCapture,
    _try_create_dxcam_backend,
)

_MONITOR = {"top": 10, "left": 20, "width": 100, "height": 50}


def _fake_dxcam_module(camera: MagicMock) -> types.ModuleType:
    module = types.ModuleType("dxcam")
    module.create = MagicMock(return_value=camera)
    return module


class MssBackendTests(unittest.TestCase):
    def test_grab_delegates_to_mss_and_drops_alpha(self):
        backend = MssBackend()
        fake_screenshot = np.zeros((50, 100, 4), dtype=np.uint8)  # BGRA
        mock_sct = MagicMock()
        mock_sct.grab.return_value = fake_screenshot
        with patch.object(backend, "_get_sct", return_value=mock_sct):
            result = backend.grab(_MONITOR)
        mock_sct.grab.assert_called_once_with(_MONITOR)
        self.assertEqual(result.shape, (50, 100, 3))  # alpha dropped

    def test_sct_created_lazily_and_reused(self):
        backend = MssBackend()
        self.assertIsNone(backend._sct)
        with patch("evealert.tools.windowscapture.mss.mss") as mock_mss_ctor:
            mock_mss_ctor.return_value = MagicMock()
            sct1 = backend._get_sct()
            sct2 = backend._get_sct()
        mock_mss_ctor.assert_called_once()
        self.assertIs(sct1, sct2)

    def test_close_releases_and_resets(self):
        backend = MssBackend()
        mock_sct = MagicMock()
        backend._sct = mock_sct
        backend.close()
        mock_sct.close.assert_called_once()
        self.assertIsNone(backend._sct)

    def test_close_without_init_does_not_raise(self):
        MssBackend().close()  # must not raise


class DxcamBackendTests(unittest.TestCase):
    def test_grab_crops_full_frame_to_requested_region(self):
        backend = DxcamBackend()
        full_frame = np.arange(200 * 300 * 3, dtype=np.uint8).reshape(200, 300, 3)
        mock_camera = MagicMock()
        mock_camera.grab.return_value = full_frame
        fake_module = _fake_dxcam_module(mock_camera)

        with patch.dict(sys.modules, {"dxcam": fake_module}):
            result = backend.grab(_MONITOR)

        top, left = _MONITOR["top"], _MONITOR["left"]
        bottom, right = top + _MONITOR["height"], left + _MONITOR["width"]
        expected = full_frame[top:bottom, left:right]
        np.testing.assert_array_equal(result, expected)

    def test_camera_created_with_bgr_output_to_match_mss(self):
        backend = DxcamBackend()
        mock_camera = MagicMock()
        mock_camera.grab.return_value = np.zeros((200, 300, 3), dtype=np.uint8)
        fake_module = _fake_dxcam_module(mock_camera)

        with patch.dict(sys.modules, {"dxcam": fake_module}):
            backend.grab(_MONITOR)

        fake_module.create.assert_called_once_with(output_idx=0, output_color="BGR")

    def test_none_frame_reuses_last_successful_frame(self):
        """dxcam returns None when nothing changed since the last grab --
        not an error. The backend must reuse the last good frame rather
        than propagate None for a merely-static screen."""
        backend = DxcamBackend()
        full_frame = np.full((200, 300, 3), 77, dtype=np.uint8)
        mock_camera = MagicMock()
        mock_camera.grab.side_effect = [full_frame, None]
        fake_module = _fake_dxcam_module(mock_camera)

        with patch.dict(sys.modules, {"dxcam": fake_module}):
            first = backend.grab(_MONITOR)
            second = backend.grab(_MONITOR)

        np.testing.assert_array_equal(first, second)

    def test_none_frame_before_any_successful_grab_returns_none(self):
        backend = DxcamBackend()
        mock_camera = MagicMock()
        mock_camera.grab.return_value = None
        fake_module = _fake_dxcam_module(mock_camera)

        with patch.dict(sys.modules, {"dxcam": fake_module}):
            result = backend.grab(_MONITOR)

        self.assertIsNone(result)

    def test_camera_created_lazily_and_reused(self):
        backend = DxcamBackend()
        mock_camera = MagicMock()
        mock_camera.grab.return_value = np.zeros((200, 300, 3), dtype=np.uint8)
        fake_module = _fake_dxcam_module(mock_camera)

        with patch.dict(sys.modules, {"dxcam": fake_module}):
            backend.grab(_MONITOR)
            backend.grab(_MONITOR)

        fake_module.create.assert_called_once()

    def test_close_stops_camera_and_clears_state(self):
        backend = DxcamBackend()
        mock_camera = MagicMock()
        backend._camera = mock_camera
        backend._last_frame = np.zeros((1, 1, 3), dtype=np.uint8)

        backend.close()

        mock_camera.stop.assert_called_once()
        self.assertIsNone(backend._camera)
        self.assertIsNone(backend._last_frame)

    def test_close_survives_camera_stop_raising(self):
        backend = DxcamBackend()
        mock_camera = MagicMock()
        mock_camera.stop.side_effect = RuntimeError("boom")
        backend._camera = mock_camera
        backend.close()  # must not raise
        self.assertIsNone(backend._camera)

    def test_close_without_init_does_not_raise(self):
        DxcamBackend().close()


class TryCreateDxcamBackendTests(unittest.TestCase):
    def test_returns_none_when_dxcam_not_installed(self):
        with patch.dict(sys.modules, {"dxcam": None}):
            result = _try_create_dxcam_backend()
        self.assertIsNone(result)

    def test_returns_backend_when_dxcam_available(self):
        mock_camera = MagicMock()
        fake_module = _fake_dxcam_module(mock_camera)
        with patch.dict(sys.modules, {"dxcam": fake_module}):
            result = _try_create_dxcam_backend()
        self.assertIsInstance(result, DxcamBackend)

    def test_returns_none_when_dxcam_create_raises(self):
        fake_module = types.ModuleType("dxcam")
        fake_module.create = MagicMock(side_effect=RuntimeError("no compatible GPU"))
        with patch.dict(sys.modules, {"dxcam": fake_module}):
            result = _try_create_dxcam_backend()
        self.assertIsNone(result)


class BackendParityTests(unittest.TestCase):
    """#176 acceptance criterion: identical detection results on both
    backends -- i.e. for the same underlying frame data, MssBackend and
    DxcamBackend must hand Vision.find() the same shape/dtype/values."""

    def test_mss_and_dxcam_produce_identical_arrays_for_same_source_frame(self):
        source_bgra = np.arange(50 * 100 * 4, dtype=np.uint8).reshape(50, 100, 4)
        source_bgr = source_bgra[:, :, :3]

        mss_backend = MssBackend()
        mock_sct = MagicMock()
        mock_sct.grab.return_value = source_bgra
        with patch.object(mss_backend, "_get_sct", return_value=mock_sct):
            mss_result = mss_backend.grab(_MONITOR)

        dxcam_backend = DxcamBackend()
        # dxcam is created with output_color="BGR" (no alpha channel) --
        # simulate its camera returning the equivalent BGR frame, full-size,
        # for grab() to crop down to the requested region.
        full_bgr = np.zeros((200, 300, 3), dtype=np.uint8)
        top, left = _MONITOR["top"], _MONITOR["left"]
        full_bgr[top:top + 50, left:left + 100] = source_bgr
        mock_camera = MagicMock()
        mock_camera.grab.return_value = full_bgr
        fake_module = _fake_dxcam_module(mock_camera)
        with patch.dict(sys.modules, {"dxcam": fake_module}):
            dxcam_result = dxcam_backend.grab(_MONITOR)

        self.assertEqual(mss_result.shape, dxcam_result.shape)
        self.assertEqual(mss_result.dtype, dxcam_result.dtype)
        np.testing.assert_array_equal(mss_result, dxcam_result)


class WindowCaptureTests(unittest.TestCase):
    def test_defaults_to_mss_backend(self):
        wc = WindowCapture()
        backend = wc._get_backend()
        self.assertIsInstance(backend, MssBackend)

    def test_get_screenshot_value_delegates_and_computes_monitor_dict(self):
        wc = WindowCapture()
        mock_backend = MagicMock()
        mock_backend.grab.return_value = np.zeros((50, 100, 3), dtype=np.uint8)
        wc._backend = mock_backend

        result, result2 = wc.get_screenshot_value(y1=10, x1=20, x2=120, y2=60)

        mock_backend.grab.assert_called_once_with(
            {"top": 10, "left": 20, "width": 100, "height": 50}
        )
        self.assertIs(result, result2)
        self.assertEqual(result.shape, (50, 100, 3))

    def test_get_screenshot_value_returns_none_none_on_exception(self):
        wc = WindowCapture()
        mock_backend = MagicMock()
        mock_backend.grab.side_effect = RuntimeError("capture failed")
        wc._backend = mock_backend

        result = wc.get_screenshot_value(0, 0, 10, 10)

        self.assertEqual(result, (None, None))

    def test_get_screenshot_value_returns_none_none_when_backend_returns_none(self):
        wc = WindowCapture()
        mock_backend = MagicMock()
        mock_backend.grab.return_value = None
        wc._backend = mock_backend

        result = wc.get_screenshot_value(0, 0, 10, 10)

        self.assertEqual(result, (None, None))

    def test_close_releases_backend(self):
        wc = WindowCapture()
        mock_backend = MagicMock()
        wc._backend = mock_backend

        wc.close()

        mock_backend.close.assert_called_once()
        self.assertIsNone(wc._backend)

    def test_close_without_backend_created_does_not_raise(self):
        WindowCapture().close()

    # -- backend selection: auto/dxcam fallback -----------------------------

    def test_auto_uses_dxcam_when_available(self):
        wc = WindowCapture(backend="auto")
        mock_camera = MagicMock()
        fake_module = _fake_dxcam_module(mock_camera)
        with patch.dict(sys.modules, {"dxcam": fake_module}):
            backend = wc._get_backend()
        self.assertIsInstance(backend, DxcamBackend)

    def test_auto_falls_back_to_mss_when_dxcam_unavailable(self):
        wc = WindowCapture(backend="auto")
        with patch.dict(sys.modules, {"dxcam": None}):
            backend = wc._get_backend()
        self.assertIsInstance(backend, MssBackend)

    def test_forced_dxcam_falls_back_to_mss_when_unavailable_no_crash(self):
        """#176 acceptance criterion: clean fallback when dxcam is not
        installed -- no crash, one log line."""
        wc = WindowCapture(backend="dxcam")
        with patch.dict(sys.modules, {"dxcam": None}):
            backend = wc._get_backend()  # must not raise
        self.assertIsInstance(backend, MssBackend)

    def test_fallback_warning_logged_only_once(self):
        wc = WindowCapture(backend="dxcam")
        with patch.dict(sys.modules, {"dxcam": None}), patch(
            "evealert.tools.windowscapture.logger"
        ) as mock_logger:
            wc._get_backend()
            wc._backend = None  # force re-resolution without resetting the warned flag
            wc._get_backend()
        self.assertEqual(mock_logger.warning.call_count, 1)

    # -- set_backend() hot-swap ----------------------------------------

    def test_set_backend_closes_old_backend(self):
        wc = WindowCapture()
        old_backend = MagicMock()
        wc._backend = old_backend
        wc._backend_name = "mss"

        wc.set_backend("dxcam")

        old_backend.close.assert_called_once()
        self.assertIsNone(wc._backend)

    def test_set_backend_new_backend_created_on_next_use(self):
        wc = WindowCapture()
        wc._get_backend()  # materialize the default mss backend
        wc.set_backend("dxcam")

        with patch.dict(sys.modules, {"dxcam": None}):
            backend = wc._get_backend()
        self.assertIsInstance(backend, MssBackend)  # dxcam unavailable -> fallback

    def test_set_backend_same_name_is_a_no_op_when_already_created(self):
        wc = WindowCapture()
        backend = wc._get_backend()
        wc.set_backend("mss")
        self.assertIs(wc._backend, backend)  # not recreated


if __name__ == "__main__":
    unittest.main()
