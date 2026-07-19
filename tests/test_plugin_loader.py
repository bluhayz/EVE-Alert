"""Tests for evealert.tools.plugin_loader (v1 + #181 v2, v8.0)."""

import shutil
import tempfile
import time
import unittest
from pathlib import Path


def _write_plugin(plugin_dir: Path, name: str, source: str) -> Path:
    path = plugin_dir / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    return path


class PluginLoaderTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        from evealert.tools.plugin_loader import PluginManager

        self.pm = PluginManager()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)


V1_PLUGIN = '''
__version__ = "1.0"
calls = []

def on_start():
    calls.append("start")

def on_stop():
    calls.append("stop")

def on_enemy(system, timestamp):
    calls.append(("enemy", system, timestamp))

def on_faction(system, timestamp):
    calls.append(("faction", system, timestamp))

def on_intel(line):
    calls.append(("intel", line))
'''

V2_PLUGIN = '''
__version__ = "2.0"
calls = []

def on_start(ctx):
    calls.append(("start", ctx.version))

def on_stop(ctx):
    calls.append("stop")

def on_enemy(ctx, event):
    calls.append(("enemy", event.system, event.timestamp))

def on_intel(ctx, report):
    calls.append(("intel", report.line))

def on_killmail(ctx, km):
    calls.append(("killmail", km.killmail_id))

def on_threat_score(ctx, assessment):
    calls.append(("threat_score", assessment.score))
'''

RAISING_PLUGIN = '''
def on_enemy(ctx, event):
    raise RuntimeError("boom")
'''

NO_HOOKS_PLUGIN = '''
x = 1
'''


class LoadPluginsTests(PluginLoaderTestCase):
    def test_loads_v1_plugin(self):
        _write_plugin(self.temp_dir, "v1plugin", V1_PLUGIN)
        count = self.pm.load_plugins(self.temp_dir)
        self.assertEqual(count, 1)
        record = self.pm.get_plugin("v1plugin")
        self.assertIsNotNone(record)
        self.assertEqual(record.version, "1.0")
        self.assertIn("on_enemy", record.hook_names)

    def test_loads_v2_plugin(self):
        _write_plugin(self.temp_dir, "v2plugin", V2_PLUGIN)
        count = self.pm.load_plugins(self.temp_dir)
        self.assertEqual(count, 1)
        record = self.pm.get_plugin("v2plugin")
        self.assertIn("on_killmail", record.hook_names)
        self.assertIn("on_threat_score", record.hook_names)

    def test_module_with_no_hooks_not_counted(self):
        _write_plugin(self.temp_dir, "nohooks", NO_HOOKS_PLUGIN)
        count = self.pm.load_plugins(self.temp_dir)
        self.assertEqual(count, 0)
        self.assertIsNone(self.pm.get_plugin("nohooks"))

    def test_syntax_error_plugin_skipped_not_raised(self):
        _write_plugin(self.temp_dir, "broken", "def on_start(:\n    pass")
        count = self.pm.load_plugins(self.temp_dir)  # must not raise
        self.assertEqual(count, 0)

    def test_underscore_prefixed_files_skipped(self):
        _write_plugin(self.temp_dir, "_helper", V1_PLUGIN)
        count = self.pm.load_plugins(self.temp_dir)
        self.assertEqual(count, 0)

    def test_missing_directory_returns_zero(self):
        count = self.pm.load_plugins(self.temp_dir / "does_not_exist")
        self.assertEqual(count, 0)


