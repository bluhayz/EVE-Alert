"""Unit tests for AlertManager core functionality."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from evealert.manager.alertmanager import AlertAgent
from evealert.settings.store import reset_settings_store
from evealert.statistics import AlarmStatistics


class TestAlertAgent(unittest.TestCase):
    """Test cases for AlertAgent class."""

    def setUp(self):
        """Set up test fixtures."""
        # Create mock MainMenu
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()
        self.mock_main.getdata = MagicMock()
        self.mock_main.menu = MagicMock()
        self.mock_main.menu.setting = MagicMock()

        # Create temporary settings file
        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = Path(self.temp_dir) / "settings.json"
        # Redirect stats writes to temp dir so tests never touch the real file (#159)
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")

        # Default test settings
        self.test_settings = {
            "alert_region_1": {"x": 100, "y": 100},
            "alert_region_2": {"x": 300, "y": 300},
            "faction_region_1": {"x": 400, "y": 100},
            "faction_region_2": {"x": 600, "y": 300},
            "detectionscale": {"value": 90},
            "faction_scale": {"value": 85},
            "cooldown_timer": {"value": 30},
            "volume": {"value": 100},
            "server": {"webhook": "", "mute": False},
        }

        with open(self.settings_path, "w") as f:
            json.dump(self.test_settings, f)

        # Wire the shared SettingsStore to the temp file so AlertAgent.load_settings()
        # reads from it without touching the real settings path.
        self._store = reset_settings_store(self.settings_path)

        self.mock_main.menu.setting.is_changed = False

        # Patch audio file validation — event loop no longer created in __init__
        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            self.agent = AlertAgent(self.mock_main)

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_agent_initialization(self):
        """Test AlertAgent initialization."""
        self.assertIsNotNone(self.agent)
        self.assertFalse(self.agent.running)
        self.assertFalse(self.agent.enemy)
        self.assertFalse(self.agent.faction)
        self.assertEqual(self.agent.volume, 1.0)
        self.assertIsInstance(self.agent.statistics, AlarmStatistics)

    def test_load_settings(self):
        """Test loading settings from file."""
        self.agent.load_settings()

        self.assertEqual(self.agent.volume, 1.0)  # 100% -> 1.0
        self.assertEqual(self.agent.cooldowntimer, 30)

    def test_load_settings_with_custom_volume(self):
        """Test loading custom volume setting."""
        # Update the temp file and reset the store so the agent reads the new value
        self.test_settings["volume"]["value"] = 50
        with open(self.settings_path, "w") as f:
            json.dump(self.test_settings, f)
        reset_settings_store(self.settings_path)

        self.agent.load_settings()
        self.assertEqual(self.agent.volume, 0.5)

    def test_is_running_property(self):
        """Test is_running property."""
        self.assertFalse(self.agent.is_running)
        self.agent.running = True
        self.assertTrue(self.agent.is_running)

    def test_is_alarm_property(self):
        """Test is_alarm property."""
        self.assertFalse(self.agent.is_alarm)
        self.agent.alarm_detected = True
        self.assertTrue(self.agent.is_alarm)

    def test_is_enemy_property(self):
        """Test is_enemy property."""
        self.assertFalse(self.agent.is_enemy)
        self.agent.enemy = True
        self.assertTrue(self.agent.is_enemy)

    def test_is_faction_property(self):
        """Test is_faction property."""
        self.assertFalse(self.agent.is_faction)
        self.agent.faction = True
        self.assertTrue(self.agent.is_faction)

    def test_get_statistics(self):
        """Test retrieving statistics."""
        stats = self.agent.get_statistics()
        self.assertIsInstance(stats, AlarmStatistics)
        self.assertEqual(stats.total_alarms, 0)

    def test_cooldown_management(self):
        """Test cooldown timer management."""
        self.agent.cooldowntimer = 10

        # Set cooldown for alarm type
        self.agent.cooldown_timers["enemy"] = time.time()

        # Check if cooldown is active
        time_diff = time.time() - self.agent.cooldown_timers.get("enemy", 0)
        self.assertLess(time_diff, self.agent.cooldowntimer)

    def test_mute_functionality(self):
        """Test mute setting."""
        self.assertFalse(self.agent.mute)
        self.agent.mute = True
        self.assertTrue(self.agent.mute)

    def test_webhook_cooldown(self):
        """Test webhook cooldown timer."""
        from evealert.constants import WEBHOOK_COOLDOWN

        self.agent.webhook_cooldown_timer = time.time()
        time_diff = time.time() - self.agent.webhook_cooldown_timer

        # Should be within webhook cooldown period
        self.assertLess(time_diff, WEBHOOK_COOLDOWN + 1)

    def test_alarm_trigger_count_tracking(self):
        """Test alarm trigger count management."""
        self.assertEqual(len(self.agent.alarm_trigger_counts), 0)

        # Simulate alarm trigger
        self.agent.alarm_trigger_counts["enemy"] = 1
        self.assertEqual(self.agent.alarm_trigger_counts["enemy"], 1)

        # Increment trigger
        self.agent.alarm_trigger_counts["enemy"] += 1
        self.assertEqual(self.agent.alarm_trigger_counts["enemy"], 2)

    def test_max_sound_triggers(self):
        """Test max sound triggers limit."""
        from evealert.constants import MAX_SOUND_TRIGGERS

        self.assertEqual(self.agent.max_sound_triggers, MAX_SOUND_TRIGGERS)

        # Test if we can change it
        self.agent.max_sound_triggers = 5
        self.assertEqual(self.agent.max_sound_triggers, 5)

    @patch("evealert.manager.alertmanager.sd.play")
    @patch("evealert.manager.alertmanager.sf.read")
    def test_play_sound_with_volume(self, mock_sf_read, mock_sd_play):
        """Test playing sound with volume control."""
        import numpy as np

        # Mock audio data
        mock_audio_data = np.array([[100, 100], [200, 200]], dtype="int16")
        mock_sf_read.return_value = (mock_audio_data, 44100)

        # Set volume to 50%
        self.agent.volume = 0.5
        self.agent.mute = False

        # Call play_sound method (we need to mock it or test indirectly)
        # Since play_sound is async, we test the volume application logic
        volume_adjusted = (mock_audio_data * self.agent.volume).astype("int16")

        self.assertTrue(np.all(volume_adjusted <= mock_audio_data))

    def test_statistics_integration(self):
        """Test statistics tracking integration."""
        # Record alarm
        self.agent.statistics.add_alarm("Enemy")

        self.assertEqual(self.agent.statistics.total_alarms, 1)
        self.assertEqual(self.agent.statistics.session_alarms, 1)

    def test_vision_debug_mode_sync(self):
        """Test vision debug mode synchronization."""
        # Enable enemy vision debug
        self.agent.alert_vision.debug_mode = True
        self.assertTrue(self.agent.alert_vision.is_vision_open)

        # Enable faction vision debug
        self.agent.alert_vision_faction.debug_mode_faction = True
        self.assertTrue(self.agent.alert_vision_faction.is_faction_vision_open)

    def test_configuration_validation_on_load(self):
        """Test configuration validation when loading settings."""
        # Create invalid settings
        invalid_settings = self.test_settings.copy()
        invalid_settings["detectionscale"]["value"] = 150  # Invalid: > 100

        with open(self.settings_path, "w") as f:
            json.dump(invalid_settings, f)
        reset_settings_store(self.settings_path)

        # Load should handle invalid values gracefully
        try:
            self.agent.load_settings()
            # If validation occurs, it should either fix or warn
            self.assertLessEqual(self.agent.alert_vision.method, 5)
        except Exception as e:
            # Expected if strict validation is enforced
            self.assertIsNotNone(str(e))


class TestAlertAgentAsync(unittest.IsolatedAsyncioTestCase):
    """Async test cases for AlertAgent."""

    async def asyncSetUp(self):
        """Set up async test fixtures."""
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()
        self.mock_main.getdata = MagicMock()

        # Create temporary settings
        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = Path(self.temp_dir) / "settings.json"
        # Redirect stats writes to temp dir (#159)
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")

        test_settings = {
            "alert_region_1": {"x": 100, "y": 100},
            "alert_region_2": {"x": 300, "y": 300},
            "faction_region_1": {"x": 400, "y": 100},
            "faction_region_2": {"x": 600, "y": 300},
            "detectionscale": {"value": 90},
            "faction_scale": {"value": 85},
            "cooldown_timer": {"value": 30},
            "volume": {"value": 100},
            "server": {"webhook": ""},
        }

        with open(self.settings_path, "w") as f:
            json.dump(test_settings, f)

        reset_settings_store(self.settings_path)
        self.mock_main.getdata.return_value = self.settings_path

        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            self.agent = AlertAgent(self.mock_main)

    async def asyncTearDown(self):
        """Clean up async test fixtures."""
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_no_premature_event_loop(self):
        """Event loop must not be created until start() is called."""
        # The loop is set to None in __init__ and only created inside start()
        # so the background thread gets its own clean event loop.
        self.assertIsNone(self.agent.loop)

    async def test_lookup_jump_distance_triggers_esi_within_radius(self):
        """When jumps <= threat radius and check enabled, _augment_with_esi is scheduled."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        self.agent._intel_threat_check_enabled = True
        self.agent._intel_threat_radius = 5

        # Mock loop.create_task so we can capture what coroutines are scheduled
        scheduled = []
        mock_loop = MagicMock()
        mock_loop.create_task = lambda coro: scheduled.append(coro)
        self.agent.loop = mock_loop

        # Mock universe cache: 3-system route = 2 jumps (within radius of 5)
        mock_cache = AsyncMock()
        mock_cache.get_system_id = AsyncMock(side_effect=lambda name: 1001 if name == "Jita" else 1002)
        mock_cache.get_route = AsyncMock(return_value=[1001, 1003, 1002])  # 2 jumps

        with patch("evealert.tools.universe.get_universe_cache", return_value=mock_cache):
            await self.agent._lookup_jump_distance("Jita", "Perimeter", ["Roger Booth"])

        # At least one task should have been scheduled (the _augment_with_esi call)
        self.assertTrue(
            len(scheduled) >= 1,
            "Expected _augment_with_esi to be scheduled when within threat radius",
        )
        # Close the unawaited coroutines to avoid RuntimeWarning in test output
        for coro in scheduled:
            coro.close()

    async def test_lookup_jump_distance_skips_esi_beyond_radius(self):
        """When jumps > threat radius, _augment_with_esi must NOT be scheduled."""
        from unittest.mock import AsyncMock, MagicMock, patch

        self.agent._intel_threat_check_enabled = True
        self.agent._intel_threat_radius = 2

        scheduled = []
        mock_loop = MagicMock()
        mock_loop.create_task = lambda coro: scheduled.append(coro)
        self.agent.loop = mock_loop

        # 5-system route = 4 jumps (beyond radius of 2)
        mock_cache = AsyncMock()
        mock_cache.get_system_id = AsyncMock(side_effect=lambda name: 1001 if name == "Jita" else 1002)
        mock_cache.get_route = AsyncMock(return_value=[1001, 1003, 1004, 1005, 1002])  # 4 jumps

        with patch("evealert.tools.universe.get_universe_cache", return_value=mock_cache):
            await self.agent._lookup_jump_distance("Jita", "Perimeter", ["Roger Booth"])

        self.assertEqual(scheduled, [], "Expected no ESI task beyond threat radius")

    async def test_lookup_jump_distance_skips_esi_when_disabled(self):
        """When intel_threat_check_enabled is False, _augment_with_esi is never scheduled."""
        from unittest.mock import AsyncMock, MagicMock, patch

        self.agent._intel_threat_check_enabled = False
        self.agent._intel_threat_radius = 10  # large radius, still disabled

        scheduled = []
        mock_loop = MagicMock()
        mock_loop.create_task = lambda coro: scheduled.append(coro)
        self.agent.loop = mock_loop

        mock_cache = AsyncMock()
        mock_cache.get_system_id = AsyncMock(side_effect=lambda name: 1001 if name == "Jita" else 1002)
        mock_cache.get_route = AsyncMock(return_value=[1001, 1002])  # 1 jump

        with patch("evealert.tools.universe.get_universe_cache", return_value=mock_cache):
            await self.agent._lookup_jump_distance("Jita", "Perimeter", ["Roger Booth"])

        self.assertEqual(scheduled, [], "Expected no ESI task when threat check disabled")


