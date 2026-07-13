# EVE Alert — Architecture Reference

> Companion to [COCO.md](../COCO.md). COCO.md covers conventions and thread-safety
> rules; this file maps the module inventory and data flow as of **v6.1 (PySide6)**.

## System Overview

EVE Alert is a PySide6 desktop app with a background asyncio detection engine.
Three concerns, three execution contexts:

| Context | Owner | Runs |
|---|---|---|
| Qt main thread | `QApplication` (`ui/app.py`) | All widgets: MainWindow, dialogs, tray, QTimers |
| Alert daemon thread | `AlertAgent.start()` creates its own asyncio loop | Vision loops, alarm dispatch, all async intel tasks |
| Helper threads | pynput listener, `_LoginThread(QThread)`, TTS thread, per-fetch workers | Global hotkeys, SSO login, speech, blocking fetches |

Cross-thread traffic is mediated by two seams established in the migration
(#124–#131):

- **`UIBridge`** (`evealert/bridge.py`): the engine's only view of the UI —
  `log()`, `refresh_region_toggles()`, `show_error()`. Implemented by
  `QtBridge(QObject)` (`ui/qt_bridge.py`) whose methods emit signals; Qt
  queues delivery to main-thread slots. UI worker threads use the same
  pattern with their own `Signal` declarations.
- **`SettingsStore`** (`evealert/settings/store.py`): GUI-free settings
  load/merge/save shared by engine and UI (singleton via
  `get_settings_store()`). Saving sets `store.changed`; `AlertAgent.run()`
  polls and hot-reloads.

## Module Inventory (v6.1.0)

### Core engine — `evealert/manager/alertmanager.py` (~2000 lines)

`AlertAgent` is the hub. On `start()` (in a daemon thread) it creates an event
loop and spawns tasks:

- `vision_thread` / `vision_faction_thread` — mss screenshot + OpenCV template
  match every 0.1 s; set `self.enemy` / `self.faction`.
- `run()` — polls those flags, dedups per enemy via position-quantized
  `_EnemySighting` records (with optional re-arm after
  `alerts.rearm_minutes`, #144), fires `alarm_detection()`, handles per-type
  cooldowns and settings hot-reload.
- Conditional tasks per settings: `IntelWatcher` (+ `intel_parser` reports with
  jump-distance lookups), `NeighborMonitor`, `DscanWatcher` (threat tiers, ship
  classes, probes, **cyno**, signature deltas), `WebStatusServer`,
  `KillmailMonitor`, Thera monitor, sov monitor, `_peak_hours_monitor` (#151),
  ESI deep-integration (standings/fleet/structures), update check.

`alarm_detection()` fan-out: GUI log → statistics (+ lifetime persist) → TTS →
automation webhook (#153) → sound → Discord webhooks → push notifications →
auto-screenshot → escalation counter → plugin hooks → zKillboard kills →
`_augment_with_esi()` (pilot intel, KOS, standings ally-filter #147, WH-drop,
fleet composition, **composite threat score** #141).

### GUI — `evealert/ui/` (PySide6)

| File | Class | Notes |
|---|---|---|
| `app.py` | — | QApplication factory; applies `theme.qss` |
| `main_window.py` | `MainWindow`, `_MainProxy` | Header/status card, start/stop, log pane (500-block cap), F1–F4 hotkey routing, tray + dialogs |
| `qt_bridge.py` | `QtBridge` | UIBridge → signals (`log_message`, `toggles_changed`, `error`) |
| `tray.py` | `AppTray` | QSystemTrayIcon; Show/Start/Stop/Exit |
| `settings_dialog.py` | `SettingsDialog`, `_LoginThread` | Form generated from the `FIELDS` registry + hand-built sections; profile bar; SSO login |
| `config_dialog.py` | `ConfigDialog` | Region-selection guidance and status |
| `region_overlay.py` | `RegionOverlay` | Fullscreen QRubberBand selector; devicePixelRatio-aware physical coords |
| `statistics_window.py` | `StatisticsWindow` | Stat cards, history/sessions tables, Threat Heatmap tab (#148) |
| `image_manager.py` | `ImageManagerDialog` | Template add/remove/preview; `cv2.imread` validation |
| `threshold_editor.py` | `ThresholdEditorDialog` | Per-image confidence overrides |
| `notification_wizard.py` | `NotificationWizardDialog` | Guided Telegram/Pushover/ntfy setup with live test (#149) |
| `theme.py` / `theme.qss` | — | Tokens + stylesheet; button variants via `class` dynamic property |

### Detection & data

| File | Purpose |
|---|---|
| `tools/vision.py` | `matchTemplate` engine, per-image thresholds, unreadable-template guard |
| `tools/windowscapture.py` | mss grab; lazy per-thread init |
| `tools/window_finder.py` | EVE client window bounds |
| `tools/ocr_local.py` | Optional Tesseract pilot-name OCR (#98) |
| `data/ship_classes.py` | `ShipThreatClass` enum + `SHIP_CLASS_MAP` + `classify_ship()` (#140) |
| `tools/threat_score.py` | `compute_threat_score()` → 1–10 + CAUTION/HIGH/CRITICAL (#141) |
| `tools/space_profiles.py` | F3 presets: nullsec / wormhole / highsec (#143) |
| `tools/tts.py` | pyttsx3 speech, daemon-thread, lock-serialized (#139) |

### Intel & integrations — see [INTEGRATIONS.md](INTEGRATIONS.md) for endpoints

`zkillboard.py` (kills-on-alarm + `clean_zkb_entries`), `esi_standings.py`,
`esi_auth.py` (SSO/PKCE), `universe.py` (jump graph, route threat, sov,
`resolve_ids`), `neighbor_monitor.py`, `dscan_watcher.py`, `intel_watcher.py` +
`intel_parser.py`, `kos_checker.py`, `push_notifier.py`, `wormhole.py`,
`fleet_context.py`, `threat_heatmap.py`, `web_server.py`, `plugin_loader.py`,
`net_safety.py`, `update_checker.py`, `http_common.py`.

### Persistence — `evealert/settings/`

`store.py` (SettingsStore + DEFAULT_SETTINGS), `fields.py` (FieldSpec registry
driving the settings form), `helper.py` (resource/config paths),
`stats_store.py` (lifetime stats + session reports), `validator.py`,
`logger.py`.

## Data Flow: One Enemy Alarm

```
vision_thread (0.1 s)
  └─ WindowCapture.get_screenshot_value()   # mss grab
  └─ Vision.find()                          # template match
  └─ self.enemy = True
run()
  └─ _should_alarm_enemy()                  # per-enemy dedup + rearm (#100/#144)
  └─ alarm_detection("Enemy Appears!")
       ├─ bridge.log(...)  ──signal──►  MainWindow.append_log   [main thread]
       ├─ statistics + save_lifetime_stats
       ├─ tts.speak(...)                    # daemon thread
       ├─ automation webhook POST (#153) + web_server latest-alarm slot
       ├─ play_sound / Discord webhooks / push / screenshot / plugins
       ├─ zKillboard recent kills (cooldown-gated)
       └─ _augment_with_esi()
            ├─ Local-join (or OCR) names → ESI char/corp/alliance/age/sec
            ├─ threat tiers, KOS, standings ally-filter (#147)
            ├─ zKB kill profile + D-scan ship cross-reference (#150)
            ├─ WH-drop heuristic, fleet composition
            └─ compute_threat_score() → "[THREAT: 7/10 — CRITICAL] …"
```

## Singletons

`get_settings_store()` (+ `reset_settings_store(path)` for tests),
`get_client()` (zkillboard), `get_esi_client()`, `get_universe_cache()`,
`get_plugin_manager()`, `get_kos_checker()` (reconfigure-aware),
`get_push_notifier()` (rebuilds on kwargs), `get_esi_auth()` (client-id
reconfigure-aware, #115).

## Testing

`tests/` covers ~30 modules (335+ tests). GUI dialogs largely untested (run
offscreen where they are). Two known gaps tracked in the v6.2 milestone:
tests currently write to the real user config dir (#159), and MagicMock-based
store mocks have masked real API drift (#155 — always use
`reset_settings_store(tmp_path)`).

Run: `make test` / `make check`.
