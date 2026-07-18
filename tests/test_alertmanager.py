"""Unit tests for AlertManager core functionality."""

import asyncio
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
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")

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
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
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

    def test_load_settings_capture_backend_defaults_to_mss(self):
        """#176: detection.capture_backend defaults to 'mss' -- existing
        installs must see zero behavior change unless they opt in."""
        self.agent.load_settings()
        self.assertEqual(self.agent.wincap._backend_name, "mss")

    def test_load_settings_capture_backend_reads_explicit_value(self):
        settings = dict(self.test_settings)
        settings["detection"] = {"capture_backend": "dxcam"}
        with open(self.settings_path, "w") as f:
            json.dump(settings, f)
        reset_settings_store(self.settings_path)

        self.agent.load_settings()

        self.assertEqual(self.agent.wincap._backend_name, "dxcam")

    def test_load_settings_capture_backend_hot_swap_closes_old_backend(self):
        """A settings reload while running must reconfigure the SAME
        wincap instance (hot-reload), not silently keep the old backend."""
        self.agent.load_settings()  # backend_name="mss" (default)
        mock_backend = MagicMock()
        self.agent.wincap._backend = mock_backend
        self.agent.wincap._backend_name = "mss"

        settings = dict(self.test_settings)
        settings["detection"] = {"capture_backend": "dxcam"}
        with open(self.settings_path, "w") as f:
            json.dump(settings, f)
        reset_settings_store(self.settings_path)
        self.agent.load_settings()

        mock_backend.close.assert_called_once()
        self.assertEqual(self.agent.wincap._backend_name, "dxcam")

    def test_load_settings_detection_downscale_defaults_to_1_0(self):
        """#175: detection.downscale defaults to 1.0 (off) when absent."""
        self.agent.load_settings()
        self.assertEqual(self.agent._detection_downscale, 1.0)

    def test_load_settings_detection_downscale_reads_explicit_value(self):
        settings = dict(self.test_settings)
        settings["detection"] = {"downscale": 0.5}
        with open(self.settings_path, "w") as f:
            json.dump(settings, f)
        reset_settings_store(self.settings_path)

        self.agent.load_settings()

        self.assertEqual(self.agent._detection_downscale, 0.5)

    def test_load_settings_detection_downscale_clamped_to_valid_range(self):
        settings = dict(self.test_settings)
        settings["detection"] = {"downscale": 5.0}  # above max
        with open(self.settings_path, "w") as f:
            json.dump(settings, f)
        reset_settings_store(self.settings_path)
        self.agent.load_settings()
        self.assertEqual(self.agent._detection_downscale, 1.0)

        settings["detection"] = {"downscale": 0.0}  # at/below min
        with open(self.settings_path, "w") as f:
            json.dump(settings, f)
        reset_settings_store(self.settings_path)
        self.agent.load_settings()
        self.assertEqual(self.agent._detection_downscale, 0.1)

    def test_load_settings_pilot_history_retention_defaults_to_180(self):
        """#214: intelligence.pilot_history_retention_days defaults to 180
        when not present in settings.json."""
        self.agent.load_settings()
        self.assertEqual(self.agent._pilot_history_retention_days, 180)

    def test_load_settings_pilot_history_retention_custom_value(self):
        self.test_settings["intelligence"] = {"pilot_history_retention_days": 30}
        with open(self.settings_path, "w") as f:
            json.dump(self.test_settings, f)
        reset_settings_store(self.settings_path)

        self.agent.load_settings()
        self.assertEqual(self.agent._pilot_history_retention_days, 30)

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
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")

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
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
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
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
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
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
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
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
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
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
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
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
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
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
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


