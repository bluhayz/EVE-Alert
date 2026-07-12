# EVE Alert — Feature ↔ Settings ↔ Module Map

Quick lookup table for AI agents: given a feature, find its settings block,
implementing module, and where it's wired into the engine. "Wired in" points
into `evealert/manager/alertmanager.py` unless noted.

| Feature | Settings key(s) | Module | Wired in |
|---|---|---|---|
| Enemy detection (Local icons) | `alert_region_*`, `detectionscale`, `image_thresholds` | `tools/vision.py`, `tools/windowscapture.py` | `vision_thread` |
| Faction detection | `faction_region_*`, `faction_scale` | same | `vision_faction_thread` |
| Audio alarms + volume | `volume`, `server.mute`, `sounds` | soundfile/sounddevice | `play_sound` |
| Per-type sound cooldown | `cooldown_timer_enemy/_faction` | — | `play_sound` |
| Custom alarm sounds | `sounds.alarm`, `sounds.faction` | `menu/setting.py` browse buttons | `load_settings` |
| Discord webhook (all events) | `server.webhook`, `server.webhook_template` | dhooks_lite | `send_webhook_message` |
| Per-type webhooks + min-count | `webhooks.enemy/faction` | — | `_send_typed_webhook` |
| Named profiles | `profiles`, `active_profile` | `menu/setting.py` `_save/_new/_load/_delete_profile` | overlay applied in `SettingMenu.load_settings` |
| Per-image thresholds | `image_thresholds` | `menu/threshold_editor.py` | passed into `Vision.find*` |
| Image Manager (custom templates) | — (files in user `img/`) | `menu/image_manager.py` | `_load_image_files` |
| Hotkey remap | `hotkeys` | `hotkeys.py` | `MainMenu.on_key_release` |
| System tray | — | `tray.py` | `MainMenu.__init__` |
| EVE window auto-detect | — | `tools/window_finder.py` | `ConfigModeMenu._detect_eve_window` |
| Lifetime stats + session reports | — (`statistics.json`, `sessions/`) | `settings/stats_store.py`, `menu/statistics.py` | `alarm_detection`, `stop` |
| zKillboard kills on alarm | `intelligence.zkillboard_*` | `tools/zkillboard.py` | `alarm_detection` |
| Intel channel watcher | `intelligence.intel_log_*` | `tools/intel_watcher.py` | `start` |
| ESI pilot intel (corp/alliance/age/sec/kill profile) | `esi.*` | `tools/esi_standings.py` | `_augment_with_esi` |
| Threat tiers (KOS-RED/HOSTILE/CAUTION prefixes) | `threat_tiers` | — | `_augment_with_esi` |
| Flashy pilot alert (sec ≤ −5) | `esi.alert_flashy` | — | `_augment_with_esi` |
| Young-pilot / cyno-alt warning | (always on with ESI) | — | `_augment_with_esi` |
| Plugin system | `plugins.enabled` | `tools/plugin_loader.py` | `_load_plugins`, hooks in `alarm_detection`/`start`/`stop`/`_on_intel_line` |
| Web status dashboard | `web_ui.*` | `tools/web_server.py` | `start`; log mirror in `MainMenu.write_message` |
| Adjacent-system kill monitor | `adjacent.*` | `tools/neighbor_monitor.py` | `start` |
| Route threat check | `adjacent.destination_system` | `tools/universe.py` | `SettingMenu._check_route` → `_run_route_check` |
| Pipe/pocket classification + sov display | — (uses `server.system`) | `tools/universe.py` | `_display_system_info`, `_sov_monitor` |
| D-scan watcher (tiers + probes) | `dscan.*` | `tools/dscan_watcher.py` | `start`, `_on_dscan_*` |
| KOS checker | `kos.*`, `kos_list` | `tools/kos_checker.py` | `_augment_with_esi` |
| Push notifications | `push.*` | `tools/push_notifier.py` | `alarm_detection` |
| Auto-screenshot on alarm | `notifications.auto_screenshot` | mss | `_capture_alarm_screenshot` |
| Alarm escalation counter | `notifications.escalation_threshold` | — | `alarm_detection` |
| Thera connection monitor | `wormhole.thera_*` | `tools/wormhole.py` | `_thera_monitor` |
| WH drop heuristic | `wormhole.wh_drop_*` | `tools/wormhole.py` | `_augment_with_esi` |
| Fleet composition analysis | `fleet.composition_enabled` | `tools/fleet_context.py` | `_augment_with_esi` |
| Killmail monitor (tracked chars) | `fleet.killmail_enabled`, `fleet.tracked_character_ids` | `tools/fleet_context.py` | `start` |
| EVE SSO login | `esi_oauth.client_id` | `tools/esi_auth.py` | `SettingMenu._esi_login` |
| Standings auto-classify | `esi_oauth.standings_auto_classify` | `tools/esi_auth.py` | `_esi_standings_monitor` + `_augment_with_esi` |
| Fleet membership display | `esi_oauth.fleet_monitor` | `tools/esi_auth.py` | `_esi_deep_integration_start` |
| Structure fuel warnings | `esi_oauth.structure_alerts` | `tools/esi_auth.py` | `_esi_deep_integration_start` |
| Update check | — | `tools/update_checker.py` | `_check_for_update` |

## Gotchas for agents

- **`kos_list` is defined in `DEFAULT_SETTINGS` but never read** — the
  KosChecker's local hostile list is never populated from settings (no UI
  either). Dead setting; `threat_tiers` provides the equivalent user-facing
  behavior via `_augment_with_esi`.
- Many intel features depend on `server.system` being a real system name
  (not the placeholder `"Enter a System Name"`); they silently no-op otherwise.
- All ESI-name-resolution features are currently broken by the removed ESI
  search endpoints (see docs/INTEGRATIONS.md and open issues).
- `SettingMenu.save()` rebuilds settings from `DEFAULT_SETTINGS` + UI fields;
  any key not backed by a widget is reset to default on Save (this currently
  wipes `profiles` / `image_thresholds` — tracked as a bug).
