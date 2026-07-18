"""Tests for #174 multi-client support (v7.2 MVP).

Covers: settings["clients"] loading/migration, _ExtraClient runtime state,
per-extra-client dedup/cooldown independence, and the alarm_detection()/
play_sound() client_name threading -- the core "two clients configured,
enemy icon in either fires a correctly-labeled alarm" acceptance
criterion, plus "single-client legacy settings keep working with zero
user action".
"""

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from evealert.manager.alertmanager import AlertAgent, _ExtraClient
from evealert.settings.store import reset_settings_store


def _valid_regions() -> dict:
    """Non-degenerate top-level regions -- ConfigValidator early-returns
    load_settings() before it ever reaches the clients-processing code
    if alert_region_1 == alert_region_2 (the recurring gotcha in this
    test suite)."""
    return {
        "alert_region_1": {"x": 100, "y": 100},
        "alert_region_2": {"x": 300, "y": 300},
        "faction_region_1": {"x": 400, "y": 100},
        "faction_region_2": {"x": 600, "y": 300},
    }


def _client_entry(name="Alt", character="", x1=10, y1=10, x2=50, y2=50,
                   x1f=60, y1f=10, x2f=100, y2f=50, enabled=True):
    return {
        "name": name,
        "character": character,
        "alert_region_1": {"x": x1, "y": y1},
        "alert_region_2": {"x": x2, "y": y2},
        "faction_region_1": {"x": x1f, "y": y1f},
        "faction_region_2": {"x": x2f, "y": y2f},
        "enabled": enabled,
    }


class MultiClientTestCase(unittest.TestCase):
    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()
        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
        with open(self.settings_path, "w") as f:
            json.dump({}, f)
        reset_settings_store(self.settings_path)
        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            self.agent = AlertAgent(self.mock_main)

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_settings(self, extra: dict) -> None:
        payload = {**_valid_regions(), **extra}
        with open(self.settings_path, "w") as f:
            json.dump(payload, f)
        reset_settings_store(self.settings_path)


class LegacySingleClientTests(MultiClientTestCase):
    """#174 acceptance criterion: single-client legacy settings keep
    working with zero user action."""

    def test_absent_clients_key_uses_legacy_top_level_regions(self):
        self._write_settings({})
        self.agent.load_settings()

        self.assertEqual((self.agent.x1, self.agent.y1, self.agent.x2, self.agent.y2),
                          (100, 100, 300, 300))
        self.assertEqual(self.agent._extra_clients, [])

    def test_empty_clients_list_uses_legacy_top_level_regions(self):
        self._write_settings({"clients": []})
        self.agent.load_settings()

        self.assertEqual((self.agent.x1, self.agent.y1), (100, 100))
        self.assertEqual(self.agent._extra_clients, [])

    def test_single_entry_clients_list_has_no_extra_clients(self):
        self._write_settings({"clients": [_client_entry(name="Main", x1=5, y1=5, x2=45, y2=45)]})
        self.agent.load_settings()

        self.assertEqual((self.agent.x1, self.agent.y1, self.agent.x2, self.agent.y2),
                          (5, 5, 45, 45))
        self.assertEqual(self.agent._extra_clients, [])


