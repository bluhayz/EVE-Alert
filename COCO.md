# COCO.md — EVE-Alert AI Context Document

> **For AI coding assistants.** This document is the canonical source of
> architecture, design decisions, and conventions for the EVE-Alert repository.
> Read this before touching any source file.
>
> Companion references in `docs/`:
> - [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module inventory, data flow, threading model
> - [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) — every external API endpoint used, timeouts, failure modes
> - [docs/FEATURES.md](docs/FEATURES.md) — feature ↔ settings-key ↔ module lookup table

---

## 1. Project Purpose

EVE-Alert is a desktop PvP alert and situational-awareness tool for the MMO
**EVE Online**. It monitors a configurable region of the screen (typically the
in-game Local Chat roster) using OpenCV template matching, and fires audio
alarms, TTS readouts, Discord webhooks, and mobile push notifications when
enemy players or faction spawns are detected. A large intel layer augments
alarms with ESI pilot lookups, zKillboard activity, D-scan classification,
threat scoring, and wormhole/route awareness.

**Primary platform:** Windows. (macOS support ended with the v5 PySide6
migration; code is not intentionally broken on macOS but is untested there.)
Distributed as a standalone `.exe` built with PyInstaller.

**Version:** `evealert/__init__.py:__version__` is the single source of truth.

**GUI framework:** **PySide6 (Qt 6)** since v5.0. The customtkinter/Tkinter UI
(`evealert/menu/`, `evealert/tray.py`, `evealert/tools/overlay.py`) was deleted
at cutover — any reference to those paths or to `after(0, ...)` marshalling in
older docs/commits is historical.

---

## 2. Architecture Overview

Three execution contexts:

| Context | Owner | Runs |
|---|---|---|
| **Qt main thread** | `QApplication` (`evealert/ui/app.py`) | All widgets: `MainWindow`, dialogs, tray, timers |
| **Alert daemon thread** | `AlertAgent.start()` creates its own asyncio loop | Vision loops, alarm dispatch, every async intel task |
| **Helper threads** | pynput listener, `_LoginThread(QThread)`, TTS thread, heatmap worker | Global hotkeys, SSO login, speech, blocking fetches |

### Engine ↔ UI decoupling (Phase 0 of the migration, #124)

- `evealert/bridge.py` defines the **`UIBridge` protocol**: `log(text, color)`,
  `refresh_region_toggles()`, `show_error(msg)`. The engine only ever talks to
  this interface.
- `evealert/ui/qt_bridge.py` implements it as **`QtBridge(QObject)`** whose
  methods emit Qt signals (`log_message`, `toggles_changed`, `error`). Signal
  emission is thread-safe; Qt queues delivery to main-thread slots
  automatically. **This replaces the old `after(0, ...)` discipline entirely.**
- `evealert/settings/store.py` owns **`SettingsStore`** — GUI-free settings
  load/merge/save plus the `changed` hot-reload flag, shared by engine and UI
  via `get_settings_store()`. `DEFAULT_SETTINGS` lives here.
- `MainWindow` passes a small `_MainProxy` object to `AlertAgent` that routes
  the legacy `main.write_message(...)`-style calls into the bridge.

```
AlertAgent (asyncio, daemon thread)
    │  bridge.log("Enemy Appears!", "red")        ← any thread, safe
    ▼
QtBridge.log_message.emit(...)  ──queued──►  MainWindow.append_log(...)   [Qt main thread]
    ▲
SettingsStore.changed  ◄── SettingsDialog.save()   (engine polls + clears each run() cycle)
```

---

## 3. Critical Thread Safety Rules

> **This is the most important section for AI agents.** Violating these rules
> causes silent corruption or hard crashes.

### Rule 1 — Engine→UI traffic goes through the bridge, nothing else

Engine/async code must never touch a widget, and UI code must never mutate
engine state from a worker thread. Use `self.bridge.log(...)` /
`refresh_region_toggles()` / `show_error(...)` from the engine; use Qt signals
for any UI worker thread (see `NotificationWizardDialog._test_done` for the
canonical pattern: declare a `Signal`, `emit()` from the worker, connect to a
main-thread slot).

```python
# CORRECT (engine thread)
self.bridge.log("hello", "green")

# CORRECT (UI worker thread)
self._result_ready.emit(payload)          # slot runs on main thread

# WRONG — widget call from a worker thread
self._status_label.setText("...")
# WRONG — QTimer.singleShot from a non-Qt thread (timers need a Qt thread)
QTimer.singleShot(0, update_fn)
```

### Rule 2 — `mss.mss()` must be created in the alert thread

`WindowCapture` lazily creates its `mss` instance on first capture, which
happens inside the alert thread. **Never** move `mss.mss()` construction to
`__init__`.

### Rule 3 — The asyncio event loop is created in the daemon thread

`AlertAgent.__init__` sets `self.loop = None`; `AlertAgent.start()` (running in
the daemon thread) creates it. Do not create the loop in `__init__`.

### Rule 4 — Stop the event loop thread-safely

```python
# CORRECT (from any thread, including the loop's own)
self.alert.loop.call_soon_threadsafe(self.alert.loop.stop)
```

`AlertAgent.stop()` cancels all long-running tasks first (see #102), and
`MainWindow.exit_app()` is the canonical shutdown sequence.

### Rule 5 — pynput listener lifecycle

`MainWindow._setup_hotkeys()` starts the global-hotkey listener
(daemon thread). Its callback must only `self.hotkey_pressed.emit(...)` —
never touch widgets. `exit_app()` stops it.

### Rule 6 — Cooldown timers are absolute future timestamps

```python
self.cooldown_timers[alarm_type] = time.time() + self.cooldowntimer
if time.time() < self.cooldown_timers[alarm_type]:
    return  # still cooling
```

### Rule 7 — Settings writes are read-merge-write

Always `settings = store.load()` (or `load_raw()` once #156 lands) → patch the
keys you own → `store.save(settings)`. **Never** seed a save from
`DEFAULT_SETTINGS` — that pattern deleted user profiles once (#108).

### Rule 8 — Never mock `SettingsStore` with `MagicMock` in tests

Use a real store on a temp path: `reset_settings_store(tmp_path / "s.json")`.
A MagicMock accepts calls that don't exist on the real class and has already
masked a shipped crash (#155).

---

## 4. File Map

### Root

| File | Purpose |
|------|---------|
| `main.py` | Entry point — ASCII banner, then `evealert.ui.app.run()` |
| `pyproject.toml` | Build config (hatchling), deps (PySide6, opencv, httpx, …), extras: `dev`, `tts`, `build-windows` |
| `pytest.ini` | Pytest config |

### `evealert/` (core)

| File | Purpose |
|------|---------|
| `__init__.py` | `__version__`, `__title__` |
| `bridge.py` | `UIBridge` protocol — the engine's only view of the UI |
| `constants.py` | Magic numbers: timing, OpenCV params, audio, image prefixes |
| `exceptions.py` | Custom exception hierarchy |
| `hotkeys.py` | `parse_hotkey()`, `key_matches()`, `DEFAULT_HOTKEYS` |
| `statistics.py` | `AlarmEvent` + `AlarmStatistics` (session tracking, history deque) |
| `data/ship_classes.py` | `ShipThreatClass` enum (+ `urgency`), `SHIP_CLASS_MAP`, `classify_ship()` — D-scan ship classification data (v6.0) |

### `evealert/manager/`

| File | Purpose |
|------|---------|
| `alertmanager.py` | `AlertAgent` — the entire engine: vision tasks, alarm dispatch (sound/TTS/webhooks/push/automation), rearm logic (`_EnemySighting`), ESI augmentation + threat score, D-scan/intel/neighbor/wormhole/peak-hours task wiring, settings hot-reload |

### `evealert/ui/` (PySide6 — all widgets live here)

| File | Purpose |
|------|---------|
| `app.py` | `create_app()` / `run()` — QApplication, icon, loads `theme.qss` |
| `__main__.py` | `python -m evealert.ui` entry |
| `theme.py` | Color tokens (`BG`, `ACCENT`, `LOG_COLORS`, …) + `load_qss()` |
| `theme.qss` | The stylesheet; button variants via `class` dynamic property (`primary`/`secondary`/`danger`/`warning`) |
| `qt_bridge.py` | `QtBridge(QObject)` — UIBridge → Qt signals |
| `main_window.py` | `MainWindow` + `_MainProxy`; header/status, start/stop, log pane, hotkey routing (F1–F4), tray wiring, dialog launchers |
| `tray.py` | `AppTray(QSystemTrayIcon)` — Show/Start/Stop/Exit menu |
| `settings_dialog.py` | Registry-generated form (from `settings/fields.py`) + hand-built sections; profile bar; SSO login (`_LoginThread`) |
| `config_dialog.py` | Config Mode: selection guidance, region status, launches overlays |
| `region_overlay.py` | `RegionOverlay` — frameless fullscreen QRubberBand selector (devicePixelRatio-aware) |
| `statistics_window.py` | Live stats cards, history/sessions tables, Threat Heatmap tab |
| `image_manager.py` | Template image add/remove/preview (validates with `cv2.imread`) |
| `threshold_editor.py` | Per-image confidence override rows |
| `notification_wizard.py` | 4-page Telegram/Pushover/ntfy setup wizard with live test |
| `onboarding_wizard.py` | (planned, #164) first-run setup |

### `evealert/settings/`

| File | Purpose |
|------|---------|
| `store.py` | `SettingsStore` + `DEFAULT_SETTINGS` + `_get_by_path`/`_set_by_path`; singleton via `get_settings_store()` / `reset_settings_store()` (tests) |
| `fields.py` | `FieldSpec` registry (`FIELDS`, `TAB_ORDER`) driving the generated settings form |
| `helper.py` | `get_resource_path()` (PyInstaller-aware), `get_settings_path()`, user img path |
| `logger.py` | Rotating file logging setup |
| `stats_store.py` | Lifetime stats + per-session JSON reports (atomic writes) |
| `validator.py` | `ConfigValidator` static checks |

### `evealert/tools/`

| File | Purpose |
|------|---------|
| `vision.py` | OpenCV template matching (`TM_CCOEFF_NORMED`), per-image thresholds, unreadable-template guard |
| `windowscapture.py` | `mss` capture, lazy thread-affine init |
| `window_finder.py` | EVE client window detection |
| `http_common.py` | Canonical `USER_AGENT` / `DEFAULT_HEADERS` for ALL external HTTP |
| `zkillboard.py` | Kills-on-alarm lookup; `clean_zkb_entries()` (normalizes zKB `[null]`) |
| `esi_standings.py` | Public-ESI pilot intel + `extract_joining_characters()` |
| `esi_auth.py` | EVE SSO OAuth2 (PKCE + state + JWT identity); authed helpers: standings, fleet, structure fuel. Requires a user-registered client ID (32-hex) |
| `universe.py` | System cache, jump-graph BFS, route threat, sov map; `resolve_ids()` (`POST /universe/ids/`) is the ONLY name→ID resolver |
| `neighbor_monitor.py` | Adjacent-system kill polling |
| `dscan_watcher.py` | D-scan tail: threat tiers, ship classes, probes, cyno, signature-delta, `current_visible_types` |
| `intel_watcher.py` | Chat-log tail; raw-line + parsed-report callbacks |
| `intel_parser.py` | Free-text intel → `IntelReport` (system, count, clear, ships) |
| `kos_checker.py` | KOS APIs (CVA legacy/off by default) + local list; dead-source quarantine |
| `push_notifier.py` | Telegram / Pushover / ntfy |
| `wormhole.py` | Eve-Scout Thera connections, WH class, `WhDropDetector` |
| `fleet_context.py` | Fleet composition, TZ profile, killmail monitor (`_ZKB_SEMAPHORE` serializes zKB) |
| `threat_score.py` | `compute_threat_score()` → `ThreatAssessment` (1–10, CAUTION/HIGH/CRITICAL; cyno ⇒ 10) |
| `threat_heatmap.py` | Constellation 24-bucket kill histograms (session cache 1 h) |
| `space_profiles.py` | F3 presets (nullsec/wormhole/highsec) — settings overlays |
| `tts.py` | `is_tts_available()` / `speak()` (pyttsx3, daemon thread, serialized) |
| `ocr_local.py` | Optional Tesseract OCR of pilot names |
| `web_server.py` | Localhost dashboard + `/api/status`, `/api/log`, `/api/alarm/latest` |
| `plugin_loader.py` | User plugin discovery + hook dispatch |
| `net_safety.py` | SSRF/localhost guards for user-supplied URLs |
| `update_checker.py` | GitHub Releases version check |

---

## 5. Settings System

**`DEFAULT_SETTINGS` lives in `evealert/settings/store.py`** and is the schema
source of truth. Top-level blocks as of v6.1: `log_level`, `active_profile`,
`alert_region_1/2`, `faction_region_1/2`, `detectionscale`, `faction_scale`,
`cooldown_timer[_enemy|_faction]`, `volume`, `server`, `hotkeys`, `sounds`,
`profiles`, `image_thresholds`, `intelligence` (incl. peak-hours), `webhooks`,
`esi`, `threat_tiers`, `plugins`, `web_ui`, `adjacent`, `dscan`, `kos`,
`kos_list`, `push`, `notifications` (incl. TTS), `wormhole`, `fleet`,
`esi_oauth`, `ocr`, `diagnostics`, `alerts` (rearm), `automation`.
See [docs/FEATURES.md](docs/FEATURES.md) for block → module mapping.

- **Registry-driven UI:** most leaf settings are declared once as a `FieldSpec`
  in `evealert/settings/fields.py`; the settings dialog generates the widget,
  load, and save automatically. **Adding a simple setting = add a default in
  `store.py` + one `FieldSpec`.** Only structured settings (regions, lists,
  buttons) need hand-built dialog code.
- **Hot reload:** `store.save()` sets `store.changed = True`; `AlertAgent.run()`
  polls it each cycle and calls `load_settings()` — no restart needed.
- **Profiles:** `profiles` holds named override dicts; `store.load()` applies
  the `active_profile` overlay. ⚠ Until #156 lands, saving from the settings
  dialog while a profile is active bakes the overlay into base settings — see
  that issue before touching profile code.
- **Migration:** `SettingsStore._merge()` deep-fills missing keys from defaults
  and never lets an empty default wipe a populated user sub-dict.

### Storage locations (`platformdirs.user_config_dir("evealert")`)

| Item | Windows path |
|---|---|
| Settings | `%APPDATA%\evealert\settings.json` |
| Lifetime stats | `%APPDATA%\evealert\statistics.json` |
| Session reports | `%APPDATA%\evealert\sessions\` |
| SSO token (0600) | `%APPDATA%\evealert\esi_token.json` |
| User templates | `%APPDATA%\evealert\img\` |
| Plugins | `%APPDATA%\evealert\plugins\` |
| Logs | `%APPDATA%\evealert\logs\` |

---

## 6. Detection Pipeline

Each cycle (`VISION_SLEEP_INTERVAL = 0.1 s`): mss screenshot of the region →
`Vision.find()` template match (`TM_CCOEFF_NORMED`, per-image threshold
overrides, `cv.groupRectangles`) → `self.enemy` boolean → `run()` →
`_should_alarm_enemy()` dedup (position-quantized `_EnemySighting` records with
optional rearm, #100/#144) → `alarm_detection()`:

log → stats (+persist) → TTS → automation webhook POST → sound (≤3 plays then
cooldown) → Discord webhooks (all-events + per-type) → push → screenshot →
escalation counter → plugins → zKillboard kills → ESI augmentation
(joiner intel, KOS, standings/ally filter, WH-drop, fleet comp, threat score).

D-scan, intel channels, adjacent systems, Thera, sov, peak-hours, and killmail
monitors run as independent asyncio tasks started in `AlertAgent.start()` per
their settings toggles, all cancelled in `stop()`.

---

## 7. Resource Path Resolution

`get_resource_path(relative_path)` is the **only** correct way to locate
bundled assets (PyInstaller `sys._MEIPASS`-aware; strips a leading `evealert/`
in dev). PyInstaller `--add-data` targets must match what callers request:
`evealert/img;img`, `evealert/sound;sound`, and the Qt stylesheet
`evealert/ui/theme.qss;ui`. **Never** use `__file__`-relative asset paths.

---

## 8. Release Process

Push a tag `v*.*.*` — GitHub Actions `release.yml` builds the Windows exe:

```
pyinstaller --onefile --noconsole --name EVE-Alert
  --icon evealert/img/eve.ico
  --add-data "evealert/img;img"
  --add-data "evealert/sound;sound"
  --add-data "evealert/ui/theme.qss;ui"
  main.py
```

Local: `make build-windows`. (macOS build targets were retired at v5.)

---

## 9. Development Workflow

```bash
pip install -e ".[dev]"      # test deps (respx, pytest, PySide6, …)
pre-commit install
make check                    # lint + tests — run before every push
make test                     # pytest with coverage
```

GUI tests run headless with `QT_QPA_PLATFORM=offscreen`.

⚠ Until #159 lands, some tests write to the REAL user config dir — see that
issue for the isolation fixture spec.

---

## 10. Known Conventions

- **Detection threshold:** UI stores int 0–100; vision converts to `[0.1, 1.0]`.
- **Volume:** settings int 0–100 → engine float 0.0–1.0.
- **Sound playback:** `sd.play(...)` then `await loop.run_in_executor(None, sd.wait)`.
- **Log pane:** appends at bottom, 500-block cap, auto-scroll only when already
  at bottom; colors via tags `normal|green|red|yellow|cyan` mapped in
  `theme.LOG_COLORS`. Every line also mirrors to the web-server buffer.
- **External HTTP:** every `httpx.AsyncClient` gets
  `headers=http_common.DEFAULT_HEADERS` (merge for auth headers). zKillboard
  list responses must pass through `clean_zkb_entries()` (the API returns
  `[null]` for empty sets, and page size caps at ~200).
- **Name→ID resolution:** only via `universe.resolve_ids()`
  (`POST /universe/ids/`); the old ESI `/search/` endpoints are gone.
- **QSS variants:** `widget.setProperty("class", "danger")` then
  `style().unpolish(w); style().polish(w)` to restyle live widgets.
- **Platform conditionals:** `platform.system() == "Windows"`.