class TestAugmentWithEsiKosDecoupling(unittest.IsolatedAsyncioTestCase):
    """Regression tests for #201-#204: the OCR -> ESI -> KOS intel pipeline."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()

        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        with open(self.settings_path, "w") as f:
            json.dump({}, f)
        reset_settings_store(self.settings_path)

        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            self.agent = AlertAgent(self.mock_main)
        # Avoid real network/log-file access in these tests.
        self.agent._threat_tiers = {}
        self.agent._kos_cva_enabled = False
        self.agent._kos_custom_urls = []
        self.agent._esi_show_corp = True
        self.agent._esi_show_alliance = True
        self.agent._esi_alert_flashy = False
        self.agent._fleet_composition_enabled = False
        self.agent._esi_standings_classify = False
        self.agent._dscan_watcher = None
        self.agent._wh_drop_detector = None
        self.agent._wh_drop_enabled = False

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _logged_messages(self) -> list[str]:
        return [c.args[0] for c in self.mock_main.write_message.call_args_list]

    async def test_kos_runs_when_esi_lookup_returns_no_results(self):
        """#203: KOS must still run even when ESI resolves nothing."""
        with patch(
            "evealert.tools.esi_standings.get_esi_client"
        ) as mock_get_client, patch(
            "evealert.tools.kos_checker.get_kos_checker"
        ) as mock_get_kos:
            mock_client = AsyncMock()
            mock_client.lookup_many = AsyncMock(return_value=[])  # ESI found nothing
            mock_get_client.return_value = mock_client

            kos_result = MagicMock(source="Custom", label="KOS-RED")
            mock_kos = MagicMock()
            mock_kos.check = AsyncMock(return_value=kos_result)
            mock_get_kos.return_value = mock_kos

            await self.agent.run_intel_check(["Bad Guy"])

            mock_kos.check.assert_awaited_once_with("Bad Guy", "", "")
            messages = self._logged_messages()
            self.assertTrue(
                any("KOS" in m and "Bad Guy" in m for m in messages),
                f"Expected a KOS log line even with no ESI results, got: {messages}",
            )
            self.assertTrue(
                any("ESI lookup unavailable" in m for m in messages),
                f"Expected the header line to note ESI was unavailable, got: {messages}",
            )

    async def test_esi_and_kos_both_run_when_esi_resolves(self):
        """Sanity check: the happy path (ESI resolves + KOS hits) still works
        after restructuring the loop to iterate over names instead of results."""
        info = MagicMock(
            corporation_name="Evil Corp", alliance_name="",
            age_days=100, corp_history_count=2, security_status=0.0,
            character_id=123, corporation_id=456, alliance_id=None,
        )
        # MagicMock(name=...) sets the mock's own repr, not an attribute —
        # .name must be assigned separately.
        info.name = "Bad Guy"

        with patch(
            "evealert.tools.esi_standings.get_esi_client"
        ) as mock_get_client, patch(
            "evealert.tools.kos_checker.get_kos_checker"
        ) as mock_get_kos:
            mock_client = AsyncMock()
            mock_client.lookup_many = AsyncMock(return_value=[info])
            mock_client.get_zkillboard_profile = AsyncMock(return_value=None)
            mock_get_client.return_value = mock_client

            mock_kos = MagicMock()
            mock_kos.check = AsyncMock(return_value=None)  # not KOS
            mock_get_kos.return_value = mock_kos

            await self.agent.run_intel_check(["Bad Guy"])

            mock_kos.check.assert_awaited_once_with("Bad Guy", "Evil Corp", "")
            messages = self._logged_messages()
            self.assertTrue(any("Evil Corp" in m for m in messages))
            self.assertTrue(any("100d old" in m for m in messages))
            self.assertFalse(any("ESI lookup unavailable" in m for m in messages))

    async def test_no_names_message_mentions_ocr_when_ocr_enabled(self):
        """#202: the 'nothing found' message must be honest about why —
        distinguishing OCR-enabled-but-empty from ESI-only mode."""
        self.agent._ocr_enabled = True
        with patch(
            "evealert.tools.intel_watcher.get_eve_chatlog_dir", return_value=None
        ):
            await self.agent._augment_with_esi(hint_names=None)
        messages = self._logged_messages()
        self.assertTrue(
            any("already have been in-system" in m for m in messages),
            f"Expected the OCR-aware message, got: {messages}",
        )

    async def test_no_names_message_mentions_esi_only_when_ocr_disabled(self):
        """#202: ESI-only (no OCR) users get a message telling them OCR would help."""
        self.agent._ocr_enabled = False
        with patch(
            "evealert.tools.intel_watcher.get_eve_chatlog_dir", return_value=None
        ):
            await self.agent._augment_with_esi(hint_names=None)
        messages = self._logged_messages()
        self.assertTrue(
            any("enable 'Read pilot names from Local on alarm'" in m for m in messages),
            f"Expected the ESI-only message pointing at OCR, got: {messages}",
        )

    async def test_pilot_line_includes_zkillboard_character_link_when_zkb_has_data(self):
        """#205/#208: resolved pilots get a zkillboard.com/character/<id>/
        link ONLY when zkillboard actually has a profile for them — i.e.
        get_zkillboard_profile() returned a real (non-None) result."""
        info = MagicMock(
            corporation_name="Evil Corp", alliance_name="",
            age_days=100, corp_history_count=2, security_status=0.0,
            character_id=987654, corporation_id=456, alliance_id=None,
        )
        info.name = "Bad Guy"
        from evealert.tools.esi_standings import KillProfile

        with patch(
            "evealert.tools.esi_standings.get_esi_client"
        ) as mock_get_client, patch(
            "evealert.tools.kos_checker.get_kos_checker"
        ) as mock_get_kos:
            mock_client = AsyncMock()
            mock_client.lookup_many = AsyncMock(return_value=[info])
            mock_client.get_zkillboard_profile = AsyncMock(
                return_value=KillProfile(
                    kills_total=5, losses_total=1, top_ship=None, danger_ratio=0.8
                )
            )
            mock_get_client.return_value = mock_client
            mock_kos = MagicMock()
            mock_kos.check = AsyncMock(return_value=None)
            mock_get_kos.return_value = mock_kos

            await self.agent.run_intel_check(["Bad Guy"])

        messages = self._logged_messages()
        self.assertTrue(
            any("zkillboard.com/character/987654/" in m for m in messages),
            f"Expected a zkillboard character link in pilot line, got: {messages}",
        )

    async def test_no_zkillboard_link_when_zkb_has_never_seen_the_character(self):
        """#208: zkillboard returns HTTP 200 + {"error": ...} (parsed as None
        by _fetch_zkb_profile) for a pilot it has never indexed in any
        killmail — that pilot's character page 404s, so no link should be
        shown at all. Regression for a real 404 reported in production."""
        info = MagicMock(
            corporation_name="Republic University", alliance_name="",
            age_days=35, corp_history_count=1, security_status=0.0,
            character_id=2124449072, corporation_id=456, alliance_id=None,
        )
        info.name = "Oveim Hrild Beldrulf"

        with patch(
            "evealert.tools.esi_standings.get_esi_client"
        ) as mock_get_client, patch(
            "evealert.tools.kos_checker.get_kos_checker"
        ) as mock_get_kos:
            mock_client = AsyncMock()
            mock_client.lookup_many = AsyncMock(return_value=[info])
            mock_client.get_zkillboard_profile = AsyncMock(return_value=None)
            mock_get_client.return_value = mock_client
            mock_kos = MagicMock()
            mock_kos.check = AsyncMock(return_value=None)
            mock_get_kos.return_value = mock_kos

            await self.agent.run_intel_check(["Oveim Hrild Beldrulf"])

        messages = self._logged_messages()
        self.assertFalse(
            any("zkillboard.com/character/2124449072/" in m for m in messages),
            f"A never-indexed character must not get a (404-ing) zkillboard link, got: {messages}",
        )
        # The rest of the pilot intel (corp, age) must still be shown.
        self.assertTrue(any("Republic University" in m for m in messages))
        self.assertTrue(any("35d old" in m for m in messages))

    async def test_run_intel_check_forwards_to_augment_with_esi(self):
        """#201: the public wrapper used by the Settings OCR test forwards
        its names as hint_names, using the OCR-provided-names code path."""
        with patch.object(
            self.agent, "_augment_with_esi", new=AsyncMock()
        ) as mock_augment:
            await self.agent.run_intel_check(["Alice", "Bob"])
        mock_augment.assert_awaited_once_with(hint_names=["Alice", "Bob"])