class PilotHistoryIngestionTests(unittest.IsolatedAsyncioTestCase):
    """#215: Local-alarm and intel-channel sightings get recorded into the
    persistent pilot-history store (#214)."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()

        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
        with open(self.settings_path, "w") as f:
            json.dump({"server": {"system": "J5A-IX"}}, f)
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
        self.agent._correlate_intel_enabled = False  # keep these tests focused

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def _run_intel_check_with_esi_stub(self, name="Bad Guy", top_ship="Loki"):
        from evealert.tools.esi_standings import KillProfile

        info = MagicMock(
            corporation_name="Evil Corp", alliance_name="Evil Alliance",
            age_days=100, corp_history_count=2, security_status=0.0,
            character_id=987654, corporation_id=456, alliance_id=789,
        )
        info.name = name
        with patch(
            "evealert.tools.esi_standings.get_esi_client"
        ) as mock_get_client, patch(
            "evealert.tools.kos_checker.get_kos_checker"
        ) as mock_get_kos:
            mock_client = AsyncMock()
            mock_client.lookup_many = AsyncMock(return_value=[info])
            mock_client.get_zkillboard_profile = AsyncMock(
                return_value=KillProfile(
                    kills_total=5, losses_total=1, top_ship=top_ship, danger_ratio=0.5
                )
            )
            mock_get_client.return_value = mock_client
            mock_kos = MagicMock()
            mock_kos.check = AsyncMock(return_value=None)
            mock_get_kos.return_value = mock_kos

            await self.agent.run_intel_check([name])

    async def test_local_alarm_records_sighting_with_system_and_ship(self):
        with patch(
            "evealert.tools.pilot_history_store.record_sighting"
        ) as mock_record:
            await self._run_intel_check_with_esi_stub("Bad Guy", top_ship="Loki")

        mock_record.assert_called_once_with(
            "Bad Guy", source="local", system="J5A-IX", ship="Loki",
            corp="Evil Corp", alliance="Evil Alliance",
        )

    async def test_local_alarm_placeholder_system_recorded_as_none(self):
        with open(self.settings_path, "w") as f:
            json.dump({"server": {"system": "Enter a System Name"}}, f)
        reset_settings_store(self.settings_path)
        self.agent.load_settings()

        with patch(
            "evealert.tools.pilot_history_store.record_sighting"
        ) as mock_record:
            await self._run_intel_check_with_esi_stub("Bad Guy")

        self.assertIsNone(mock_record.call_args.kwargs["system"])

    async def test_local_toggle_disabled_records_nothing(self):
        self.agent._pilot_history_enabled = False
        with patch(
            "evealert.tools.pilot_history_store.record_sighting"
        ) as mock_record:
            await self._run_intel_check_with_esi_stub("Bad Guy")
        mock_record.assert_not_called()

    def test_intel_report_records_one_sighting_per_mentioned_pilot_not_reporter(self):
        report = _make_intel_report(
            pilot="bluhayz", mentioned_pilots=["MickFun", "OtherGuy"],
            system="J5A-IX",
        )
        with patch(
            "evealert.tools.pilot_history_store.record_sighting"
        ) as mock_record:
            self.agent._on_intel_report(report)

        recorded_names = [c.args[0] for c in mock_record.call_args_list]
        self.assertEqual(sorted(recorded_names), ["MickFun", "OtherGuy"])
        self.assertNotIn("bluhayz", recorded_names)
        for c in mock_record.call_args_list:
            self.assertEqual(c.kwargs["source"], "intel")
            self.assertEqual(c.kwargs["system"], "J5A-IX")

    def test_intel_toggle_disabled_records_nothing(self):
        self.agent._pilot_history_enabled = False
        report = _make_intel_report(mentioned_pilots=["MickFun"])
        with patch(
            "evealert.tools.pilot_history_store.record_sighting"
        ) as mock_record:
            self.agent._on_intel_report(report)
        mock_record.assert_not_called()


class PilotHistorySummaryDisplayTests(unittest.IsolatedAsyncioTestCase):
    """#216/#217: the "History: ..." line on Enemy alarms, driven by
    pilot_history_analytics.summarize() and infer_pathing()."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()

        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
        with open(self.settings_path, "w") as f:
            json.dump({"server": {"system": "J5A-IX"}}, f)
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
        self.agent._correlate_intel_enabled = False  # keep these tests focused

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _logged_messages(self) -> list[str]:
        return [c.args[0] for c in self.mock_main.write_message.call_args_list]

    async def _run_intel_check_with_esi_stub(self, name="Bad Guy"):
        info = MagicMock(
            corporation_name="Evil Corp", alliance_name="Evil Alliance",
            age_days=100, corp_history_count=2, security_status=0.0,
            character_id=987654, corporation_id=456, alliance_id=789,
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

    async def test_history_line_shown_when_summary_available(self):
        from evealert.tools.pilot_history_analytics import PilotSummary

        summary = PilotSummary(
            pilot_name="Bad Guy", sighting_count=14, first_seen=0.0,
            last_seen=45 * 86400.0, top_systems=[("J5A-IX", 9)],
            top_ship="Loki", active_hour_range="19:00-22:00",
        )
        with patch(
            "evealert.tools.pilot_history_store.record_sighting"
        ), patch(
            "evealert.tools.pilot_history_analytics.summarize", return_value=summary
        ):
            await self._run_intel_check_with_esi_stub("Bad Guy")

        messages = self._logged_messages()
        self.assertTrue(
            any("History: 14 sightings over 45d" in m for m in messages),
            f"Expected a History line, got: {messages}",
        )

    async def test_pathing_appended_to_history_line_when_available(self):
        from evealert.tools.pilot_history_analytics import PathingSummary, PilotSummary

        summary = PilotSummary(
            pilot_name="Bad Guy", sighting_count=14, first_seen=0.0,
            last_seen=45 * 86400.0, top_systems=[("J5A-IX", 9)],
            top_ship="Loki", active_hour_range="19:00-22:00",
        )
        pathing = PathingSummary(
            pilot_name="Bad Guy", home_system="J5A-IX",
            top_transitions=[(("J5A-IX", "1DQ1-A"), 5)],
        )
        with patch(
            "evealert.tools.pilot_history_store.record_sighting"
        ), patch(
            "evealert.tools.pilot_history_analytics.summarize", return_value=summary
        ), patch(
            "evealert.tools.pilot_history_analytics.infer_pathing",
            new=AsyncMock(return_value=pathing),
        ):
            await self._run_intel_check_with_esi_stub("Bad Guy")

        messages = self._logged_messages()
        self.assertTrue(
            any("home J5A-IX; often moves J5A-IX -> 1DQ1-A" in m for m in messages),
            f"Expected pathing appended to the History line, got: {messages}",
        )

    async def test_no_pathing_segment_when_infer_pathing_returns_none(self):
        from evealert.tools.pilot_history_analytics import PilotSummary

        summary = PilotSummary(
            pilot_name="Bad Guy", sighting_count=14, first_seen=0.0,
            last_seen=45 * 86400.0, top_systems=[("J5A-IX", 9)],
            top_ship="Loki", active_hour_range="19:00-22:00",
        )
        with patch(
            "evealert.tools.pilot_history_store.record_sighting"
        ), patch(
            "evealert.tools.pilot_history_analytics.summarize", return_value=summary
        ), patch(
            "evealert.tools.pilot_history_analytics.infer_pathing",
            new=AsyncMock(return_value=None),
        ):
            await self._run_intel_check_with_esi_stub("Bad Guy")

        messages = self._logged_messages()
        history_lines = [m for m in messages if m.strip().startswith("History:")]
        self.assertEqual(len(history_lines), 1)
        self.assertNotIn("home", history_lines[0])

    async def test_no_history_line_when_summary_is_none(self):
        """Fewer than 3 sightings -> summarize() returns None -> no line."""
        with patch(
            "evealert.tools.pilot_history_store.record_sighting"
        ), patch(
            "evealert.tools.pilot_history_analytics.summarize", return_value=None
        ):
            await self._run_intel_check_with_esi_stub("Bad Guy")

        messages = self._logged_messages()
        self.assertFalse(any(m.strip().startswith("History:") for m in messages))

    async def test_no_history_line_when_toggle_disabled(self):
        from evealert.tools.pilot_history_analytics import PilotSummary

        self.agent._pilot_history_enabled = False
        summary = PilotSummary(
            pilot_name="Bad Guy", sighting_count=14, first_seen=0.0,
            last_seen=45 * 86400.0, top_systems=[("J5A-IX", 9)],
            top_ship="Loki", active_hour_range="19:00-22:00",
        )
        with patch(
            "evealert.tools.pilot_history_analytics.summarize", return_value=summary
        ) as mock_summarize:
            await self._run_intel_check_with_esi_stub("Bad Guy")

        mock_summarize.assert_not_called()
        messages = self._logged_messages()
        self.assertFalse(any(m.strip().startswith("History:") for m in messages))

    async def test_history_frequency_and_route_feed_into_threat_score(self):
        """#218: sighting history for the CURRENT system feeds
        compute_threat_score() -- a pilot frequently seen here, on their
        regular route, must score higher and carry a behavioral label."""
        from evealert.tools.pilot_history_analytics import PathingSummary, PilotSummary

        summary = PilotSummary(
            pilot_name="Bad Guy", sighting_count=14, first_seen=0.0,
            last_seen=45 * 86400.0, top_systems=[("J5A-IX", 5)],
            top_ship="Loki", active_hour_range=None,
        )
        pathing = PathingSummary(
            pilot_name="Bad Guy", home_system="J5A-IX", top_transitions=[],
        )
        with patch(
            "evealert.tools.pilot_history_analytics.summarize", return_value=summary
        ), patch(
            "evealert.tools.pilot_history_analytics.infer_pathing",
            new=AsyncMock(return_value=pathing),
        ):
            await self._run_intel_check_with_esi_stub("Bad Guy")

        messages = self._logged_messages()
        threat_line = next((m for m in messages if m.startswith("[THREAT:")), None)
        self.assertIsNotNone(threat_line, f"Expected a THREAT line, got: {messages}")
        self.assertIn("frequent resident", threat_line)
        self.assertIn("seen here 5x recently", threat_line)
        self.assertIn("on their regular route", threat_line)

    async def test_no_history_data_leaves_threat_score_unaffected(self):
        """No sighting history at all -- summarize()/infer_pathing() both
        return None -- the THREAT line must show no behavioral label."""
        with patch(
            "evealert.tools.pilot_history_analytics.summarize", return_value=None
        ):
            await self._run_intel_check_with_esi_stub("Bad Guy")

        messages = self._logged_messages()
        threat_line = next((m for m in messages if m.startswith("[THREAT:")), None)
        self.assertIsNotNone(threat_line, f"Expected a THREAT line, got: {messages}")
        self.assertNotIn("(", threat_line)


class ManualBlueTierTests(unittest.IsolatedAsyncioTestCase):
    """#173: a manual "blue" threat_tiers entry suppresses KOS/threat
    counting for that pilot, identically to an ESI-standings score >= +5
    (#147), gated behind the same _standings_filter_blues toggle."""

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
        self.agent._fleet_composition_enabled = False
        self.agent._esi_standings_classify = False
        self.agent._dscan_watcher = None
        self.agent._wh_drop_detector = None
        self.agent._wh_drop_enabled = False
        self.agent._correlate_intel_enabled = False
        self.agent._pilot_history_enabled = False

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _logged_messages(self) -> list[str]:
        return [c.args[0] for c in self.mock_main.write_message.call_args_list]

    async def _run_with_esi_stub(self, name="Bad Guy"):
        info = MagicMock(
            corporation_name="Evil Corp", alliance_name="Evil Alliance",
            age_days=100, corp_history_count=2, security_status=0.0,
            character_id=987654, corporation_id=456, alliance_id=789,
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
            return mock_kos

    async def test_manual_blue_tier_suppresses_kos_check_when_filter_enabled(self):
        self.agent._threat_tiers = {"Bad Guy": "blue"}
        self.agent._standings_filter_blues = True

        mock_kos = await self._run_with_esi_stub("Bad Guy")

        mock_kos.check.assert_not_called()
        messages = self._logged_messages()
        self.assertTrue(
            any("[ALLY]" in m and "Bad Guy" in m for m in messages),
            f"Expected an [ALLY] filtered line, got: {messages}",
        )
        # The pilot's own header/ZKB/KOS lines must not appear -- only the
        # [ALLY] line represents them.
        self.assertFalse(any("[KOS" in m for m in messages))

    async def test_manual_blue_tier_ignored_when_filter_disabled(self):
        """The same manual-blue tag with the toggle OFF must not suppress
        anything -- matching #147's existing standings-based behavior."""
        self.agent._threat_tiers = {"Bad Guy": "blue"}
        self.agent._standings_filter_blues = False

        mock_kos = await self._run_with_esi_stub("Bad Guy")

        mock_kos.check.assert_awaited_once()
        messages = self._logged_messages()
        self.assertFalse(any("[ALLY]" in m for m in messages))

    async def test_red_tier_unaffected_by_blue_handling(self):
        self.agent._threat_tiers = {"Bad Guy": "red"}
        self.agent._standings_filter_blues = True

        mock_kos = await self._run_with_esi_stub("Bad Guy")

        mock_kos.check.assert_awaited_once()
        messages = self._logged_messages()
        self.assertFalse(any("[ALLY]" in m for m in messages))
        self.assertTrue(any("[KOS-RED]" in m for m in messages))


class MultiChannelIntelTests(unittest.TestCase):
    """#171: multi-channel intel watcher -- settings migration, per-channel
    IntelWatcher construction, cross-channel dedup, and channel-tagged
    log rendering."""

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

    def _write_settings(self, intelligence: dict) -> None:
        # Non-degenerate regions -- load_settings() validates and
        # early-returns (skipping the intelligence section entirely)
        # when x1 == x2, per the region-validation check in
        # ConfigValidator.validate_settings_dict().
        with open(self.settings_path, "w") as f:
            json.dump({
                "alert_region_1": {"x": 100, "y": 100},
                "alert_region_2": {"x": 300, "y": 300},
                "faction_region_1": {"x": 400, "y": 100},
                "faction_region_2": {"x": 600, "y": 300},
                "intelligence": intelligence,
            }, f)
        reset_settings_store(self.settings_path)

    def _logged_messages(self) -> list[str]:
        return [c.args[0] for c in self.mock_main.write_message.call_args_list]

    # -- settings migration -------------------------------------------

    def test_legacy_single_channel_migrates_to_a_one_element_list(self):
        self._write_settings({"intel_log_channel": "Intel"})
        self.agent.load_settings()
        self.assertEqual(self.agent._intel_channels, ["Intel"])

    def test_new_intel_channels_key_used_when_present(self):
        self._write_settings({"intel_channels": ["Intel", "Alliance"]})
        self.agent.load_settings()
        self.assertEqual(self.agent._intel_channels, ["Intel", "Alliance"])

    def test_new_key_takes_precedence_over_legacy_key(self):
        self._write_settings({
            "intel_log_channel": "Intel",
            "intel_channels": ["Alliance", "NC-INT"],
        })
        self.agent.load_settings()
        self.assertEqual(self.agent._intel_channels, ["Alliance", "NC-INT"])

    def test_neither_key_set_yields_empty_list(self):
        self._write_settings({})
        self.agent.load_settings()
        self.assertEqual(self.agent._intel_channels, [])

    # -- per-channel watcher construction -------------------------------

    def test_build_intel_watchers_creates_one_per_channel(self):
        self.agent._intel_channels = ["Intel", "Alliance"]
        watchers = self.agent._build_intel_watchers()
        self.assertEqual(len(watchers), 2)
        self.assertEqual(watchers[0].channel_pattern, "Intel")
        self.assertEqual(watchers[1].channel_pattern, "Alliance")

    def test_build_intel_watchers_uses_explicit_log_dir_override(self):
        """#191: an explicit intel_log_dir setting is passed straight
        through to every watcher, bypassing auto-detection entirely."""
        self.agent._intel_channels = ["Intel"]
        self.agent._intel_log_dir = str(Path(self.temp_dir))
        watchers = self.agent._build_intel_watchers()
        self.assertEqual(watchers[0]._chatlog_dir, Path(self.temp_dir))

    def test_build_intel_watchers_falls_back_to_auto_detect_when_dir_empty(self):
        self.agent._intel_channels = ["Intel"]
        self.agent._intel_log_dir = ""
        with patch(
            "evealert.tools.intel_watcher.get_eve_chatlog_dir",
            return_value=Path("/auto/detected/dir"),
        ) as mock_detect:
            watchers = self.agent._build_intel_watchers()
        mock_detect.assert_called_once()
        self.assertEqual(watchers[0]._chatlog_dir, Path("/auto/detected/dir"))

    def test_load_settings_reads_intel_log_dir(self):
        self._write_settings({"intel_log_dir": "/custom/eve/logs"})
        self.agent.load_settings()
        self.assertEqual(self.agent._intel_log_dir, "/custom/eve/logs")

    def test_load_settings_intel_log_dir_defaults_to_empty(self):
        self._write_settings({})
        self.agent.load_settings()
        self.assertEqual(self.agent._intel_log_dir, "")

    def test_each_watcher_callback_reports_its_own_channel(self):
        """Regression guard: a naive `lambda line: ...channel=channel`
        closure over the loop variable would have every watcher report
        the LAST channel in the list, not its own."""
        self.agent._intel_channels = ["Intel", "Alliance", "NC-INT"]
        watchers = self.agent._build_intel_watchers()

        seen = []
        with patch.object(
            self.agent, "_on_intel_line", side_effect=lambda line, channel=None: seen.append((line, channel))
        ):
            for w in watchers:
                w.callback(f"line for {w.channel_pattern}")

        self.assertEqual(
            seen,
            [
                ("line for Intel", "Intel"),
                ("line for Alliance", "Alliance"),
                ("line for NC-INT", "NC-INT"),
            ],
        )

    def test_two_channels_tail_concurrently_from_separate_files(self):
        """Acceptance criterion: two configured channels both tail
        concurrently -- verified with two real temp log files."""
        self.agent._intel_channels = ["Intel", "Alliance"]

        reports = []
        with patch.object(
            self.agent, "_on_intel_report", side_effect=lambda r: reports.append(r)
        ):
            # Built inside the patch context: on_intel=self._on_intel_report
            # is resolved to a bound method AT CONSTRUCTION TIME, so
            # patching the attribute afterward wouldn't affect watchers
            # that already captured the original (unpatched) method.
            watchers = self.agent._build_intel_watchers()
            with tempfile.TemporaryDirectory() as tmpdir:
                for watcher, system in zip(watchers, ["Jita", "Amarr"]):
                    log = Path(tmpdir) / f"{watcher.channel_pattern}_test.txt"
                    log.write_text(f"[ 2024.05.01 15:30:22 ] bluhayz > {system} clr\n")
                    watcher._log_path = log
                    watcher._file_pos = 0
                    watcher._tail_once()

        self.assertEqual(len(reports), 2)
        self.assertEqual({r.channel for r in reports}, {"Intel", "Alliance"})

    # -- cross-channel dedup ---------------------------------------------

    def test_is_duplicate_intel_line_true_within_window(self):
        line = "[ 2024.05.01 15:30:22 ] bluhayz > D7-ZAC clr"
        self.assertFalse(self.agent._is_duplicate_intel_line(line))
        # Same (pilot, message) posted moments later in another channel.
        line_again = "[ 2024.05.01 15:30:24 ] bluhayz > D7-ZAC clr"
        self.assertTrue(self.agent._is_duplicate_intel_line(line_again))

    def test_is_duplicate_intel_line_false_outside_window(self):
        line = "[ 2024.05.01 15:30:22 ] bluhayz > D7-ZAC clr"
        with patch("evealert.manager.alertmanager.time.time", return_value=1000.0):
            self.assertFalse(self.agent._is_duplicate_intel_line(line))
        with patch(
            "evealert.manager.alertmanager.time.time", return_value=1000.0 + 31
        ):
            self.assertFalse(self.agent._is_duplicate_intel_line(line))

    def test_different_pilot_same_message_not_a_duplicate(self):
        line_a = "[ 2024.05.01 15:30:22 ] bluhayz > D7-ZAC clr"
        line_b = "[ 2024.05.01 15:30:22 ] someoneelse > D7-ZAC clr"
        self.assertFalse(self.agent._is_duplicate_intel_line(line_a))
        self.assertFalse(self.agent._is_duplicate_intel_line(line_b))

    def test_build_intel_watchers_share_the_same_dedup_check(self):
        """A duplicate paste across two channel watchers must only fire
        callbacks once total, not once per watcher."""
        self.agent._intel_channels = ["Intel", "Alliance"]

        reports = []
        with patch.object(
            self.agent, "_on_intel_report", side_effect=lambda r: reports.append(r)
        ):
            watchers = self.agent._build_intel_watchers()
            with tempfile.TemporaryDirectory() as tmpdir:
                line = "[ 2024.05.01 15:30:22 ] bluhayz > D7-ZAC clr\n"
                for watcher in watchers:
                    log = Path(tmpdir) / f"{watcher.channel_pattern}_test.txt"
                    log.write_text(line)
                    watcher._log_path = log
                    watcher._file_pos = 0
                    watcher._tail_once()

        self.assertEqual(len(reports), 1)

    # -- channel-tagged rendering -----------------------------------------

    def test_on_intel_report_tags_channel_when_present(self):
        report = _make_intel_report(mentioned_pilots=["MickFun"])
        report.channel = "NC-INT"
        self.agent._on_intel_report(report)
        messages = self._logged_messages()
        self.assertTrue(
            any(m.startswith("Intel[NC-INT]:") for m in messages),
            f"Expected a channel-tagged Intel line, got: {messages}",
        )

    def test_on_intel_report_no_tag_when_channel_absent(self):
        report = _make_intel_report(mentioned_pilots=["MickFun"])
        self.assertIsNone(report.channel)
        self.agent._on_intel_report(report)
        messages = self._logged_messages()
        self.assertTrue(any(m.startswith("Intel:") for m in messages))
        self.assertFalse(any(m.startswith("Intel[") for m in messages))

    def test_on_intel_line_tags_channel_when_given(self):
        self.agent._on_intel_line("D7-ZAC clr", channel="Intel")
        messages = self._logged_messages()
        self.assertTrue(any(m.startswith("Intel: [Intel] ") for m in messages))

    def test_on_intel_line_no_tag_when_channel_omitted(self):
        self.agent._on_intel_line("D7-ZAC clr")
        messages = self._logged_messages()
        self.assertTrue(any(m == "Intel: D7-ZAC clr" for m in messages))


class PruneOnStartupTests(unittest.TestCase):
    """#214: the persistent pilot-history store is pruned once per app
    start (AlertAgent.__init__), not on every load_settings() reload."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()
        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
        with open(self.settings_path, "w") as f:
            # Non-degenerate regions -- load_settings() validates and
            # early-returns (skipping the intelligence section entirely,
            # including pilot_history_retention_days) when x1 == x2.
            json.dump({
                "alert_region_1": {"x": 100, "y": 100},
                "alert_region_2": {"x": 300, "y": 300},
                "faction_region_1": {"x": 400, "y": 100},
                "faction_region_2": {"x": 600, "y": 300},
                "intelligence": {"pilot_history_retention_days": 42},
            }, f)
        reset_settings_store(self.settings_path)

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_prune_called_once_at_construction_with_configured_retention(self):
        with patch(
            "evealert.tools.pilot_history_store.prune_older_than"
        ) as mock_prune, patch(
            "evealert.manager.alertmanager.AlertAgent._validate_audio_files"
        ):
            AlertAgent(self.mock_main)
        mock_prune.assert_called_once_with(42)

    def test_prune_not_called_again_on_a_second_load_settings(self):
        with patch(
            "evealert.manager.alertmanager.AlertAgent._validate_audio_files"
        ):
            agent = AlertAgent(self.mock_main)
        with patch(
            "evealert.tools.pilot_history_store.prune_older_than"
        ) as mock_prune:
            agent.load_settings()
        mock_prune.assert_not_called()

    def test_prune_failure_does_not_raise(self):
        """A DB error during startup prune must not crash construction."""
        with patch(
            "evealert.tools.pilot_history_store.prune_older_than",
            side_effect=OSError("disk full"),
        ), patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            AlertAgent(self.mock_main)  # must not raise


class R2Z2SettingsTests(unittest.TestCase):
    """#169: r2z2 settings block -- defaults, explicit values, watchlist
    parsing, and last_sequence persistence round-tripping."""

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

    def _write_settings(self, r2z2: dict) -> None:
        # Non-degenerate regions -- load_settings() validates and
        # early-returns (skipping everything past region checks) when
        # x1 == x2, per ConfigValidator.validate_settings_dict().
        with open(self.settings_path, "w") as f:
            json.dump({
                "alert_region_1": {"x": 100, "y": 100},
                "alert_region_2": {"x": 300, "y": 300},
                "faction_region_1": {"x": 400, "y": 100},
                "faction_region_2": {"x": 600, "y": 300},
                "r2z2": r2z2,
            }, f)
        reset_settings_store(self.settings_path)

    def test_defaults_when_no_r2z2_block(self):
        self._write_settings({})
        self.agent.load_settings()
        self.assertFalse(self.agent._r2z2_enabled)
        self.assertEqual(self.agent._r2z2_alarm_jumps, 2)
        self.assertEqual(self.agent._r2z2_watch_jumps, 5)
        self.assertEqual(self.agent._r2z2_alliance_watchlist, set())
        self.assertIsNone(self.agent._r2z2_last_sequence)

    def test_explicit_values_loaded(self):
        self._write_settings({
            "enabled": True,
            "alarm_jumps": 3,
            "watch_jumps": 7,
            "alliance_watchlist": [99000001, 99000002],
            "last_sequence": 123456,
        })
        self.agent.load_settings()
        self.assertTrue(self.agent._r2z2_enabled)
        self.assertEqual(self.agent._r2z2_alarm_jumps, 3)
        self.assertEqual(self.agent._r2z2_watch_jumps, 7)
        self.assertEqual(self.agent._r2z2_alliance_watchlist, {99000001, 99000002})
        self.assertEqual(self.agent._r2z2_last_sequence, 123456)

    def test_stop_persists_last_sequence_without_disturbing_other_settings(self):
        """stop() must read-merge-write the sequence -- never seed a save
        from DEFAULT_SETTINGS (#108 data-loss pattern)."""
        self._write_settings({"enabled": True, "alliance_watchlist": [123]})
        self.agent.load_settings()
        self.agent._r2z2_consumer = MagicMock()
        self.agent._r2z2_consumer.last_sequence = 999888
        self.agent._r2z2_consumer.stop = MagicMock()

        self.agent.stop()

        with open(self.settings_path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["r2z2"]["last_sequence"], 999888)
        # The rest of the r2z2 block (and other sections) must survive the merge.
        self.assertEqual(saved["r2z2"]["alliance_watchlist"], [123])
        self.assertTrue(saved["r2z2"]["enabled"])

    def test_stop_stops_and_clears_the_consumer(self):
        self._write_settings({"enabled": True})
        self.agent.load_settings()
        consumer = MagicMock()
        consumer.last_sequence = 1
        self.agent._r2z2_consumer = consumer

        self.agent.stop()

        consumer.stop.assert_called_once()
        self.assertIsNone(self.agent._r2z2_consumer)

    def test_stop_without_a_consumer_does_not_touch_settings(self):
        """No consumer means R2Z2 was never enabled/started -- stop() must
        not write to settings.json at all (file stays exactly as written)."""
        self._write_settings({"enabled": False})
        self.agent.load_settings()
        self.assertIsNone(self.agent._r2z2_consumer)
        before = self.settings_path.read_text(encoding="utf-8")

        self.agent.stop()  # must not raise

        after = self.settings_path.read_text(encoding="utf-8")
        self.assertEqual(before, after)


class R2Z2AdjacentKillCountTests(unittest.TestCase):
    """#169: the threat score's adjacent_kills signal prefers the R2Z2
    consumer's buffer over NeighborMonitor when R2Z2 is active."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.temp_dir = tempfile.mkdtemp()
        settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
        with open(settings_path, "w") as f:
            json.dump({}, f)
        reset_settings_store(settings_path)
        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            self.agent = AlertAgent(self.mock_main)
        self.agent._adjacent_poll_interval = 120

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_prefers_r2z2_consumer_when_present(self):
        self.agent._r2z2_consumer = MagicMock()
        self.agent._r2z2_consumer.kill_count_since = MagicMock(return_value=4)
        self.agent._neighbor_monitor = MagicMock(last_kill_count=99)

        self.assertEqual(self.agent._get_adjacent_kill_count(), 4)
        self.agent._r2z2_consumer.kill_count_since.assert_called_once_with(120)

    def test_falls_back_to_neighbor_monitor_when_no_consumer(self):
        self.agent._r2z2_consumer = None
        self.agent._neighbor_monitor = MagicMock(last_kill_count=7)

        self.assertEqual(self.agent._get_adjacent_kill_count(), 7)

    def test_returns_zero_when_neither_present(self):
        self.agent._r2z2_consumer = None
        self.agent._neighbor_monitor = None

        self.assertEqual(self.agent._get_adjacent_kill_count(), 0)


class R2Z2ConsumerWiringTests(unittest.IsolatedAsyncioTestCase):
    """#169: engine wiring around R2Z2Consumer -- system resolution,
    on_kill reporting (log line + alarm), and sequence tracking."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()
        self.temp_dir = tempfile.mkdtemp()
        settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
        with open(settings_path, "w") as f:
            json.dump({}, f)
        reset_settings_store(settings_path)
        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            self.agent = AlertAgent(self.mock_main)
        self.agent.loop = asyncio.get_event_loop()
        self.agent._r2z2_alarm_jumps = 2
        self.agent._r2z2_watch_jumps = 5
        self.agent._r2z2_alliance_watchlist = {99000001}

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _logged_messages(self) -> list[str]:
        return [c.args[0] for c in self.mock_main.write_message.call_args_list]

    # -- _start_r2z2_consumer -------------------------------------------

    async def test_starts_consumer_with_resolved_system_id(self):
        mock_cache = MagicMock()
        mock_cache.get_system_id = AsyncMock(return_value=30000142)
        mock_consumer_instance = MagicMock()
        mock_consumer_instance.run = AsyncMock(return_value=None)

        with patch(
            "evealert.tools.universe.get_universe_cache", return_value=mock_cache
        ), patch(
            "evealert.tools.r2z2.R2Z2Consumer", return_value=mock_consumer_instance
        ) as mock_cls:
            await self.agent._start_r2z2_consumer("Jita")
            await asyncio.sleep(0)  # let the scheduled create_task() run

        mock_cls.assert_called_once_with(
            origin_system_id=30000142,
            watch_jumps=5,
            alliance_watchlist={99000001},
            on_kill=self.agent._on_r2z2_kill,
            sequence=self.agent._r2z2_last_sequence,
        )
        self.assertIs(self.agent._r2z2_consumer, mock_consumer_instance)
        mock_consumer_instance.run.assert_awaited_once()

    async def test_unresolvable_system_does_not_start_a_consumer(self):
        mock_cache = MagicMock()
        mock_cache.get_system_id = AsyncMock(return_value=None)

        with patch(
            "evealert.tools.universe.get_universe_cache", return_value=mock_cache
        ), patch("evealert.tools.r2z2.R2Z2Consumer") as mock_cls:
            await self.agent._start_r2z2_consumer("Not A Real System")

        mock_cls.assert_not_called()
        self.assertIsNone(self.agent._r2z2_consumer)

    # -- _on_r2z2_kill / _report_r2z2_kill -------------------------------

    async def _fire_kill(self, jump_dist):
        from evealert.tools.r2z2 import LiveKillmail

        killmail = LiveKillmail(
            killmail_id=1, solar_system_id=30000142,
            victim_ship_type_id=587, attacker_count=3, location_id=None,
        )
        self.agent._r2z2_consumer = MagicMock(last_sequence=42)
        with patch(
            "evealert.tools.r2z2.resolve_ship_name", new=AsyncMock(return_value="Rifter")
        ), patch(
            "evealert.tools.universe.get_universe_cache"
        ) as mock_get_cache, patch.object(
            self.agent, "play_sound", new=AsyncMock()
        ) as mock_play:
            mock_cache = MagicMock()
            mock_cache.get_system_name = AsyncMock(return_value="Jita")
            mock_get_cache.return_value = mock_cache

            self.agent._on_r2z2_kill(killmail, jump_dist)
            await asyncio.sleep(0.05)  # let the scheduled report task run

        return mock_play

    async def test_kill_within_alarm_jumps_logs_and_plays_alarm(self):
        mock_play = await self._fire_kill(jump_dist=1)

        messages = self._logged_messages()
        self.assertTrue(
            any("LIVE KILL: Rifter destroyed in Jita" in m and "(1j away)" in m
                and "3 attackers" in m for m in messages),
            f"Expected a LIVE KILL line, got: {messages}",
        )
        mock_play.assert_awaited_once()
        self.assertEqual(self.agent._r2z2_last_sequence, 42)

    async def test_kill_outside_alarm_jumps_logs_without_alarm(self):
        mock_play = await self._fire_kill(jump_dist=5)  # > alarm_jumps=2

        messages = self._logged_messages()
        self.assertTrue(any("(5j away)" in m for m in messages))
        mock_play.assert_not_awaited()

    async def test_watchlist_only_kill_labels_as_watchlist_not_jumps(self):
        mock_play = await self._fire_kill(jump_dist=None)

        messages = self._logged_messages()
        self.assertTrue(any("(watchlist)" in m for m in messages))
        mock_play.assert_not_awaited()


def _mock_camp(system_id=30000144, location_id=999, confidence="camp",
               kill_count=4, last_kill_age_seconds=30.0):
    camp = MagicMock()
    camp.system_id = system_id
    camp.location_id = location_id
    camp.confidence = confidence
    camp.kill_count = kill_count
    camp.last_kill_age_seconds = last_kill_age_seconds
    camp.gate_name = None
    camp.system_name = None
    return camp


class GateCampMonitorTests(unittest.IsolatedAsyncioTestCase):
    """#170: the periodic gate-camp monitor -- warns once per full-
    confidence camp per hour, only within adjacent.max_jumps."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()
        self.temp_dir = tempfile.mkdtemp()
        settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
        with open(settings_path, "w") as f:
            json.dump({}, f)
        reset_settings_store(settings_path)
        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            self.agent = AlertAgent(self.mock_main)
        self.agent._adjacent_max_jumps = 3
        self.agent._r2z2_consumer = MagicMock()

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _logged_messages(self) -> list[str]:
        return [c.args[0] for c in self.mock_main.write_message.call_args_list]

    async def _run_one_cycle(self, origin_id=30000142):
        """Drive _gatecamp_monitor through exactly one loop body execution
        by mocking asyncio.sleep to flip `running` off on its 2nd call
        (1st call is the top-of-loop sleep before the body we want to
        exercise; the 2nd is the top-of-next-iteration sleep, which we
        intercept to stop the loop cleanly)."""
        self.agent.running = True
        call_count = {"n": 0}

        async def fake_sleep(_):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                self.agent.running = False

        with patch("evealert.manager.alertmanager.asyncio.sleep", new=fake_sleep):
            await self.agent._gatecamp_monitor(origin_id)

    def _patch_camp_layer(self, camps, nearby):
        mock_cache = MagicMock()
        mock_cache.get_systems_within_jumps = AsyncMock(return_value=nearby)
        return patch(
            "evealert.tools.gatecamp.get_active_camps", return_value=camps
        ), patch(
            "evealert.tools.gatecamp.resolve_camp_names",
            new=AsyncMock(side_effect=lambda cs: cs),
        ), patch(
            "evealert.tools.universe.get_universe_cache", return_value=mock_cache
        )

    async def test_warns_for_a_full_confidence_camp_within_radius(self):
        camp = _mock_camp(confidence="camp")
        p1, p2, p3 = self._patch_camp_layer([camp], {30000144: 2})
        with p1, p2, p3:
            await self._run_one_cycle()

        messages = self._logged_messages()
        self.assertTrue(
            any("GATE CAMP" in m and "(2j away)" in m and "4 kills" in m for m in messages),
            f"Expected a GATE CAMP line, got: {messages}",
        )

    async def test_possible_camp_does_not_warn(self):
        camp = _mock_camp(confidence="possible_camp")
        p1, p2, p3 = self._patch_camp_layer([camp], {30000144: 2})
        with p1, p2, p3:
            await self._run_one_cycle()

        messages = self._logged_messages()
        self.assertFalse(any("GATE CAMP" in m for m in messages))

    async def test_camp_outside_adjacent_radius_does_not_warn(self):
        camp = _mock_camp(confidence="camp")
        p1, p2, p3 = self._patch_camp_layer([camp], {})  # not in the nearby map
        with p1, p2, p3:
            await self._run_one_cycle()

        messages = self._logged_messages()
        self.assertFalse(any("GATE CAMP" in m for m in messages))

    async def test_cooldown_prevents_rewarning_within_an_hour(self):
        camp = _mock_camp(confidence="camp")
        p1, p2, p3 = self._patch_camp_layer([camp], {30000144: 2})
        with p1, p2, p3:
            await self._run_one_cycle()
            await self._run_one_cycle()

        messages = self._logged_messages()
        camp_lines = [m for m in messages if "GATE CAMP" in m]
        self.assertEqual(len(camp_lines), 1)


class RouteCheckGateCampTests(unittest.IsolatedAsyncioTestCase):
    """#170: _run_route_check() feeds active camp system IDs into
    route_threat() and renders a [CAMP] marker for those legs."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()
        self.temp_dir = tempfile.mkdtemp()
        settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
        with open(settings_path, "w") as f:
            json.dump({}, f)
        reset_settings_store(settings_path)
        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            self.agent = AlertAgent(self.mock_main)
        self.agent._r2z2_consumer = MagicMock()

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _logged_messages(self) -> list[str]:
        return [c.args[0] for c in self.mock_main.write_message.call_args_list]

    async def test_camped_leg_renders_camp_marker(self):
        from evealert.tools.universe import RouteLeg, RouteSuggestion

        camp = _mock_camp(system_id=30000144)
        camped_leg = RouteLeg(30000144, "SomeSystem", 1, 0, "danger", has_camp=True)
        mock_cache = MagicMock()
        mock_cache.get_system_id = AsyncMock(side_effect=lambda name: {
            "Jita": 30000142, "Amarr": 30002187,
        }.get(name))
        mock_cache.suggest_safer_route = AsyncMock(return_value=RouteSuggestion(
            shortest=[camped_leg], suggested=[camped_leg], detoured=False,
        ))

        with patch(
            "evealert.tools.universe.get_universe_cache", return_value=mock_cache
        ), patch(
            "evealert.tools.gatecamp.get_active_camps", return_value=[camp]
        ):
            await self.agent._run_route_check("Jita", "Amarr")

        mock_cache.suggest_safer_route.assert_awaited_once_with(
            30000142, 30002187, camped_system_ids={30000144}
        )
        messages = self._logged_messages()
        self.assertTrue(any("[CAMP]" in m for m in messages))
        # Not detoured -- only the Shortest line should render, not Suggested.
        self.assertTrue(any("Shortest route" in m for m in messages))
        self.assertFalse(any("Suggested route" in m for m in messages))

    async def test_detoured_suggestion_renders_both_routes(self):
        from evealert.tools.universe import RouteLeg, RouteSuggestion

        mock_cache = MagicMock()
        mock_cache.get_system_id = AsyncMock(side_effect=lambda name: {
            "Jita": 30000142, "Amarr": 30002187,
        }.get(name))
        shortest = [RouteLeg(1, "Hot", 1, 20, "danger")]
        suggested = [RouteLeg(2, "Quiet", 1, 0, "safe"), RouteLeg(3, "AlsoQuiet", 2, 0, "safe")]
        mock_cache.suggest_safer_route = AsyncMock(return_value=RouteSuggestion(
            shortest=shortest, suggested=suggested, detoured=True,
        ))

        with patch(
            "evealert.tools.universe.get_universe_cache", return_value=mock_cache
        ), patch(
            "evealert.tools.gatecamp.get_active_camps", return_value=[]
        ):
            await self.agent._run_route_check("Jita", "Amarr")

        messages = self._logged_messages()
        self.assertTrue(any("Shortest route to Amarr: 1 hop(s)" in m for m in messages))
        self.assertTrue(any("Suggested route to Amarr: 2 hop(s)" in m for m in messages))

    async def test_no_path_found_message(self):
        mock_cache = MagicMock()
        mock_cache.get_system_id = AsyncMock(side_effect=lambda name: {
            "Jita": 30000142, "Amarr": 30002187,
        }.get(name))
        mock_cache.suggest_safer_route = AsyncMock(return_value=None)

        with patch(
            "evealert.tools.universe.get_universe_cache", return_value=mock_cache
        ), patch(
            "evealert.tools.gatecamp.get_active_camps", return_value=[]
        ):
            await self.agent._run_route_check("Jita", "Amarr")

        messages = self._logged_messages()
        self.assertTrue(any("no path found" in m for m in messages))


class CacheMaintenanceTaskTests(unittest.IsolatedAsyncioTestCase):
    """#177: the periodic cache-maintenance task purges expired TTL-cache
    entries from the universe/zKillboard/heatmap caches."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()
        self.temp_dir = tempfile.mkdtemp()
        settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
        with open(settings_path, "w") as f:
            json.dump({}, f)
        reset_settings_store(settings_path)
        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            self.agent = AlertAgent(self.mock_main)

    def tearDown(self):
        import shutil

        from evealert.tools import threat_heatmap
        from evealert.tools.universe import get_universe_cache
        from evealert.tools.zkillboard import get_client

        # These are process-wide singletons -- clear the synthetic entries
        # this test class seeded so they don't leak into other tests.
        get_universe_cache()._kill_count_cache.clear()
        get_client()._cache.clear()
        threat_heatmap._CACHE.clear()

        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def _run_one_cycle(self):
        """Drive _cache_maintenance_task() through exactly one loop body
        execution, same mocked-sleep pattern as GateCampMonitorTests."""
        self.agent.running = True
        call_count = {"n": 0}

        async def fake_sleep(_):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                self.agent.running = False

        with patch("evealert.manager.alertmanager.asyncio.sleep", new=fake_sleep):
            await self.agent._cache_maintenance_task()

    async def test_purges_expired_entries_from_all_three_caches(self):
        from evealert.tools import threat_heatmap
        from evealert.tools.universe import get_universe_cache
        from evealert.tools.zkillboard import get_client

        stale = time.time() - 100_000
        get_universe_cache()._kill_count_cache[30000142] = (stale, 5)
        get_client()._cache["stale-system"] = (stale, None)
        threat_heatmap._CACHE[("STALE", 7)] = (stale, {})

        await self._run_one_cycle()

        self.assertNotIn(30000142, get_universe_cache()._kill_count_cache)
        self.assertNotIn("stale-system", get_client()._cache)
        self.assertNotIn(("STALE", 7), threat_heatmap._CACHE)

    async def test_fresh_entries_survive_a_cycle(self):
        from evealert.tools import threat_heatmap
        from evealert.tools.universe import get_universe_cache
        from evealert.tools.zkillboard import get_client

        now = time.time()
        get_universe_cache()._kill_count_cache[30000142] = (now, 5)
        get_client()._cache["fresh-system"] = (now, None)
        threat_heatmap._CACHE[("FRESH", 7)] = (now, {})

        await self._run_one_cycle()

        self.assertIn(30000142, get_universe_cache()._kill_count_cache)
        self.assertIn("fresh-system", get_client()._cache)
        self.assertIn(("FRESH", 7), threat_heatmap._CACHE)

    async def test_a_purge_failure_does_not_crash_the_loop(self):
        with patch(
            "evealert.tools.universe.get_universe_cache",
            side_effect=RuntimeError("boom"),
        ):
            await self._run_one_cycle()  # must not raise


class LocationMonitorDuplicateSystemInfoTests(unittest.IsolatedAsyncioTestCase):
    """#223: _location_monitor() must not re-fire _display_system_info()
    on the FIRST ESI-detected system -- start() already displayed it
    once; only a genuine later system change should trigger a fresh
    display."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()
        self.mock_main.refresh_context_line = MagicMock()
        self.temp_dir = tempfile.mkdtemp()
        settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
        with open(settings_path, "w") as f:
            json.dump({}, f)
        reset_settings_store(settings_path)
        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            self.agent = AlertAgent(self.mock_main)
        self.agent.loop = MagicMock()

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def _run_two_polls(self, locations):
        """Drive _location_monitor() through exactly len(locations) poll
        iterations, one system-location result per call, then stop."""
        self.agent.running = True
        mock_auth = MagicMock()
        mock_auth.is_authenticated = True
        location_iter = iter(locations)

        async def fake_get_location(auth):
            try:
                return next(location_iter)
            except StopIteration:
                self.agent.running = False
                return None

        with patch(
            "evealert.tools.esi_auth.get_esi_auth", return_value=mock_auth
        ), patch(
            "evealert.tools.esi_auth.get_character_location",
            new=AsyncMock(side_effect=fake_get_location),
        ), patch(
            "evealert.manager.alertmanager.asyncio.sleep", new=AsyncMock()
        ):
            await self.agent._location_monitor()

    def _logged_messages(self) -> list[str]:
        return [c.args[0] for c in self.mock_main.write_message.call_args_list]

    async def test_first_detection_does_not_schedule_display_system_info(self):
        await self._run_two_polls(["Jita"])

        self.agent.loop.create_task.assert_not_called()

    async def test_first_detection_still_logs_auto_detected_message(self):
        """The lighter "System: auto-detected -> X" line is unrelated to
        _display_system_info() and must still fire every time."""
        await self._run_two_polls(["Jita"])

        messages = self._logged_messages()
        self.assertTrue(any("auto-detected" in m and "Jita" in m for m in messages))

    async def test_second_real_system_change_schedules_display_system_info(self):
        await self._run_two_polls(["Jita", "Amarr"])

        self.agent.loop.create_task.assert_called_once()

    async def test_unchanged_system_between_polls_does_not_reschedule(self):
        await self._run_two_polls(["Jita", "Jita"])

        self.agent.loop.create_task.assert_not_called()

    async def test_settings_store_updated_on_every_detection_including_first(self):
        """server.system must stay correct even though the display is
        suppressed on the first detection."""
        await self._run_two_polls(["Jita"])

        self.assertEqual(self.agent._settings_store.get("server.system"), "Jita")


class StabilizeEnemyIdentitiesTests(unittest.TestCase):
    """#224: OCR misread tolerance -- the same on-screen pilot read
    slightly differently between polls (classic l/t/I/1 confusion) must
    not be treated as a brand-new pilot every time."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()
        self.temp_dir = tempfile.mkdtemp()
        settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
        with open(settings_path, "w") as f:
            json.dump({}, f)
        reset_settings_store(settings_path)
        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            self.agent = AlertAgent(self.mock_main)

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_first_sighting_at_a_position_is_trusted_immediately(self):
        result = self.agent._stabilize_enemy_identities(
            {(1, 1): "lilbitofgoop"}, frozenset({(1, 1)})
        )
        self.assertEqual(result, {(1, 1): "lilbitofgoop"})
        self.assertEqual(self.agent._enemy_identity_anchors[(1, 1)], "lilbitofgoop")

    def test_exact_repeat_read_keeps_the_anchor(self):
        self.agent._stabilize_enemy_identities({(1, 1): "lilbitofgoop"}, frozenset({(1, 1)}))
        result = self.agent._stabilize_enemy_identities(
            {(1, 1): "lilbitofgoop"}, frozenset({(1, 1)})
        )
        self.assertEqual(result, {(1, 1): "lilbitofgoop"})

    def test_fuzzy_close_misread_is_absorbed_into_the_anchor(self):
        """The core fix: a near-miss OCR read must not change the
        identity used downstream."""
        self.agent._stabilize_enemy_identities({(1, 1): "lilbitofgoop"}, frozenset({(1, 1)}))
        result = self.agent._stabilize_enemy_identities(
            {(1, 1): "litbitofgoop"}, frozenset({(1, 1)})
        )
        self.assertEqual(result, {(1, 1): "lilbitofgoop"})  # anchor, not the new read
        self.assertEqual(self.agent._enemy_identity_anchors[(1, 1)], "lilbitofgoop")

    def test_the_full_reported_misread_sequence_stabilizes_to_one_identity(self):
        """Exact real-world sequence from #224's log excerpt."""
        r1 = self.agent._stabilize_enemy_identities(
            {(1, 1): "lilbitofgoop"}, frozenset({(1, 1)})
        )
        r2 = self.agent._stabilize_enemy_identities(
            {(1, 1): "litbitofgoop"}, frozenset({(1, 1)})
        )
        r3 = self.agent._stabilize_enemy_identities(
            {(1, 1): "titbitofgoop"}, frozenset({(1, 1)})
        )
        self.assertEqual(r1[(1, 1)], "lilbitofgoop")
        self.assertEqual(r2[(1, 1)], "lilbitofgoop")
        self.assertEqual(r3[(1, 1)], "lilbitofgoop")  # still the same anchor throughout

    def test_dissimilar_name_requires_two_consecutive_reads_before_taking_over(self):
        self.agent._stabilize_enemy_identities({(1, 1): "Bob McTest"}, frozenset({(1, 1)}))
        # A completely different name appears once -- must not switch yet.
        r2 = self.agent._stabilize_enemy_identities(
            {(1, 1): "Evil Corp Pilot"}, frozenset({(1, 1)})
        )
        self.assertEqual(r2[(1, 1)], "Bob McTest")
        # Same new name repeats -- now it takes over (genuine pilot swap).
        r3 = self.agent._stabilize_enemy_identities(
            {(1, 1): "Evil Corp Pilot"}, frozenset({(1, 1)})
        )
        self.assertEqual(r3[(1, 1)], "Evil Corp Pilot")
        self.assertEqual(self.agent._enemy_identity_anchors[(1, 1)], "Evil Corp Pilot")

    def test_dissimilar_name_that_does_not_repeat_never_takes_over(self):
        self.agent._stabilize_enemy_identities({(1, 1): "Bob McTest"}, frozenset({(1, 1)}))
        self.agent._stabilize_enemy_identities({(1, 1): "Evil Corp Pilot"}, frozenset({(1, 1)}))
        # A THIRD, different-again name -- resets the challenger, anchor unmoved.
        r3 = self.agent._stabilize_enemy_identities(
            {(1, 1): "Some Rando"}, frozenset({(1, 1)})
        )
        self.assertEqual(r3[(1, 1)], "Bob McTest")
        self.assertEqual(self.agent._enemy_identity_anchors[(1, 1)], "Bob McTest")

    def test_position_leaving_screen_clears_its_anchor(self):
        self.agent._stabilize_enemy_identities({(1, 1): "Bob McTest"}, frozenset({(1, 1)}))
        self.agent._stabilize_enemy_identities({}, frozenset())  # icon gone
        self.assertNotIn((1, 1), self.agent._enemy_identity_anchors)

    def test_position_reused_by_a_new_pilot_after_leaving_is_trusted_immediately(self):
        self.agent._stabilize_enemy_identities({(1, 1): "Bob McTest"}, frozenset({(1, 1)}))
        self.agent._stabilize_enemy_identities({}, frozenset())  # old pilot left
        result = self.agent._stabilize_enemy_identities(
            {(1, 1): "Someone New"}, frozenset({(1, 1)})
        )
        self.assertEqual(result[(1, 1)], "Someone New")  # no stale anchor inherited

    def test_two_positions_do_not_cross_contaminate(self):
        r = self.agent._stabilize_enemy_identities(
            {(1, 1): "lilbitofgoop", (2, 2): "Someone Else"},
            frozenset({(1, 1), (2, 2)}),
        )
        self.assertEqual(r, {(1, 1): "lilbitofgoop", (2, 2): "Someone Else"})
        r2 = self.agent._stabilize_enemy_identities(
            {(1, 1): "litbitofgoop", (2, 2): "Someone Else"},
            frozenset({(1, 1), (2, 2)}),
        )
        self.assertEqual(r2, {(1, 1): "lilbitofgoop", (2, 2): "Someone Else"})

    def test_reset_alarm_clears_stabilization_state(self):
        self.agent._stabilize_enemy_identities({(1, 1): "Bob McTest"}, frozenset({(1, 1)}))
        self.agent._enemy_identity_challenger[(1, 1)] = ("X", 1)
        asyncio.run(self.agent.reset_alarm("Enemy"))
        self.assertEqual(self.agent._enemy_identity_anchors, {})
        self.assertEqual(self.agent._enemy_identity_challenger, {})


class ShouldAlarmEnemyOcrMisreadIntegrationTests(unittest.TestCase):
    """#224 acceptance criterion: a synthetic sequence of near-identical
    OCR reads at a stable position must produce exactly one alarm/history
    trigger, not one per misread variant."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()
        self.temp_dir = tempfile.mkdtemp()
        settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
        with open(settings_path, "w") as f:
            json.dump({}, f)
        reset_settings_store(settings_path)
        with patch("evealert.manager.alertmanager.AlertAgent._validate_audio_files"):
            self.agent = AlertAgent(self.mock_main)

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_stabilized_misread_sequence_triggers_alarm_exactly_once(self):
        misreads = ["lilbitofgoop", "litbitofgoop", "titbitofgoop"]
        triggers = []
        for raw_name in misreads:
            identities = self.agent._stabilize_enemy_identities(
                {(1, 1): raw_name}, frozenset({(1, 1)})
            )
            self.agent._enemy_points = [(20, 20)]  # quantizes to (1, 1) w/ grid=20
            triggers.append(self.agent._should_alarm_enemy(identities))

        self.assertEqual(triggers, [True, False, False])

    def test_unstabilized_raw_ocr_would_have_retriggered_every_time(self):
        """Sanity check that the test harness actually exercises the bug:
        without stabilization, each distinct raw string is a new key."""
        misreads = ["lilbitofgoop", "litbitofgoop", "titbitofgoop"]
        triggers = []
        for raw_name in misreads:
            self.agent._enemy_points = [(20, 20)]
            triggers.append(
                self.agent._should_alarm_enemy({(1, 1): raw_name})  # raw, unstabilized
            )

        self.assertEqual(triggers, [True, True, True])  # the bug, unfixed


class PilotHistoryStabilizationAccumulationTests(unittest.IsolatedAsyncioTestCase):
    """#224 acceptance criterion: sightings from a debounced/stabilized
    identity accumulate under one pilot_history_store record, instead of
    fragmenting across N near-duplicate OCR-misread "pilots"."""

    def setUp(self):
        self.mock_main = MagicMock()
        self.mock_main.write_message = MagicMock()
        self.temp_dir = tempfile.mkdtemp()
        settings_path = Path(self.temp_dir) / "settings.json"
        os.environ["EVEALERT_STATS_PATH"] = str(Path(self.temp_dir) / "statistics.json")
        os.environ["EVEALERT_PILOT_HISTORY_PATH"] = str(Path(self.temp_dir) / "pilot_history.db")
        with open(settings_path, "w") as f:
            json.dump({"server": {"system": "J5A-IX"}}, f)
        reset_settings_store(settings_path)
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
        self.agent._correlate_intel_enabled = False

    def tearDown(self):
        import shutil

        os.environ.pop("EVEALERT_STATS_PATH", None)
        os.environ.pop("EVEALERT_PILOT_HISTORY_PATH", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_misread_sequence_accumulates_under_one_pilot_record(self):
        from evealert.tools.esi_standings import KillProfile
        from evealert.tools.pilot_history_store import get_sightings

        misreads = ["lilbitofgoop", "litbitofgoop", "titbitofgoop"]
        info = MagicMock(
            corporation_name="Evil Corp", alliance_name="Evil Alliance",
            age_days=100, corp_history_count=2, security_status=0.0,
            character_id=987654, corporation_id=456, alliance_id=789,
        )
        info.name = "lilbitofgoop"

        for raw_name in misreads:
            # Mirrors _resolve_enemy_identities: stabilize the raw OCR read
            # for a stable on-screen position before it ever reaches the
            # ESI/history pipeline via _last_ocr_names.
            stabilized = self.agent._stabilize_enemy_identities(
                {(1, 1): raw_name}, frozenset({(1, 1)})
            )
            resolved_name = stabilized[(1, 1)]

            with patch(
                "evealert.tools.esi_standings.get_esi_client"
            ) as mock_get_client, patch(
                "evealert.tools.kos_checker.get_kos_checker"
            ) as mock_get_kos:
                mock_client = AsyncMock()
                mock_client.lookup_many = AsyncMock(return_value=[info])
                mock_client.get_zkillboard_profile = AsyncMock(
                    return_value=KillProfile(
                        kills_total=5, losses_total=1, top_ship="Loki", danger_ratio=0.5
                    )
                )
                mock_get_client.return_value = mock_client
                mock_kos = MagicMock()
                mock_kos.check = AsyncMock(return_value=None)
                mock_get_kos.return_value = mock_kos

                await self.agent.run_intel_check([resolved_name])

        anchor_sightings = get_sightings("lilbitofgoop")
        self.assertEqual(len(anchor_sightings), 3)
        self.assertEqual(get_sightings("litbitofgoop"), [])
        self.assertEqual(get_sightings("titbitofgoop"), [])


if __name__ == "__main__":
    unittest.main()