class ClientsListLoadingTests(MultiClientTestCase):
    def test_first_entry_becomes_primary_region_overriding_legacy_keys(self):
        self._write_settings({
            "clients": [_client_entry(name="Main", x1=1, y1=2, x2=3, y2=4)],
        })
        self.agent.load_settings()
        # NOT the legacy top-level (100,100,300,300) -- clients[0] wins.
        self.assertEqual((self.agent.x1, self.agent.y1, self.agent.x2, self.agent.y2),
                          (1, 2, 3, 4))

    def test_extra_clients_built_from_remaining_entries(self):
        self._write_settings({
            "clients": [
                _client_entry(name="Main", x1=1, y1=1, x2=2, y2=2),
                _client_entry(name="Alt1", x1=10, y1=10, x2=50, y2=50),
                _client_entry(name="Alt2", x1=60, y1=60, x2=90, y2=90),
            ],
        })
        self.agent.load_settings()

        self.assertEqual(len(self.agent._extra_clients), 2)
        names = [c.name for c in self.agent._extra_clients]
        self.assertEqual(names, ["Alt1", "Alt2"])
        self.assertEqual(
            (self.agent._extra_clients[0].x1, self.agent._extra_clients[0].y1,
             self.agent._extra_clients[0].x2, self.agent._extra_clients[0].y2),
            (10, 10, 50, 50),
        )

    def test_extra_client_disabled_flag_read(self):
        self._write_settings({
            "clients": [
                _client_entry(name="Main"),
                _client_entry(name="Alt", enabled=False),
            ],
        })
        self.agent.load_settings()
        self.assertFalse(self.agent._extra_clients[0].enabled)

    def test_extra_client_character_field_read(self):
        self._write_settings({
            "clients": [
                _client_entry(name="Main"),
                _client_entry(name="Alt", character="Bob Alt"),
            ],
        })
        self.agent.load_settings()
        self.assertEqual(self.agent._extra_clients[0].character, "Bob Alt")

    def test_unnamed_client_gets_a_default_name(self):
        entry = _client_entry(name="Main")
        alt = _client_entry(name="", x1=10, y1=10, x2=50, y2=50)
        self._write_settings({"clients": [entry, alt]})
        self.agent.load_settings()
        self.assertTrue(self.agent._extra_clients[0].name)  # non-empty fallback


class RebuildExtraClientsIdempotenceTests(MultiClientTestCase):
    """Reloading settings with an UNCHANGED clients[1:] region/name must
    preserve the existing runtime objects (and their dedup/cooldown
    state), not silently reset every extra client on every save."""

    def test_identical_reload_preserves_the_same_runtime_object(self):
        clients = [_client_entry(name="Main"), _client_entry(name="Alt", x1=10, y1=10, x2=50, y2=50)]
        self._write_settings({"clients": clients})
        self.agent.load_settings()
        first_instance = self.agent._extra_clients[0]
        first_instance.seen_enemies = {"marker": object()}  # sentinel

        # Reload with the exact same client config.
        self._write_settings({"clients": clients})
        self.agent.load_settings()

        self.assertIs(self.agent._extra_clients[0], first_instance)
        self.assertIn("marker", self.agent._extra_clients[0].seen_enemies)

    def test_region_change_rebuilds_and_closes_old_client(self):
        clients = [_client_entry(name="Main"), _client_entry(name="Alt", x1=10, y1=10, x2=50, y2=50)]
        self._write_settings({"clients": clients})
        self.agent.load_settings()
        old_instance = self.agent._extra_clients[0]
        old_instance.wincap = MagicMock()

        moved = [_client_entry(name="Main"), _client_entry(name="Alt", x1=99, y1=99, x2=150, y2=150)]
        self._write_settings({"clients": moved})
        self.agent.load_settings()

        self.assertIsNot(self.agent._extra_clients[0], old_instance)
        self.assertEqual(self.agent._extra_clients[0].x1, 99)
        old_instance.wincap.close.assert_called_once()

    def test_enabled_flag_change_alone_updates_in_place(self):
        clients = [_client_entry(name="Main"), _client_entry(name="Alt", x1=10, y1=10, x2=50, y2=50, enabled=True)]
        self._write_settings({"clients": clients})
        self.agent.load_settings()
        original = self.agent._extra_clients[0]

        toggled = [_client_entry(name="Main"), _client_entry(name="Alt", x1=10, y1=10, x2=50, y2=50, enabled=False)]
        self._write_settings({"clients": toggled})
        self.agent.load_settings()

        self.assertIs(self.agent._extra_clients[0], original)  # not rebuilt
        self.assertFalse(self.agent._extra_clients[0].enabled)


