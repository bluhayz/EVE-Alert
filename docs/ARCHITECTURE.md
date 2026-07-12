# EVE Alert — Architecture Reference

> Companion to [COCO.md](../COCO.md). COCO.md covers conventions and thread-safety
> rules; this file maps the full v4.0 module inventory and data flow.

## System Overview

EVE Alert is a Tkinter desktop app with a background asyncio detection engine.
Three concerns, three execution contexts:

| Context | Owner | Runs |
|---|---|---|
| Main thread | Tkinter event loop | All GUI (`MainMenu`, `SettingMenu`, dialogs, overlay) |
| Alert daemon thread | `AlertAgent.start()` creates its own asyncio loop | Vision loops, alarm dispatch, all async intel tasks |
| Helper daemon threads | pynput listener, pystray tray, ESI login thread | Input hooks, tray menu, one-shot OAuth flow |

Cross-thread traffic is one-directional by convention: background code schedules
GUI updates via `main.after(0, ...)` (wrapped as `AlertAgent._ui()`); the GUI
signals the engine via plain attribute flags (`setting.changed`) polled by the
`run()` loop, or `asyncio.run_coroutine_threadsafe` for one-shot coroutines
(see `SettingMenu._check_route`).

## Module Inventory (v4.0.0)

### Core engine — `evealert/manager/alertmanager.py` (~1500 lines)

`AlertAgent` is the hub. On `start()` (called in a daemon thread) it creates an
event loop and spawns tasks:

- `vision_thread` / `vision_faction_thread` — screenshot + template match every
  `VISION_SLEEP_INTERVAL` (0.1 s); write `self.enemy` / `self.faction` booleans.
- `run()` — polls those booleans, fires `alarm_detection()` (log + stats +
  sound + webhooks + push + plugins + intel lookups), handles per-type cooldown
  and settings hot-reload.
- Conditional tasks based on settings: `IntelWatcher`, `NeighborMonitor`,
  `DscanWatcher`, `WebStatusServer`, `KillmailMonitor`, `_thera_monitor`,
  `_sov_monitor`, `_esi_deep_integration_start`, `_check_for_update`,
  `_display_system_info`.

### GUI — `evealert/menu/`

| File | Class | Notes |
|---|---|---|
| `main.py` | `MainMenu`, `MainMenuButtons`, `MenuManager` | Root window; owns `AlertAgent`, `OverlaySystem`, `TrayManager`, pynput listener |
| `setting.py` | `SettingMenu`, `DEFAULT_SETTINGS` | ~2000 lines; the settings schema source of truth; lazy-created Toplevel; `save()` / `apply_settings_runtime()` / profile + threat-tier + sound + ESI-login helpers |
| `config.py` | `ConfigModeMenu` | Region-selection guide window; EVE window auto-detect |
| `image_manager.py` | `ImageManagerWindow` | Add/remove user template images (platformdirs `img/`) |
| `statistics.py` | `StatisticsWindow` | Live Stats + Sessions tabs; CSV export |
| `threshold_editor.py` | `ThresholdEditorWindow` | Per-image confidence overrides |

### Detection — `evealert/tools/`

| File | Purpose |
|---|---|
| `vision.py` | OpenCV `matchTemplate` engine (`TM_CCOEFF_NORMED`); per-image threshold overrides; debug overlay windows |
| `windowscapture.py` | `mss` screen grab; lazy per-thread init (thread-affinity requirement) |
| `window_finder.py` | EVE client window bounds (pygetwindow / osascript) |
| `overlay.py` | Fullscreen marquee region selector; writes regions to settings.json |

### Intel & integrations — `evealert/tools/`

| File | Feature | External API |
|---|---|---|
| `zkillboard.py` | Recent kills in system on Enemy alarm | ESI + zKillboard |
| `esi_standings.py` | Pilot lookup: corp/alliance, age, sec status, corp history, kill profile; Local join parser | ESI + zKillboard |
| `esi_auth.py` | EVE SSO OAuth2 (v4.0): token lifecycle, personal standings, fleet membership, structure fuel | EVE SSO + ESI (authed) |
| `universe.py` | System ID/name cache, jump-graph BFS, gate-count classification, sovereignty, route threat | ESI + zKillboard |
| `neighbor_monitor.py` | Kill polling within N jumps | zKillboard |
| `dscan_watcher.py` | D-scan file tail; ship threat tiers (RED/ORANGE/YELLOW/GREEN); probe detection | local files |
| `intel_watcher.py` | Intel chat-log tail | local files |
| `kos_checker.py` | KOS lookups (CVA + custom APIs + local list) | CVA KOS API |
| `wormhole.py` | Thera connections, WH class inference, WH-drop heuristic | Eve-Scout |
| `fleet_context.py` | Fleet composition analysis, TZ activity profile, killmail monitor | zKillboard + ESI |
| `push_notifier.py` | Telegram / Pushover / ntfy.sh push | respective APIs |
| `web_server.py` | Localhost status dashboard + JSON API (stdlib asyncio only) | — |
| `plugin_loader.py` | User `.py` plugin hooks (`on_start/on_stop/on_enemy/on_faction/on_intel`) | — |
| `update_checker.py` | Startup version check | GitHub Releases |