class HookDispatchV1CompatTests(PluginLoaderTestCase):
    """Acceptance criterion: v1 plugins keep loading and running unchanged."""

    def test_v1_on_start_called_with_no_args(self):
        _write_plugin(self.temp_dir, "p", V1_PLUGIN)
        self.pm.load_plugins(self.temp_dir)
        record = self.pm.get_plugin("p")
        fn = record._hooks["on_start"]

        self.pm.call("on_start")
        time.sleep(0.2)

        self.assertIn("start", fn.__globals__["calls"])
        self.assertFalse(record.quarantined)

    def test_v1_on_enemy_receives_system_and_timestamp_kwargs(self):
        _write_plugin(self.temp_dir, "p", V1_PLUGIN)
        self.pm.load_plugins(self.temp_dir)
        record = self.pm.get_plugin("p")
        fn = record._hooks["on_enemy"]

        self.pm.call("on_enemy", system="Jita", timestamp="12:00:00")
        time.sleep(0.2)

        calls = fn.__globals__["calls"]
        self.assertIn(("enemy", "Jita", "12:00:00"), calls)

    def test_v1_on_intel_receives_line_kwarg(self):
        _write_plugin(self.temp_dir, "p", V1_PLUGIN)
        self.pm.load_plugins(self.temp_dir)
        record = self.pm.get_plugin("p")
        fn = record._hooks["on_intel"]

        self.pm.call("on_intel", line="hostile spotted")
        time.sleep(0.2)

        self.assertIn(("intel", "hostile spotted"), fn.__globals__["calls"])


class HookDispatchV2Tests(PluginLoaderTestCase):
    def test_v2_on_enemy_receives_ctx_and_event(self):
        from evealert.plugin_api import AlarmEvent

        _write_plugin(self.temp_dir, "p", V2_PLUGIN)
        self.pm.load_plugins(self.temp_dir)
        record = self.pm.get_plugin("p")
        fn = record._hooks["on_enemy"]

        event = AlarmEvent(alarm_type="Enemy", system="Jita", timestamp="12:00:00")
        self.pm.call(
            "on_enemy", ctx_settings={"notifications": {}}, log_fn=lambda t: None, event=event
        )
        time.sleep(0.2)

        calls = fn.__globals__["calls"]
        self.assertIn(("enemy", "Jita", "12:00:00"), calls)

    def test_v2_on_start_receives_ctx_with_version(self):
        _write_plugin(self.temp_dir, "p", V2_PLUGIN)
        self.pm.load_plugins(self.temp_dir)
        record = self.pm.get_plugin("p")
        fn = record._hooks["on_start"]

        self.pm.call("on_start", ctx_settings={}, log_fn=lambda t: None)
        time.sleep(0.2)

        calls = fn.__globals__["calls"]
        self.assertTrue(any(c[0] == "start" for c in calls))

    def test_v2_on_killmail_and_on_threat_score_dispatch(self):
        from evealert.plugin_api import KillmailEvent, ThreatScoreEvent

        _write_plugin(self.temp_dir, "p", V2_PLUGIN)
        self.pm.load_plugins(self.temp_dir)
        record = self.pm.get_plugin("p")

        self.pm.call(
            "on_killmail", ctx_settings={}, log_fn=lambda t: None,
            event=KillmailEvent(killmail_id=123, system_id=1, system_name="Jita",
                                 victim_ship_type_id=None),
        )
        self.pm.call(
            "on_threat_score", ctx_settings={}, log_fn=lambda t: None,
            event=ThreatScoreEvent(score=7, label="CRITICAL"),
        )
        time.sleep(0.2)

        calls = record._hooks["on_killmail"].__globals__["calls"]
        self.assertIn(("killmail", 123), calls)
        self.assertIn(("threat_score", 7), calls)


