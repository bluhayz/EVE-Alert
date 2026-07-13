# EVE Alert — Feature ↔ Settings ↔ Module Map (v6.1)

Quick lookup table for AI agents: given a feature, find its settings block,
implementing module, and where it's wired into the engine. "Wired in" points
into `evealert/manager/alertmanager.py` unless noted. UI = `evealert/ui/`.

| Feature | Settings key(s) | Module | Wired in |
|---|---|---|---|
| Enemy detection | `alert_region_*`, `detectionscale`, `image_thresholds` | `tools/vision.py`, `tools/windowscapture.py` | `vision_thread` |
| Faction detection | `faction_region_*`, `faction_scale` | same | `vision_faction_thread` |
| Per-enemy dedup + re-alert | `alerts.rearm_minutes` | — (`_EnemySighting`) | `_should_alarm_enemy` |
| Audio alarms + volume | `volume`, `server.mute`, `sounds` | soundfile/sounddevice | `play_sound` |
| Per-type sound cooldown | `cooldown_timer_enemy/_faction` | — | `play_sound` |
| TTS voice alerts | `notifications.tts_enabled`, `tts_rate` | `tools/tts.py` | `alarm_detection`; test in Settings |
| Discord webhook (all events) | `server.webhook`, `server.webhook_template` | dhooks_lite | `send_webhook_message` |
| Per-type webhooks + min-count | `webhooks.enemy/faction` | — | `_send_typed_webhook` |
| Automation bridge (outbound POST) | `automation.enabled`, `automation.webhook_url` | `tools/net_safety.py` guards | `_post_automation_webhook`; also `GET /api/alarm/latest` |
| Push notifications | `push.*` | `tools/push_notifier.py` | `alarm_detection`; setup wizard `ui/notification_wizard.py` |
| Auto-screenshot / escalation | `notifications.auto_screenshot`, `escalation_threshold` | mss | `alarm_detection` |
| Named profiles | `profiles`, `active_profile` | `settings/store.py` overlay | `SettingsDialog` profile bar (⚠ #156) |
| Space profiles (F3) | writes dscan/alerts/notifications/intelligence keys | `tools/space_profiles.py` | `MainWindow._cycle_space_profile` (⚠ #155) |
| Per-image thresholds | `image_thresholds` | `ui/threshold_editor.py` | `Vision.find*` |
| Image Manager | — (user `img/` dir) | `ui/image_manager.py` | `_load_image_files` |
| Hotkeys F1/F2 remap | `hotkeys` | `hotkeys.py` | `MainWindow._setup_hotkeys` (⚠ restart needed, #161) |
| F4 status readout | — | `tools/tts.py`, `tools/threat_score.py` | `MainWindow._speak_status` |
| System tray | — | `ui/tray.py` | `MainWindow._build_tray` |
| EVE window auto-detect | — | `tools/window_finder.py` | `ConfigDialog` |
| Region selection overlay | writes region keys | `ui/region_overlay.py` | `ConfigDialog.start_selection` |
| Lifetime stats + sessions | — (`statistics.json`, `sessions/`) | `settings/stats_store.py`, `ui/statistics_window.py` | `alarm_detection`, `stop` |
| zKillboard kills on alarm | `intelligence.zkillboard_*` | `tools/zkillboard.py` | `alarm_detection` |
| Intel channel watcher + parser | `intelligence.intel_log_*` | `tools/intel_watcher.py`, `tools/intel_parser.py` | `start`, `_on_intel_report` (+ jump distance) |
| ESI pilot intel | `esi.*` | `tools/esi_standings.py` | `_augment_with_esi` |
| OCR name detection | `ocr.*` | `tools/ocr_local.py` (Tesseract) | `_augment_with_esi` |
| Threat tiers | `threat_tiers` | — | `_augment_with_esi` |
| Composite threat score | (derives from other signals) | `tools/threat_score.py` | after ESI intel block |
| KOS checker | `kos.*` (CVA off by default, #135) | `tools/kos_checker.py` | `_augment_with_esi` |
| Standings ally filter | `esi_oauth.standings_filter_blues` | `tools/esi_auth.py` | `_augment_with_esi` (#147) |
| D-scan monitor + ship classes | `dscan.*` | `tools/dscan_watcher.py`, `data/ship_classes.py` | `start`, `_on_dscan_*` |
| Cyno detection | (part of D-scan) | same | CRITICAL alarm path, bypasses cooldown (#146) |
| WH signature delta | `dscan.alert_new_signatures` | `tools/dscan_watcher.py` | `on_new_signature` (#145) |
| Ship cross-reference | (zKB top_ship × D-scan types) | `tools/dscan_watcher.py`.`current_visible_types` | `_augment_with_esi` (#150) |
| Adjacent-system monitor | `adjacent.*` | `tools/neighbor_monitor.py` | `start` |
| Route threat check | `adjacent.destination_system` | `tools/universe.py` | Settings "Check Route" |
| Sov + system classification | `server.system` | `tools/universe.py` | `_display_system_info`, sov monitor |
| Thera monitor / WH drop | `wormhole.*` | `tools/wormhole.py` | Thera task / `_augment_with_esi` |
| Fleet composition / killmails | `fleet.*` | `tools/fleet_context.py` | `_augment_with_esi` / `start` |
| Constellation threat heatmap | — | `tools/threat_heatmap.py` | Statistics window tab (#148) |
| Peak-hours warning | `intelligence.peak_hours_warning`, `peak_threshold_multiplier` | `tools/threat_heatmap.py` | `_peak_hours_monitor` (#151) |
| EVE SSO login | `esi_oauth.client_id` (user-registered, 32-hex) | `tools/esi_auth.py` | Settings dialog `_LoginThread` |
| Fleet membership / structure fuel | `esi_oauth.fleet_monitor`, `structure_alerts` | `tools/esi_auth.py` | ESI deep-integration start |
| Web dashboard + API | `web_ui.*` | `tools/web_server.py` | `start`; log mirrored from `MainWindow.append_log` |
| Plugin system | `plugins.enabled` | `tools/plugin_loader.py` | hooks in `alarm_detection`/`start`/`stop`/intel |
| Diagnostics bundle / log level | `diagnostics.*`, `log_level` | Settings Diagnostics section | — |
| Update check | — | `tools/update_checker.py` | `start` |

## Gotchas for agents

- **Registry first:** simple settings are `FieldSpec` entries in
  `settings/fields.py` — the dialog auto-generates their widgets and
  round-trip. Only add hand-built dialog code for structured settings.
- Open v6.2 defects that touch this map: profiles bake into base settings on
  save (#156), space profiles crash (#155), hotkey remap needs restart (#161),
  F1/F2 fire outside Config Mode (#154).
- Many intel features silently no-op until `server.system` is a real system
  name (placeholder `"Enter a System Name"` disables them).
- zKillboard list responses: always `clean_zkb_entries()` (`[null]` = empty),
  and expect the ~200-entry page cap (#163).
- SSO requires the user's own developer-application client ID; there is no
  built-in client (#136 — and the settings placeholder text is wrong until
  #157 lands).