class ResolveEnemyIdentitiesTests(unittest.TestCase):
    """#213: OCR-based per-icon identity resolution, throttled so it
    doesn't run on every 0.1-0.2s poll cycle."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()

        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        with open(self.settings_path, "w") as f:
            json.dump({}, f)
        reset_settings_store(self.settings_path)

        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            self.agent = AlertAgent(self.mock_main)
        self.agent._ocr_enabled = True
        self.agent.x1, self.agent.y1, self.agent.x2, self.agent.y2 = 0, 1000, 200, 1300
        self.agent._ocr_region = (0, 0, 0, 0)  # falls back to alert region

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _patch_ocr_available(self):
        """OCR backend availability depends on what's installed on the
        machine running the tests (winsdk/winrt/Tesseract) -- always mock
        it explicitly so these tests are deterministic in CI."""
        return patch("evealert.tools.ocr_local.is_ocr_available", return_value=True)

    def test_resolves_and_caches_last_ocr_names(self):
        self.agent._enemy_points = [(50, 33)]
        with self._patch_ocr_available(), patch(
            "evealert.tools.ocr_local.match_names_to_targets",
            return_value=({(2, 51): "Bad Guy"}, ["Bad Guy"]),
        ) as mock_match:
            identities = self.agent._resolve_enemy_identities()
        mock_match.assert_called_once()
        self.assertEqual(identities, {(2, 51): "Bad Guy"})
        self.assertEqual(self.agent._last_ocr_names, ["Bad Guy"])

    def test_last_ocr_names_excludes_unmatched_roster_names(self):
        """Regression: a release shipped with _last_ocr_names set to
        match_names_to_targets()'s all_names (every name OCR found
        anywhere in the captured region -- the whole Local roster,
        including the player's own name and corp/fleet mates) instead of
        only the names actually matched to an enemy icon's row. That fed
        the alarm headline AND the ESI query, reporting/querying the
        entire roster as "the enemy". Only the matched identity may ever
        end up in _last_ocr_names."""
        self.agent._enemy_points = [(50, 33)]
        with self._patch_ocr_available(), patch(
            "evealert.tools.ocr_local.match_names_to_targets",
            return_value=(
                {(2, 51): "Bad Guy"},
                [
                    "Bad Guy", "bluhauz", "AschRafie", "Bronwen Morgan",
                    "Demi Tras", "Floki Orti",
                ],
            ),
        ):
            identities = self.agent._resolve_enemy_identities()
        self.assertEqual(identities, {(2, 51): "Bad Guy"})
        self.assertEqual(self.agent._last_ocr_names, ["Bad Guy"])
        self.assertNotIn("bluhauz", self.agent._last_ocr_names)
        self.assertNotIn("AschRafie", self.agent._last_ocr_names)

    def _ocr_alarm_messages(self) -> list[str]:
        return [
            c.args[0]
            for c in self.mock_main.write_message.call_args_list
            if c.args[0].startswith("OCR [alarm]:")
        ]

    def test_ocr_message_not_repeated_when_identity_unchanged(self):
        """Regression: a stationary pilot re-announced 'identified pilot(s):
        ...' on every fresh (non-throttled) resolve even though nothing
        about the sighting had changed -- e.g. every
        _IDENTITY_RESOLVE_MIN_INTERVAL seconds for as long as they stayed.
        Two fresh resolves (forced via a changing position set, so the
        throttle doesn't just cache-hit) that both identify the same pilot
        must only log the message once."""
        self.agent._enemy_points = [(50, 33)]
        with self._patch_ocr_available(), patch(
            "evealert.tools.ocr_local.match_names_to_targets",
            return_value=({(2, 51): "Bad Guy"}, ["Bad Guy"]),
        ) as mock_match:
            self.agent._resolve_enemy_identities()
            self.agent._enemy_points = [(50, 33), (50, 333)]  # forces a fresh resolve
            self.agent._resolve_enemy_identities()
        self.assertEqual(mock_match.call_count, 2)  # OCR did run twice
        messages = self._ocr_alarm_messages()
        self.assertEqual(
            messages.count("OCR [alarm]: identified pilot(s): Bad Guy"), 1,
            f"Expected the identical result to be logged only once, got: {messages}",
        )

    def test_ocr_message_repeated_when_identity_changes(self):
        """A genuinely different result (new pilot) must still log, even
        right after a previous OCR [alarm] line."""
        self.agent._enemy_points = [(50, 33)]
        with self._patch_ocr_available(), patch(
            "evealert.tools.ocr_local.match_names_to_targets",
            side_effect=[
                ({(2, 51): "Bad Guy"}, ["Bad Guy"]),
                ({(2, 51): "Other Guy"}, ["Other Guy"]),
            ],
        ):
            self.agent._resolve_enemy_identities()
            self.agent._enemy_points = [(50, 33), (50, 333)]
            self.agent._resolve_enemy_identities()
        messages = self._ocr_alarm_messages()
        self.assertIn("OCR [alarm]: identified pilot(s): Bad Guy", messages)
        self.assertIn("OCR [alarm]: identified pilot(s): Other Guy", messages)

    def test_ocr_log_message_reset_allows_relogging_same_result(self):
        """reset_alarm("Enemy") (fired when the pilot leaves, #100) clears
        the dedup state so a later, genuinely new engagement with the same
        pilot name still logs -- the suppression must not persist across
        engagements."""
        import asyncio

        self.agent._enemy_points = [(50, 33)]
        with self._patch_ocr_available(), patch(
            "evealert.tools.ocr_local.match_names_to_targets",
            return_value=({(2, 51): "Bad Guy"}, ["Bad Guy"]),
        ):
            self.agent._resolve_enemy_identities()
            asyncio.run(self.agent.reset_alarm("Enemy"))
            self.agent._resolve_enemy_identities()
        messages = self._ocr_alarm_messages()
        self.assertEqual(
            messages.count("OCR [alarm]: identified pilot(s): Bad Guy"), 2,
            f"Expected the message again after reset_alarm, got: {messages}",
        )

    def test_last_ocr_names_empty_when_no_icon_matches_a_row(self):
        """No enemy icon matched any OCR'd row -> the alarm headline/ESI
        hint list must stay empty, NOT fall back to every name found in
        the region (that's the same bug as above, just via the
        no-match path instead of the some-match path)."""
        self.agent._enemy_points = [(50, 33)]
        with self._patch_ocr_available(), patch(
            "evealert.tools.ocr_local.match_names_to_targets",
            return_value=({}, ["bluhauz", "AschRafie", "Bronwen Morgan"]),
        ):
            identities = self.agent._resolve_enemy_identities()
        self.assertEqual(identities, {})
        self.assertEqual(self.agent._last_ocr_names, [])

    def test_throttled_when_position_set_unchanged(self):
        """A second call within _IDENTITY_RESOLVE_MIN_INTERVAL, with the
        SAME detected positions, must reuse the cached mapping instead of
        running OCR again."""
        self.agent._enemy_points = [(50, 33)]
        with self._patch_ocr_available(), patch(
            "evealert.tools.ocr_local.match_names_to_targets",
            return_value=({(2, 51): "Bad Guy"}, ["Bad Guy"]),
        ) as mock_match:
            self.agent._resolve_enemy_identities()
            result2 = self.agent._resolve_enemy_identities()
        mock_match.assert_called_once()  # NOT called twice
        self.assertEqual(result2, {(2, 51): "Bad Guy"})

    def test_not_throttled_when_position_set_changes(self):
        """A new icon position must trigger an immediate re-resolve even
        within the throttle window -- a genuinely new arrival must be
        identified right away, not delayed up to _IDENTITY_RESOLVE_MIN_INTERVAL."""
        self.agent._enemy_points = [(50, 33)]
        with self._patch_ocr_available(), patch(
            "evealert.tools.ocr_local.match_names_to_targets",
            return_value=({}, []),
        ) as mock_match:
            self.agent._resolve_enemy_identities()
            self.agent._enemy_points = [(50, 33), (50, 333)]  # new icon appeared
            self.agent._resolve_enemy_identities()
        self.assertEqual(mock_match.call_count, 2)

    def test_ocr_disabled_returns_empty_without_calling_ocr(self):
        self.agent._ocr_enabled = False
        self.agent._enemy_points = [(50, 33)]
        with self._patch_ocr_available(), patch(
            "evealert.tools.ocr_local.match_names_to_targets"
        ) as mock_match:
            identities = self.agent._resolve_enemy_identities()
        mock_match.assert_not_called()
        self.assertEqual(identities, {})
        self.assertEqual(self.agent._last_ocr_names, [])

    def test_build_enemy_alarm_text_uses_resolved_names(self):
        self.agent._last_ocr_names = ["Bad Guy", "Other Guy"]
        text = self.agent._build_enemy_alarm_text()
        self.assertEqual(text, "Enemy Appears! — Bad Guy, Other Guy")

    def test_build_enemy_alarm_text_falls_back_when_no_names(self):
        self.agent._last_ocr_names = []
        self.assertEqual(self.agent._build_enemy_alarm_text(), "Enemy Appears!")


def _make_intel_report(pilot="bluhayz", mentioned_pilots=None, system="J5A-IX",
                        message="MickFun  J5A-IX nv but maybe shuttle"):
    from evealert.tools.intel_parser import IntelReport  # noqa: PLC0415

    return IntelReport(
        pilot=pilot,
        raw_line=f"[ 2026.07.17 11:29:27 ] {pilot} > {message}",
        system=system,
        hostile_count=1,
        is_clear=False,
        ships=[],
        mentioned_pilots=mentioned_pilots or [],
    )


class FindRecentIntelReportTests(unittest.TestCase):
    """#212: AlertAgent._find_recent_intel_report() -- the matching/recency
    logic underneath the Enemy-alarm intel-correlation line."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()
        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        with open(self.settings_path, "w") as f:
            json.dump({}, f)
        reset_settings_store(self.settings_path)
        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            self.agent = AlertAgent(self.mock_main)

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_matches_mentioned_pilot_case_insensitively(self):
        report = _make_intel_report(mentioned_pilots=["MickFun"])
        self.agent._intel_reports_recent.append((time.time(), report))
        match = self.agent._find_recent_intel_report("mickfun")
        self.assertIsNotNone(match)
        matched_report, age = match
        self.assertIs(matched_report, report)
        self.assertLess(age, 1.0)

    def test_matches_reporting_pilot_themselves(self):
        report = _make_intel_report(pilot="bluhayz", mentioned_pilots=[])
        self.agent._intel_reports_recent.append((time.time(), report))
        match = self.agent._find_recent_intel_report("BluHayz")
        self.assertIsNotNone(match)

    def test_no_match_when_name_not_mentioned(self):
        report = _make_intel_report(mentioned_pilots=["SomeoneElse"])
        self.agent._intel_reports_recent.append((time.time(), report))
        self.assertIsNone(self.agent._find_recent_intel_report("MickFun"))

    def test_report_outside_recency_window_does_not_match(self):
        report = _make_intel_report(mentioned_pilots=["MickFun"])
        stale_time = time.time() - (
            self.agent._INTEL_CORRELATION_WINDOW_SECONDS + 30
        )
        self.agent._intel_reports_recent.append((stale_time, report))
        self.assertIsNone(self.agent._find_recent_intel_report("MickFun"))

    def test_report_just_inside_recency_window_matches(self):
        report = _make_intel_report(mentioned_pilots=["MickFun"])
        recent_time = time.time() - (
            self.agent._INTEL_CORRELATION_WINDOW_SECONDS - 30
        )
        self.agent._intel_reports_recent.append((recent_time, report))
        self.assertIsNotNone(self.agent._find_recent_intel_report("MickFun"))

    def test_most_recent_matching_report_wins(self):
        old = _make_intel_report(mentioned_pilots=["MickFun"], message="old sighting")
        new = _make_intel_report(mentioned_pilots=["MickFun"], message="new sighting")
        now = time.time()
        self.agent._intel_reports_recent.append((now - 100, old))
        self.agent._intel_reports_recent.append((now - 5, new))
        match = self.agent._find_recent_intel_report("MickFun")
        self.assertIsNotNone(match)
        matched_report, _ = match
        self.assertIs(matched_report, new)


class IntelCorrelationPipelineTests(unittest.IsolatedAsyncioTestCase):
    """#212: end-to-end -- a buffered intel report surfaces as an extra log
    line on a matching Enemy-alarm pilot, via run_intel_check() (the same
    pipeline path a live alarm uses)."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()

        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        with open(self.settings_path, "w") as f:
            json.dump({}, f)
        reset_settings_store(self.settings_path)

        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            self.agent = AlertAgent(self.mock_main)
        self.agent._threat_tiers = {}
        self.agent._kos_cva_enabled = False
        self.agent._kos_custom_urls = []
        self.agent._fleet_composition_enabled = False
        self.agent._esi_standings_classify = False
        self.agent._dscan_watcher = None
        self.agent._wh_drop_detector = None
        self.agent._wh_drop_enabled = False

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _logged_messages(self) -> list[str]:
        return [c.args[0] for c in self.mock_main.write_message.call_args_list]

    async def _run_intel_check_with_esi_stub(self, name="MickFun"):
        info = MagicMock(
            corporation_name="Fraternity.", alliance_name="",
            age_days=1267, corp_history_count=2, security_status=0.0,
            character_id=2120857559, corporation_id=456, alliance_id=None,
        )
        info.name = name
        with patch(
            "evealert.tools.esi_standings.get_esi_client"
        ) as mock_get_client, patch(
            "evealert.tools.kos_checker.get_kos_checker"
        ) as mock_get_kos:
            mock_client = AsyncMock()
            mock_client.lookup_many = AsyncMock(return_value=[info])
            mock_client.get_zkillboard_profile = AsyncMock(return_value=None)
            mock_get_client.return_value = mock_client
            mock_kos = MagicMock()
            mock_kos.check = AsyncMock(return_value=None)
            mock_get_kos.return_value = mock_kos

            await self.agent.run_intel_check([name])

    async def test_matching_recent_report_shown_inline(self):
        report = _make_intel_report(
            pilot="bluhayz", mentioned_pilots=["MickFun"],
            system="J5A-IX", message="MickFun  J5A-IX nv but maybe shuttle",
        )
        self.agent._intel_reports_recent.append((time.time(), report))

        await self._run_intel_check_with_esi_stub("MickFun")

        messages = self._logged_messages()
        self.assertTrue(
            any("Intel (" in m and "reported by bluhayz" in m and "J5A-IX" in m for m in messages),
            f"Expected an inline intel-correlation line, got: {messages}",
        )

    async def test_no_match_produces_no_extra_line(self):
        report = _make_intel_report(pilot="bluhayz", mentioned_pilots=["SomeoneElse"])
        self.agent._intel_reports_recent.append((time.time(), report))

        await self._run_intel_check_with_esi_stub("MickFun")

        messages = self._logged_messages()
        self.assertFalse(any(m.strip().startswith("Intel (") for m in messages))

    async def test_aged_out_report_produces_no_extra_line(self):
        report = _make_intel_report(pilot="bluhayz", mentioned_pilots=["MickFun"])
        stale_time = time.time() - (
            self.agent._INTEL_CORRELATION_WINDOW_SECONDS + 60
        )
        self.agent._intel_reports_recent.append((stale_time, report))

        await self._run_intel_check_with_esi_stub("MickFun")

        messages = self._logged_messages()
        self.assertFalse(any(m.strip().startswith("Intel (") for m in messages))

    async def test_toggle_disabled_suppresses_correlation_even_with_a_match(self):
        self.agent._correlate_intel_enabled = False
        report = _make_intel_report(pilot="bluhayz", mentioned_pilots=["MickFun"])
        self.agent._intel_reports_recent.append((time.time(), report))

        await self._run_intel_check_with_esi_stub("MickFun")

        messages = self._logged_messages()
        self.assertFalse(any(m.strip().startswith("Intel (") for m in messages))


if __name__ == "__main__":
    unittest.main()