class ExtraClientTests(unittest.TestCase):
    def test_region_key_reflects_name_and_all_region_coords(self):
        c = _ExtraClient(
            name="Alt", character="", x1=1, y1=2, x2=3, y2=4,
            x1_faction=5, y1_faction=6, x2_faction=7, y2_faction=8,
            enabled=True, needle_paths=[], needle_faction_paths=[],
        )
        self.assertEqual(c.region_key(), ("Alt", 1, 2, 3, 4, 5, 6, 7, 8))

    def test_close_releases_wincap_and_vision(self):
        c = _ExtraClient(
            name="Alt", character="", x1=0, y1=0, x2=1, y2=1,
            x1_faction=0, y1_faction=0, x2_faction=1, y2_faction=1,
            enabled=True, needle_paths=[], needle_faction_paths=[],
        )
        c.wincap = MagicMock()
        c.vision = MagicMock()
        c.vision_faction = MagicMock()
        c.close()
        c.wincap.close.assert_called_once()
        c.vision.clean_up.assert_called_once()
        c.vision_faction.clean_up.assert_called_once()


class ShouldAlarmExtraClientEnemyTests(MultiClientTestCase):
    def _make_client(self):
        return _ExtraClient(
            name="Alt", character="", x1=0, y1=0, x2=1, y2=1,
            x1_faction=0, y1_faction=0, x2_faction=1, y2_faction=1,
            enabled=True, needle_paths=[], needle_faction_paths=[],
        )

    def test_new_enemy_triggers(self):
        client = self._make_client()
        client.enemy_points = [(10, 10)]
        self.assertTrue(self.agent._should_alarm_extra_client_enemy(client))

    def test_same_enemy_does_not_retrigger(self):
        client = self._make_client()
        client.enemy_points = [(10, 10)]
        self.agent._should_alarm_extra_client_enemy(client)  # first sighting
        self.assertFalse(self.agent._should_alarm_extra_client_enemy(client))

    def test_primary_clients_seen_enemies_is_untouched(self):
        """Extra-client dedup must never leak into the primary client's
        own _seen_enemies dict."""
        client = self._make_client()
        client.enemy_points = [(10, 10)]
        self.agent._seen_enemies = {}
        self.agent._should_alarm_extra_client_enemy(client)
        self.assertEqual(self.agent._seen_enemies, {})

    def test_two_extra_clients_dedup_independently(self):
        client_a = self._make_client()
        client_a.name = "A"
        client_a.enemy_points = [(10, 10)]
        client_b = self._make_client()
        client_b.name = "B"
        client_b.enemy_points = [(10, 10)]  # same position, different client

        self.assertTrue(self.agent._should_alarm_extra_client_enemy(client_a))
        # client_a's seen_enemies must not affect client_b's independent dict
        self.assertTrue(self.agent._should_alarm_extra_client_enemy(client_b))
        self.assertFalse(self.agent._should_alarm_extra_client_enemy(client_a))
        self.assertFalse(self.agent._should_alarm_extra_client_enemy(client_b))


class ResetExtraClientAlarmTests(MultiClientTestCase):
    def test_clears_only_this_clients_cooldown_state(self):
        client = _ExtraClient(
            name="Alt", character="", x1=0, y1=0, x2=1, y2=1,
            x1_faction=0, y1_faction=0, x2_faction=1, y2_faction=1,
            enabled=True, needle_paths=[], needle_faction_paths=[],
        )
        self.agent.alarm_trigger_counts[("Alt", "Enemy")] = 3
        self.agent.cooldown_timers[("Alt", "Enemy")] = time.time() + 100
        self.agent.alarm_trigger_counts["Enemy"] = 7  # primary client's own count

        self.agent._reset_extra_client_alarm(client, "Enemy")

        self.assertEqual(self.agent.alarm_trigger_counts[("Alt", "Enemy")], 0)
        self.assertEqual(self.agent.cooldown_timers[("Alt", "Enemy")], 0)
        self.assertEqual(self.agent.alarm_trigger_counts["Enemy"], 7)  # untouched

    def test_clears_seen_enemies_for_enemy_alarm_type(self):
        client = _ExtraClient(
            name="Alt", character="", x1=0, y1=0, x2=1, y2=1,
            x1_faction=0, y1_faction=0, x2_faction=1, y2_faction=1,
            enabled=True, needle_paths=[], needle_faction_paths=[],
        )
        client.seen_enemies = {"x": object()}
        self.agent._reset_extra_client_alarm(client, "Enemy")
        self.assertEqual(client.seen_enemies, {})

    def test_faction_reset_does_not_touch_seen_enemies(self):
        client = _ExtraClient(
            name="Alt", character="", x1=0, y1=0, x2=1, y2=1,
            x1_faction=0, y1_faction=0, x2_faction=1, y2_faction=1,
            enabled=True, needle_paths=[], needle_faction_paths=[],
        )
        client.seen_enemies = {"x": object()}
        self.agent._reset_extra_client_alarm(client, "Faction")
        self.assertIn("x", client.seen_enemies)  # unchanged -- Faction reset must not touch it


