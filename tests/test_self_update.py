"""Tests for self-update infrastructure (#self-update).

Covers update_checker additions, self_updater helpers, and the
UpdateDialog signal/slot wiring.  No real network calls or file writes
are made — everything is mocked.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock


# ---------------------------------------------------------------------------
# update_checker — new helpers
# ---------------------------------------------------------------------------

class FetchAssetUrlTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_exe_url(self):
        from evealert.tools.update_checker import fetch_latest_asset_url

        fake_payload = {
            "assets": [
                {"name": "EVE-Alert.exe", "browser_download_url": "https://example.com/EVE-Alert.exe"},
                {"name": "checksums.txt", "browser_download_url": "https://example.com/checksums.txt"},
            ]
        }
        resp = mock.AsyncMock()
        resp.raise_for_status = mock.Mock()  # synchronous
        resp.json = mock.Mock(return_value=fake_payload)  # synchronous

        client = mock.AsyncMock()
        client.get = mock.AsyncMock(return_value=resp)
        client.__aenter__ = mock.AsyncMock(return_value=client)
        client.__aexit__ = mock.AsyncMock(return_value=False)

        with mock.patch("httpx.AsyncClient", return_value=client):
            result = await fetch_latest_asset_url("v6.3.8")

        self.assertEqual(result, "https://example.com/EVE-Alert.exe")

    async def test_returns_none_when_no_exe_asset(self):
        from evealert.tools.update_checker import fetch_latest_asset_url

        fake_payload = {"assets": [{"name": "README.txt", "browser_download_url": "https://x.com/r"}]}
        resp = mock.AsyncMock()
        resp.raise_for_status = mock.Mock()  # synchronous
        resp.json = mock.Mock(return_value=fake_payload)  # synchronous

        client = mock.AsyncMock()
        client.get = mock.AsyncMock(return_value=resp)
        client.__aenter__ = mock.AsyncMock(return_value=client)
        client.__aexit__ = mock.AsyncMock(return_value=False)

        with mock.patch("httpx.AsyncClient", return_value=client):
            result = await fetch_latest_asset_url("v6.3.8")

        self.assertIsNone(result)

    async def test_returns_none_on_network_error(self):
        from evealert.tools.update_checker import fetch_latest_asset_url

        client = mock.AsyncMock()
        client.get = mock.AsyncMock(side_effect=OSError("no internet"))
        client.__aenter__ = mock.AsyncMock(return_value=client)
        client.__aexit__ = mock.AsyncMock(return_value=False)

        with mock.patch("httpx.AsyncClient", return_value=client):
            result = await fetch_latest_asset_url("v6.3.8")

        self.assertIsNone(result)


class DownloadReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_bytes_to_file(self):
        from evealert.tools.update_checker import download_release

        chunks = [b"hello ", b"world"]
        resp = mock.AsyncMock()
        resp.raise_for_status = mock.Mock()
        resp.headers = {"content-length": "11"}
        resp.aiter_bytes = mock.MagicMock(return_value=_async_iter(chunks))
        resp.__aenter__ = mock.AsyncMock(return_value=resp)
        resp.__aexit__ = mock.AsyncMock(return_value=False)

        stream_ctx = mock.MagicMock()
        stream_ctx.__aenter__ = mock.AsyncMock(return_value=resp)
        stream_ctx.__aexit__ = mock.AsyncMock(return_value=False)

        client = mock.AsyncMock()
        client.stream = mock.MagicMock(return_value=stream_ctx)
        client.__aenter__ = mock.AsyncMock(return_value=client)
        client.__aexit__ = mock.AsyncMock(return_value=False)

        dest = Path(self._tmp())
        seen: list[tuple[int, int]] = []

        with mock.patch("httpx.AsyncClient", return_value=client):
            await download_release("https://x.com/f.exe", dest, lambda d, t: seen.append((d, t)))

        self.assertEqual(dest.read_bytes(), b"hello world")
        self.assertEqual(seen, [(6, 11), (11, 11)])

    def _tmp(self):
        import tempfile, os  # noqa: E401
        fd, path = tempfile.mkstemp(suffix=".exe")
        os.close(fd)
        return path


async def _async_iter(items):
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# self_updater helpers
# ---------------------------------------------------------------------------

class SelfUpdaterTests(unittest.TestCase):
    def test_is_updatable_false_on_non_windows(self):
        from evealert.tools import self_updater
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertFalse(self_updater.is_updatable())

    def test_is_updatable_false_when_not_frozen(self):
        from evealert.tools import self_updater
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.object(sys, "frozen", False, create=True):
                self.assertFalse(self_updater.is_updatable())

    def test_is_updatable_true_when_frozen_windows(self):
        from evealert.tools import self_updater
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.object(sys, "frozen", True, create=True):
                self.assertTrue(self_updater.is_updatable())

    def test_write_swap_script_contains_pid_and_paths(self):
        from evealert.tools.self_updater import write_swap_script
        current = Path("C:/EVE-Alert.exe")
        new = Path("C:/Temp/EVE-Alert-update.exe")
        script_path = write_swap_script(current, new, 12345, relaunch=True)
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("12345", content)
        # Paths are stored with forward slashes in the script regardless of OS
        self.assertIn(str(current).replace("\\", "/"), content)
        self.assertIn(str(new).replace("\\", "/"), content)
        self.assertIn("Start-Process", content)

    def test_write_swap_script_no_relaunch(self):
        from evealert.tools.self_updater import write_swap_script
        script_path = write_swap_script(
            Path("C:/old.exe"), Path("C:/new.exe"), 99, relaunch=False
        )
        content = script_path.read_text(encoding="utf-8")
        self.assertNotIn("Start-Process", content)

    def test_cleanup_temp_download_no_error_if_missing(self):
        from evealert.tools.self_updater import cleanup_temp_download, temp_download_path
        p = temp_download_path()
        if p.exists():
            p.unlink()
        # Should not raise even when file doesn't exist
        cleanup_temp_download()

    def test_get_current_exe_prefers_argv0_over_sys_executable(self):
        """get_current_exe() should return the argv[0] path, not sys.executable
        when argv[0] points to a real .exe (PyInstaller bundle scenario)."""
        import tempfile, os  # noqa: E401,PLC0415
        from evealert.tools import self_updater

        # Create a temp .exe so Path.exists() returns True
        fd, fake_exe = tempfile.mkstemp(suffix=".exe")
        os.close(fd)
        try:
            with mock.patch.object(sys, "platform", "win32"), \
                 mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "argv", [fake_exe]), \
                 mock.patch.object(sys, "executable", "C:/Temp/_MEI123/python.exe"):
                result = self_updater.get_current_exe()
            self.assertIsNotNone(result)
            # Should be the argv[0] path, NOT the _MEI temp python.exe
            self.assertNotIn("_MEI", str(result))
            self.assertTrue(str(result).endswith(".exe"))
        finally:
            os.unlink(fake_exe)


# ---------------------------------------------------------------------------
# QtBridge — update_available signal
# ---------------------------------------------------------------------------

class QtBridgeUpdateSignalTests(unittest.TestCase):
    def test_notify_update_emits_signal(self):
        import os  # noqa: PLC0415
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415
        app = QApplication.instance() or QApplication([])  # noqa: F841

        from evealert.ui.qt_bridge import QtBridge  # noqa: PLC0415
        bridge = QtBridge()
        received: list[str] = []
        bridge.update_available.connect(received.append)
        bridge.notify_update("v9.9.9")
        self.assertEqual(received, ["v9.9.9"])


if __name__ == "__main__":
    unittest.main()
