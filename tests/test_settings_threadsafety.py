"""Regression test for cross-thread settings sync (issue #114).

SettingMenu.load_settings() is called from the alert daemon thread; it must
NOT mutate Tkinter widgets directly. The widget sync (apply_settings) must be
dispatched to the main thread via self.main.after(0, ...).
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evealert.menu.setting import DEFAULT_SETTINGS, SettingMenu


class _Stub:
    default = DEFAULT_SETTINGS
    merge_settings_with_defaults = SettingMenu.merge_settings_with_defaults
    load_settings = SettingMenu.load_settings

    def __init__(self, window_created):
        self._window_created = window_created
        self.main = mock.MagicMock()
        self.apply_settings = mock.MagicMock()


class LoadSettingsThreadSafetyTests(unittest.TestCase):
    def _run_with_temp_settings(self, stub):
        payload = {"log_level": "INFO"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch(
                "evealert.menu.setting.get_settings_path", return_value=str(path)
            ):
                return stub.load_settings()

    def test_apply_settings_not_called_synchronously(self):
        stub = _Stub(window_created=True)
        result = self._run_with_temp_settings(stub)
        # Pure read+merge still returns the dict synchronously
        self.assertEqual(result["log_level"], "INFO")
        # Widget sync must NOT run inline on the calling (alert) thread
        stub.apply_settings.assert_not_called()
        # It must be scheduled onto the main thread instead
        stub.main.after.assert_called_once()
        self.assertEqual(stub.main.after.call_args.args[0], 0)

    def test_scheduled_callback_applies_settings(self):
        stub = _Stub(window_created=True)
        self._run_with_temp_settings(stub)
        # Invoke the callback that was scheduled via after(0)
        scheduled = stub.main.after.call_args.args[1]
        scheduled()
        stub.apply_settings.assert_called_once()

    def test_no_ui_dispatch_before_window_created(self):
        stub = _Stub(window_created=False)
        result = self._run_with_temp_settings(stub)
        self.assertEqual(result["log_level"], "INFO")
        stub.main.after.assert_not_called()
        stub.apply_settings.assert_not_called()


if __name__ == "__main__":
    unittest.main()