class QuarantineTests(PluginLoaderTestCase):
    def test_disabled_plugin_not_called(self):
        _write_plugin(self.temp_dir, "p", V1_PLUGIN)
        self.pm.load_plugins(self.temp_dir)
        record = self.pm.get_plugin("p")
        fn = record._hooks["on_start"]

        self.pm.set_enabled("p", False)
        self.pm.call("on_start")
        time.sleep(0.2)
        self.assertEqual(fn.__globals__["calls"], [])

    def test_quarantines_after_three_consecutive_failures(self):
        from evealert.plugin_api import AlarmEvent

        _write_plugin(self.temp_dir, "p", RAISING_PLUGIN)
        self.pm.load_plugins(self.temp_dir)
        record = self.pm.get_plugin("p")
        self.assertFalse(record.quarantined)

        event = AlarmEvent(alarm_type="Enemy", system="Jita", timestamp="00:00:00")
        for _ in range(3):
            self.pm.call("on_enemy", ctx_settings={}, log_fn=lambda t: None, event=event)
            time.sleep(0.15)

        self.assertTrue(record.quarantined)
        self.assertEqual(record.status, "quarantined")

    def test_quarantined_plugin_stops_receiving_calls(self):
        from evealert.plugin_api import AlarmEvent

        _write_plugin(self.temp_dir, "p", RAISING_PLUGIN)
        self.pm.load_plugins(self.temp_dir)
        record = self.pm.get_plugin("p")
        event = AlarmEvent(alarm_type="Enemy", system="Jita", timestamp="00:00:00")

        for _ in range(3):
            self.pm.call("on_enemy", ctx_settings={}, log_fn=lambda t: None, event=event)
            time.sleep(0.15)
        self.assertTrue(record.quarantined)

        failures_before = record._consecutive_failures["on_enemy"]
        self.pm.call("on_enemy", ctx_settings={}, log_fn=lambda t: None, event=event)
        time.sleep(0.15)
        self.assertEqual(record._consecutive_failures["on_enemy"], failures_before)

    def test_reset_quarantine_re_enables(self):
        from evealert.plugin_api import AlarmEvent

        _write_plugin(self.temp_dir, "p", RAISING_PLUGIN)
        self.pm.load_plugins(self.temp_dir)
        record = self.pm.get_plugin("p")
        event = AlarmEvent(alarm_type="Enemy", system="Jita", timestamp="00:00:00")

        for _ in range(3):
            self.pm.call("on_enemy", ctx_settings={}, log_fn=lambda t: None, event=event)
            time.sleep(0.15)
        self.assertTrue(record.quarantined)

        self.assertTrue(self.pm.reset_quarantine("p"))
        self.assertFalse(record.quarantined)
        self.assertEqual(record._consecutive_failures["on_enemy"], 0)

    def test_reset_quarantine_unknown_plugin_returns_false(self):
        self.assertFalse(self.pm.reset_quarantine("nonexistent"))

    def test_set_enabled_unknown_plugin_returns_false(self):
        self.assertFalse(self.pm.set_enabled("nonexistent", False))

    def test_success_resets_failure_counter(self):
        source = '''
calls = []
_fail = [True, True, False, False]

def on_enemy(ctx, event):
    if _fail.pop(0):
        raise RuntimeError("boom")
    calls.append("ok")
'''
        from evealert.plugin_api import AlarmEvent

        _write_plugin(self.temp_dir, "p", source)
        self.pm.load_plugins(self.temp_dir)
        record = self.pm.get_plugin("p")
        event = AlarmEvent(alarm_type="Enemy", system="Jita", timestamp="00:00:00")

        for _ in range(4):
            self.pm.call("on_enemy", ctx_settings={}, log_fn=lambda t: None, event=event)
            time.sleep(0.15)

        # 2 failures, then 2 successes reset the streak -- never hit 3 in a row.
        self.assertFalse(record.quarantined)


class ListPluginsTests(PluginLoaderTestCase):
    def test_list_plugins_sorted_by_name(self):
        _write_plugin(self.temp_dir, "zeta", V1_PLUGIN)
        _write_plugin(self.temp_dir, "alpha", V1_PLUGIN)
        self.pm.load_plugins(self.temp_dir)
        names = [r.name for r in self.pm.list_plugins()]
        self.assertEqual(names, ["alpha", "zeta"])

    def test_hook_count(self):
        _write_plugin(self.temp_dir, "a", V1_PLUGIN)
        _write_plugin(self.temp_dir, "b", V2_PLUGIN)
        self.pm.load_plugins(self.temp_dir)
        self.assertEqual(self.pm.hook_count("on_enemy"), 2)
        self.assertEqual(self.pm.hook_count("on_killmail"), 1)


if __name__ == "__main__":
    unittest.main()
