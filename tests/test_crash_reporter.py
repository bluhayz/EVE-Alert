"""Tests for evealert.tools.crash_reporter (#180, v8.0)."""

import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


class CrashReporterTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.fake_log_dir = self.temp_dir / "logs"
        self.fake_log_dir.mkdir()
        self._log_path_patch = patch(
            "evealert.settings.logger.LOG_PATH", self.fake_log_dir
        )
        self._log_path_patch.start()

    def tearDown(self):
        self._log_path_patch.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _raise_and_capture(self):
        """Return (exc_type, exc_value, exc_tb) from a real raised exception."""
        try:
            raise ValueError("boom")
        except ValueError:
            return sys.exc_info()


class WriteCrashBundleTests(CrashReporterTestCase):
    def test_writes_traceback_and_context(self):
        from evealert.tools.crash_reporter import write_crash_bundle

        exc_info = self._raise_and_capture()
        bundle_dir = write_crash_bundle(*exc_info, context="test-context")

        self.assertIsNotNone(bundle_dir)
        self.assertTrue(bundle_dir.is_dir())
        tb_text = (bundle_dir / "traceback.txt").read_text(encoding="utf-8")
        self.assertIn("ValueError: boom", tb_text)
        ctx = json.loads((bundle_dir / "context.json").read_text(encoding="utf-8"))
        self.assertEqual(ctx["crash"]["context"], "test-context")
        self.assertEqual(ctx["crash"]["exception_type"], "ValueError")

    def test_redacts_settings(self):
        from evealert.tools.crash_reporter import write_crash_bundle

        exc_info = self._raise_and_capture()
        settings = {"push": {"telegram_token": "secret123"}, "server": {"webhook": "https://x"}}
        bundle_dir = write_crash_bundle(*exc_info, settings=settings)

        redacted = json.loads(
            (bundle_dir / "redacted_settings.json").read_text(encoding="utf-8")
        )
        self.assertEqual(redacted["push"]["telegram_token"], "***REDACTED***")
        self.assertEqual(redacted["server"]["webhook"], "***REDACTED***")

    def test_no_settings_skips_redacted_file(self):
        from evealert.tools.crash_reporter import write_crash_bundle

        exc_info = self._raise_and_capture()
        bundle_dir = write_crash_bundle(*exc_info)
        self.assertFalse((bundle_dir / "redacted_settings.json").exists())

    def test_includes_recent_log_tail(self):
        from evealert.tools.crash_reporter import write_crash_bundle

        (self.fake_log_dir / "alert.log").write_text(
            "\n".join(f"line {i}" for i in range(300)), encoding="utf-8"
        )
        exc_info = self._raise_and_capture()
        bundle_dir = write_crash_bundle(*exc_info)

        recent = (bundle_dir / "recent_log.txt").read_text(encoding="utf-8")
        lines = recent.splitlines()
        self.assertEqual(len(lines), 200)
        self.assertEqual(lines[-1], "line 299")  # tail, not head

    def test_write_failure_never_raises(self):
        from evealert.tools.crash_reporter import write_crash_bundle

        exc_info = self._raise_and_capture()
        with patch(
            "evealert.tools.crash_reporter.get_crash_dir",
            side_effect=OSError("disk full"),
        ):
            result = write_crash_bundle(*exc_info)  # must not raise
        self.assertIsNone(result)


class AcknowledgeTests(CrashReporterTestCase):
    def test_find_unacknowledged_returns_none_when_empty(self):
        from evealert.tools.crash_reporter import find_unacknowledged_crash

        self.assertIsNone(find_unacknowledged_crash())

    def test_find_and_acknowledge_round_trip(self):
        from evealert.tools.crash_reporter import (
            find_unacknowledged_crash,
            mark_acknowledged,
            write_crash_bundle,
        )

        exc_info = self._raise_and_capture()
        bundle_dir = write_crash_bundle(*exc_info)

        found = find_unacknowledged_crash()
        self.assertEqual(found, bundle_dir)

        mark_acknowledged(bundle_dir)
        self.assertIsNone(find_unacknowledged_crash())

    def test_returns_most_recent_unacknowledged(self):
        import time

        from evealert.tools.crash_reporter import (
            find_unacknowledged_crash,
            write_crash_bundle,
        )

        exc_info = self._raise_and_capture()
        write_crash_bundle(*exc_info)
        time.sleep(1.01)  # crash dirs are timestamped to the second
        newest = write_crash_bundle(*exc_info)

        self.assertEqual(find_unacknowledged_crash(), newest)


class ExceptionHookDispatchTests(CrashReporterTestCase):
    def setUp(self):
        super().setUp()
        import evealert.tools.crash_reporter as cr

        self._orig_installed = cr._installed
        self._orig_callback = cr._on_crash_callback
        cr._installed = False
        cr._on_crash_callback = None

    def tearDown(self):
        import evealert.tools.crash_reporter as cr

        cr._installed = self._orig_installed
        cr._on_crash_callback = self._orig_callback
        super().tearDown()

    def test_install_sets_hooks_when_enabled(self):
        from evealert.tools.crash_reporter import install

        orig_excepthook = sys.excepthook
        orig_threading_hook = threading.excepthook
        try:
            install(enabled=True)
            self.assertIsNot(sys.excepthook, orig_excepthook)
            self.assertIsNot(threading.excepthook, orig_threading_hook)
        finally:
            sys.excepthook = orig_excepthook
            threading.excepthook = orig_threading_hook

    def test_install_disabled_leaves_hooks_untouched(self):
        from evealert.tools.crash_reporter import install

        orig_excepthook = sys.excepthook
        try:
            install(enabled=False)
            self.assertIs(sys.excepthook, orig_excepthook)
        finally:
            sys.excepthook = orig_excepthook

    def test_sys_excepthook_writes_bundle_and_notifies(self):
        from evealert.tools.crash_reporter import _sys_excepthook

        notified = []
        with patch(
            "evealert.tools.crash_reporter._on_crash_callback",
            side_effect=lambda p: notified.append(p),
        ):
            exc_info = self._raise_and_capture()
            _sys_excepthook(*exc_info)

        self.assertEqual(len(notified), 1)
        self.assertTrue(notified[0].is_dir())

    def test_keyboard_interrupt_is_not_bundled(self):
        from evealert.tools.crash_reporter import _sys_excepthook

        try:
            raise KeyboardInterrupt()
        except KeyboardInterrupt:
            exc_info = sys.exc_info()

        with patch("evealert.tools.crash_reporter.write_crash_bundle") as mock_write:
            with patch("sys.__excepthook__"):
                _sys_excepthook(*exc_info)
        mock_write.assert_not_called()

    def test_threading_excepthook_dispatches(self):
        from evealert.tools.crash_reporter import _threading_excepthook

        exc_info = self._raise_and_capture()
        args = threading.ExceptHookArgs(
            (exc_info[0], exc_info[1], exc_info[2], threading.current_thread())
        )
        with patch("evealert.tools.crash_reporter._handle_exception") as mock_handle:
            _threading_excepthook(args)
        mock_handle.assert_called_once()
        self.assertIn("thread:", mock_handle.call_args.kwargs["context"])


if __name__ == "__main__":
    unittest.main()