class PlaySoundClientNameTests(MultiClientTestCase):
    """play_sound() cooldown/trigger-count isolation between the primary
    client (bare alarm_type key) and named extra clients."""

    async def _play(self, client_name, sound="fake.wav"):
        with patch("evealert.manager.alertmanager._SOUNDDEVICE_AVAILABLE", True), \
             patch("evealert.manager.alertmanager.sf.read", return_value=([0, 0], 44100)), \
             patch("evealert.manager.alertmanager.sd.play"), \
             patch("evealert.manager.alertmanager.sd.wait"):
            await self.agent.play_sound(sound, "Enemy", client_name)

    def test_primary_client_cooldown_key_is_unchanged_plain_string(self):
        asyncio.run(self._play(None))
        self.assertIn("Enemy", self.agent.alarm_trigger_counts)
        self.assertNotIn(("Enemy",), self.agent.alarm_trigger_counts)

    def test_named_client_cooldown_key_is_a_tuple(self):
        asyncio.run(self._play("Alt"))
        self.assertIn(("Alt", "Enemy"), self.agent.alarm_trigger_counts)
        self.assertNotIn("Enemy", self.agent.alarm_trigger_counts)

    def test_primary_and_extra_client_cooldowns_are_independent(self):
        # Exhaust the primary client's trigger count into cooldown.
        self.agent.max_sound_triggers = 1
        asyncio.run(self._play(None))
        asyncio.run(self._play(None))  # now in cooldown
        self.assertGreater(self.agent.cooldown_timers["Enemy"], time.time())

        # The extra client must be unaffected -- its own key starts fresh.
        asyncio.run(self._play("Alt"))
        self.assertEqual(self.agent.cooldown_timers.get(("Alt", "Enemy"), 0), 0)


class AlarmDetectionClientNameTests(MultiClientTestCase):
    def _logged_messages(self) -> list:
        return [c.args[0] for c in self.mock_main.write_message.call_args_list]

    async def _fire(self, client_name):
        with patch.object(self.agent, "play_sound", new=AsyncMock()), \
             patch.object(self.agent, "send_webhook_message", new=AsyncMock()) as mock_webhook, \
             patch("evealert.manager.alertmanager.save_lifetime_stats"):
            await self.agent.alarm_detection(
                "Enemy Appears!", "fake.wav", "Enemy", client_name
            )
        return mock_webhook

    def test_client_name_prefixes_the_log_message(self):
        asyncio.run(self._fire("Alt"))
        messages = self._logged_messages()
        self.assertTrue(any(m.startswith("[Alt] Enemy Appears!") for m in messages))

    def test_no_client_name_leaves_message_unprefixed(self):
        asyncio.run(self._fire(None))
        messages = self._logged_messages()
        self.assertIn("Enemy Appears!", messages)
        self.assertFalse(any(m.startswith("[") for m in messages))

    def test_extra_client_alarm_skips_discord_webhook(self):
        mock_webhook = asyncio.run(self._fire("Alt"))
        mock_webhook.assert_not_awaited()

    def test_primary_client_alarm_still_sends_discord_webhook(self):
        mock_webhook = asyncio.run(self._fire(None))
        mock_webhook.assert_awaited_once()

    def test_extra_client_alarm_passes_client_name_to_play_sound(self):
        with patch.object(self.agent, "play_sound", new=AsyncMock()) as mock_play, \
             patch.object(self.agent, "send_webhook_message", new=AsyncMock()), \
             patch("evealert.manager.alertmanager.save_lifetime_stats"):
            asyncio.run(self.agent.alarm_detection("Enemy Appears!", "fake.wav", "Enemy", "Alt"))
        mock_play.assert_awaited_once_with("fake.wav", "Enemy", "Alt")


