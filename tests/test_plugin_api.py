"""Tests for evealert.plugin_api (#181, v8.0)."""

import unittest
from unittest.mock import patch


class PluginContextLogTests(unittest.TestCase):
    def test_log_calls_log_fn(self):
        from evealert.plugin_api import PluginContext

        received = []
        ctx = PluginContext(settings={}, log_fn=received.append)
        ctx.log("hello")
        self.assertEqual(received, ["hello"])

    def test_version_matches_api_version(self):
        from evealert.plugin_api import API_VERSION, PluginContext

        ctx = PluginContext(settings={}, log_fn=lambda t: None)
        self.assertEqual(ctx.version, API_VERSION)

    def test_settings_is_the_snapshot_passed_in(self):
        from evealert.plugin_api import PluginContext

        settings = {"server": {"system": "Jita"}}
        ctx = PluginContext(settings=settings, log_fn=lambda t: None)
        self.assertEqual(ctx.settings["server"]["system"], "Jita")


class PluginContextSpeakTests(unittest.TestCase):
    def test_speak_noop_when_tts_disabled(self):
        from evealert.plugin_api import PluginContext

        ctx = PluginContext(
            settings={"notifications": {"tts_enabled": False}}, log_fn=lambda t: None
        )
        with patch("evealert.tools.tts.speak") as mock_speak:
            ctx.speak("hello")
        mock_speak.assert_not_called()

    def test_speak_calls_tts_when_enabled(self):
        from evealert.plugin_api import PluginContext

        ctx = PluginContext(
            settings={"notifications": {"tts_enabled": True, "tts_rate": 200}},
            log_fn=lambda t: None,
        )
        with patch("evealert.tools.tts.speak") as mock_speak:
            ctx.speak("hello")
        mock_speak.assert_called_once_with("hello", 200)

    def test_speak_failure_never_raises(self):
        from evealert.plugin_api import PluginContext

        ctx = PluginContext(
            settings={"notifications": {"tts_enabled": True}}, log_fn=lambda t: None
        )
        with patch("evealert.tools.tts.speak", side_effect=RuntimeError("no audio device")):
            ctx.speak("hello")  # must not raise


class PluginContextWebhookTests(unittest.TestCase):
    def test_fire_webhook_posts_json(self):
        from evealert.plugin_api import PluginContext

        ctx = PluginContext(settings={}, log_fn=lambda t: None)
        with patch("httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            ctx.fire_webhook("https://example.com/hook", {"foo": "bar"})
        mock_client.post.assert_called_once_with(
            "https://example.com/hook", json={"foo": "bar"}
        )

    def test_fire_webhook_failure_never_raises(self):
        from evealert.plugin_api import PluginContext

        ctx = PluginContext(settings={}, log_fn=lambda t: None)
        with patch("httpx.Client", side_effect=RuntimeError("network down")):
            ctx.fire_webhook("https://example.com/hook", {})  # must not raise


class EventDataclassTests(unittest.TestCase):
    def test_alarm_event_defaults(self):
        from evealert.plugin_api import AlarmEvent

        event = AlarmEvent(alarm_type="Enemy", system="Jita", timestamp="12:00:00")
        self.assertIsNone(event.client_name)

    def test_killmail_event_defaults(self):
        from evealert.plugin_api import KillmailEvent

        km = KillmailEvent(
            killmail_id=1, system_id=2, system_name="Jita", victim_ship_type_id=None
        )
        self.assertEqual(km.attacker_character_ids, ())
        self.assertIsNone(km.jump_distance)

    def test_threat_score_event_defaults(self):
        from evealert.plugin_api import ThreatScoreEvent

        event = ThreatScoreEvent(score=5, label="HIGH")
        self.assertEqual(event.reasons, ())
        self.assertIsNone(event.behavioral_label)

    def test_events_are_frozen(self):
        from evealert.plugin_api import AlarmEvent

        event = AlarmEvent(alarm_type="Enemy", system="Jita", timestamp="12:00:00")
        with self.assertRaises(Exception):
            event.system = "Amarr"


if __name__ == "__main__":
    unittest.main()