### Persistence — `evealert/settings/`

| File | Purpose |
|---|---|
| `helper.py` | `get_resource_path()` (PyInstaller-aware), settings/img/plugins path helpers |
| `stats_store.py` | Atomic lifetime stats + per-session JSON reports |
| `validator.py` | `ConfigValidator` static checks (regions, scales, cooldown, webhook URL, audio) |
| `logger.py` | Rotating file logging setup |

## Data Flow: One Enemy Alarm

```
vision_thread (0.1s cycle)
  └─ WindowCapture.get_screenshot_value()      # mss grab of alert region
  └─ Vision.find()                              # matchTemplate vs image_* templates
  └─ self.enemy = True
run() (0.1–0.2s cycle)
  └─ alarm_detection("Enemy Appears!", ...)
       ├─ _ui(write_message)                    # GUI log (red) — main thread
       ├─ statistics.add_alarm + save_lifetime_stats
       ├─ play_sound()                          # ≤3 plays then per-type cooldown
       ├─ send_webhook_message()                # all-events + per-type Discord hooks
       ├─ push notifier (Telegram/Pushover/ntfy)
       ├─ auto-screenshot (optional)
       ├─ escalation counter check
       ├─ plugin on_enemy hook (thread pool)
       ├─ zKillboard system kills lookup (cooldown-gated)
       └─ _augment_with_esi()                   # async: parse Local log joins →
            ├─ ESI char/corp/alliance lookup      per-pilot intel lines
            ├─ threat-tier match, flashy check, young-pilot heuristic
            ├─ zKillboard kill profile
            ├─ KOS check (CVA/custom/local)
            ├─ WH-drop heuristic
            ├─ fleet composition (3+ hostiles)
            └─ personal-standings classification (v4.0, needs SSO login)
```

When detection clears: `reset_alarm()` zeroes trigger counts and sends the
"Alarm cleared" webhook once.

## Singleton Pattern (caveat)

Several modules use module-level singletons via `get_*()` factories:
`get_client()` (zkillboard), `get_esi_client()`, `get_universe_cache()`,
`get_plugin_manager()`, `get_kos_checker()`, `get_push_notifier()`,
`get_esi_auth()`.

**Caveat:** `get_kos_checker(**kwargs)` and `get_esi_auth(client_id=...)`
ignore their arguments after first construction — settings changes to these
subsystems do not take effect until app restart. `get_push_notifier()` instead
rebuilds whenever kwargs are passed. Treat this inconsistency carefully when
modifying (tracked as a known issue).

## Settings Schema

`DEFAULT_SETTINGS` in `evealert/menu/setting.py` is the schema source of truth.
Top-level keys as of v4.0: `log_level`, `active_profile`, `alert_region_1/2`,
`faction_region_1/2`, `detectionscale`, `faction_scale`, `cooldown_timer`,
`cooldown_timer_enemy`, `cooldown_timer_faction`, `volume`, `server`
(webhook/system/mute/webhook_template), `hotkeys`, `sounds`, `profiles`,
`image_thresholds`, `intelligence`, `webhooks` (per-type), `esi`,
`threat_tiers`, `plugins`, `web_ui`, `adjacent`, `dscan`, `kos`, `kos_list`,
`push`, `notifications`, `wormhole`, `fleet`, `esi_oauth`.

`merge_settings_with_defaults()` back-fills missing keys recursively on load,
so adding a key to `DEFAULT_SETTINGS` is the complete migration story.

Storage: `platformdirs.user_config_dir("evealert")` —
`settings.json`, `statistics.json`, `esi_token.json`, `sessions/`, `img/`,
`plugins/`, `logs/`.

## Testing

`tests/` covers: alertmanager basics, hotkeys, intel watcher, statistics,
stats store, update checker (respx-mocked), validator, vision, zkillboard
(respx-mocked). GUI classes and the newer intel modules (universe,
neighbor_monitor, dscan_watcher, kos_checker, wormhole, fleet_context,
esi_auth, web_server, push_notifier, plugin_loader) have **no test coverage** —
be extra careful editing those.

Run: `make test` (pytest + coverage) or `make check` (lint + tests).