class RunLoopTwoClientsIntegrationTests(MultiClientTestCase):
    """#174 acceptance criterion: two EVE clients configured; enemy icon
    in either fires a correctly-labeled alarm. Exercises run()'s actual
    dispatch logic for one poll cycle, not just the helper methods."""

    def setUp(self):
        super().setUp()
        self._write_settings({
            "clients": [_client_entry(name="Main"), _client_entry(name="Alt", x1=10, y1=10, x2=50, y2=50)],
        })
        self.agent.load_settings()
        self.agent.loop = MagicMock()  # alarm_detection may schedule tasks

    def _logged_messages(self) -> list:
        return [c.args[0] for c in self.mock_main.write_message.call_args_list]

    async def _one_cycle(self):
        with patch.object(self.agent, "play_sound", new=AsyncMock()), \
             patch.object(self.agent, "send_webhook_message", new=AsyncMock()), \
             patch.object(self.agent, "_resolve_enemy_identities", return_value={}), \
             patch("evealert.manager.alertmanager.save_lifetime_stats"), \
             patch("evealert.manager.alertmanager.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
            try:
                await self.agent.run()
            except asyncio.CancelledError:
                pass  # run() loops forever -- bail out after exactly one cycle

    def test_extra_client_enemy_fires_a_labeled_alarm(self):
        self.agent.enemy = False
        self.agent.faction = False
        extra = self.agent._extra_clients[0]
        extra.enemy = True
        extra.enemy_points = [(5, 5)]

        asyncio.run(self._one_cycle())

        messages = self._logged_messages()
        self.assertTrue(
            any(m.startswith("[Alt] Enemy Appears!") for m in messages),
            f"Expected a labeled Alt alarm, got: {messages}",
        )

    def test_primary_client_enemy_fires_an_unlabeled_alarm(self):
        self.agent.enemy = True
        self.agent._enemy_points = [(5, 5)]
        self.agent.faction = False
        self.agent._extra_clients[0].enemy = False

        asyncio.run(self._one_cycle())

        messages = self._logged_messages()
        self.assertTrue(any(m.startswith("Enemy Appears!") for m in messages))
        self.assertFalse(any(m.startswith("[Main]") for m in messages))

    def test_both_clients_simultaneously_each_fire_their_own_alarm(self):
        self.agent.enemy = True
        self.agent._enemy_points = [(1, 1)]
        self.agent.faction = False
        extra = self.agent._extra_clients[0]
        extra.enemy = True
        extra.enemy_points = [(9, 9)]

        asyncio.run(self._one_cycle())

        messages = self._logged_messages()
        self.assertTrue(any(m.startswith("Enemy Appears!") for m in messages))
        self.assertTrue(any(m.startswith("[Alt] Enemy Appears!") for m in messages))

    def test_disabled_extra_client_never_alarms(self):
        self.agent.enemy = False
        self.agent.faction = False
        extra = self.agent._extra_clients[0]
        extra.enabled = False
        extra.enemy = True
        extra.enemy_points = [(5, 5)]

        asyncio.run(self._one_cycle())

        messages = self._logged_messages()
        self.assertFalse(any("Alt" in m for m in messages))


if __name__ == "__main__":
    unittest.main()
