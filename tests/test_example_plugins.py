"""Acceptance test for #181: the three shipped example plugins load
cleanly from a fresh plugins directory and dispatch without raising."""

import time
import unittest
from pathlib import Path

_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples" / "plugins"


class ExamplePluginsLoadTests(unittest.TestCase):
    def setUp(self):
        from evealert.tools.plugin_loader import PluginManager

        self.pm = PluginManager()

    def test_all_three_examples_load(self):
        count = self.pm.load_plugins(_EXAMPLES_DIR)
        self.assertEqual(count, 3)
        names = set(self.pm.loaded_names)
        self.assertEqual(
            names, {"discord_rich_alarm", "sound_per_tier", "csv_event_log"}
        )

    def test_all_hooks_detected_as_v2(self):
        self.pm.load_plugins(_EXAMPLES_DIR)
        for record in self.pm.list_plugins():
            for hook in record.hook_names:
                self.assertFalse(
                    record._is_v1[hook],
                    f"{record.name}.{hook} should use the v2 ctx-first signature",
                )

    def test_discord_plugin_unconfigured_webhook_is_a_noop(self):
        from evealert.plugin_api import AlarmEvent

        self.pm.load_plugins(_EXAMPLES_DIR)
        event = AlarmEvent(alarm_type="Enemy", system="Jita", timestamp="12:00:00")
        # Placeholder WEBHOOK_URL -- must not raise or attempt a real POST.
        self.pm.call("on_enemy", ctx_settings={}, log_fn=lambda t: None, event=event)
        time.sleep(0.3)
        record = self.pm.get_plugin("discord_rich_alarm")
        self.assertFalse(record.quarantined)

    def test_csv_event_log_writes_a_row(self):
        import csv
        import tempfile
        from unittest.mock import patch

        from evealert.plugin_api import AlarmEvent

        self.pm.load_plugins(_EXAMPLES_DIR)
        record = self.pm.get_plugin("csv_event_log")
        module_globals = record._hooks["on_enemy"].__globals__

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "events.csv"
            with patch.dict(module_globals, {"LOG_PATH": str(csv_path)}):
                event = AlarmEvent(alarm_type="Enemy", system="Jita", timestamp="12:00:00")
                self.pm.call(
                    "on_enemy", ctx_settings={}, log_fn=lambda t: None, event=event
                )
                time.sleep(0.3)

            self.assertTrue(csv_path.exists())
            with open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["event"], "enemy_alarm")
            self.assertEqual(rows[0]["system"], "Jita")

        self.assertFalse(record.quarantined)


if __name__ == "__main__":
    unittest.main()
