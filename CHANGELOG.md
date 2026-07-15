# Changelog

## [6.3.24] 2026-07-15

### Fixed — OCR name detection root cause (#199)

- **WinRT OCR line structure.** `OcrResult.text` flattens the entire
  recognition result into ONE space-joined string with no newlines, so
  `parse_eve_names` (which splits on newlines) saw a single 120+-char token,
  failed the 3–37-char name regex, and returned zero names on every capture —
  while the OCR engine was reading every pilot perfectly. This was the actual
  cause behind the v6.3.6–v6.3.22 miss reports; the pixel-format fixes were
  necessary but not sufficient. `_winrt_recognize_async` now builds its output
  from `result.lines` (one pilot per line). Verified end-to-end on a real
  Windows machine against two live Local captures: 10/10 lines extracted,
  9 pilots resolved through ESI, full intel/threat analysis produced.
- **Icon glyphs misread as letter tokens.** EVE's standing icons frequently
  OCR as short letter tokens (`"S Naveia"`, `"(S DamonR"`), which the
  non-alphanumeric strip can't remove. `parse_eve_names` now also emits the
  remainder after a leading 1–2-char token; ESI exact-match resolution picks
  the right candidate and drops the wrong one silently.
- **Preprocessing retuned with measured data.** Accuracy matrix on a real
  capture: 3× upscale with no contrast boost scores 8/10 exact names vs 6/10
  for the previous 2×+invert+contrast pipeline — the contrast×2.0 step was
  actively harmful (5/10 at 3–4×), and inversion is a no-op for WinRT. New
  pipeline: grayscale → 3× LANCZOS → RGBA.
- **Python 3.13+ support.** `winsdk` stopped publishing wheels after
  Python 3.12 (source build fails without a C compiler). `ocr_local` now
  falls back to the maintained `winrt-*` namespace packages (same OS engine),
  declared in `pyproject.toml` for `python_version >= '3.13'`.

## [6.3.6] 2026-07-14

### Changed — Bundled OCR (Windows.Media.Ocr)

- **OCR no longer requires an external Tesseract installation.** On Windows 10
  1607+ (all modern Windows), the OCR engine is already part of the OS.
  `winsdk` (a small ~2 MB Microsoft-maintained package) is now a base
  dependency on Windows (`sys_platform == "win32"`) that provides Python
  bindings to `Windows.Media.Ocr`.

- `evealert/tools/ocr_local.py` rearchitected with two independent backends:
  1. **Windows.Media.Ocr** (priority) — `is_winrt_ocr_available()`,
     `_ocr_with_winrt(pil_img)`.  Runs recognition via an asyncio event loop
     isolated from the alert daemon loop to avoid conflicts.
  2. **pytesseract + Tesseract** (fallback) — `is_tesseract_available()`.
     Unchanged behaviour; requires `pip install ".[ocr]"` plus the Tesseract
     binary for users on non-Windows platforms or older Windows builds.

- Settings dialog **"Check Tesseract"** button renamed to **"Check OCR"**.
  Status messages now distinguish the active backend:
  - `✓ OCR ready (Windows.Media.Ocr — built-in)` — no install needed
  - `✓ OCR ready (Tesseract 5.x.y)` — Tesseract fallback active
  - `✗ OCR unavailable` — neither backend found

- `build-windows` extra no longer includes `pytesseract` (not needed in the
  bundled .exe).

## [6.3.0] 2026-07-13

### Added — UX & Onboarding (Epic v6.3)

- **#167 Log pane ergonomics** — `LogPane(QWidget)` replaces the bare
  `QPlainTextEdit`. New toolbar: All / Alarms / Intel / System category
  filter buttons (driven by message color tags), free-text search, and a
  Pause toggle (ring buffer of 2 000 entries keeps filling while paused; view
  re-renders on resume). Right-click context menu: Copy line / Copy all visible.
  `MainWindow.append_log` delegates to the pane; web-server mirroring unchanged.

- **#168 Tray icon state** — Three icon variants generated at runtime (grey =
  stopped, green = running, red = alarm flash) by compositing a status dot onto
  the base icon with `QPainter`. `QtBridge.alarm_fired` signal (new) emitted on
  every red log message; connected to `AppTray.on_alarm()` which flashes red for
  10 s then reverts. Tooltip updated: `EVE Alert — Running · last alarm HH:MM:SS`.
  New "Mute alarms" checkable tray menu item writes `server.mute` via
  `load_raw()/save()` and hot-reloads the engine.

- **#165 HotkeyEdit capture widget** — `HotkeyEdit(QPushButton)` enters capture
  mode on click, grabs the keyboard, converts the pressed Qt key to a
  pynput-compatible string, and rejects conflicting bindings with a tooltip
  warning. Replaces the two free-text QLineEdit hotkey fields in Settings;
  F3 (profile cycle) and F4 (status readout) now exposed as remappable bindings.
  `DEFAULT_HOTKEYS` expanded; `reload_hotkeys()` reads all four bindings.

- **#166 Profile manager UI** — `ProfileManagerDialog(QDialog)` shows a profile
  list (user profiles + read-only built-in space profiles) and a diff table
  (key / base value / profile value). Actions: New, Duplicate, Rename, Delete,
  Set Active, Remove override. "Save current settings as profile" opens a
  checkbox-list dialog so profiles stay minimal overlays. Settings dialog profile
  bar simplified to combo + "Manage Profiles…" button.

- **#164 First-run onboarding wizard** — `OnboardingWizardDialog` auto-shown on
  first launch (when alert region is unconfigured and
  `ui.onboarding_completed` is false). Four pages: Welcome (EVE window detect),
  Alert Region (RegionOverlay + live mss thumbnail), Sound & volume (test alarm),
  Done (start detection checkbox). Persists region + volume; marks
  `ui.onboarding_completed = True` on Finish or Skip.
  Re-launchable via **Settings → Detection → "Run Setup Wizard…"**.

---

## [6.2.0] 2026-07-13

### Fixed

- **#154 F1/F2 hotkey hijack** — hotkeys only fire when Config Mode dialog is
  `isVisible()`; after the dialog closes F1/F2 are silently ignored so EVE
  in-game fire-weapons/functions work normally.

- **#155 Space profiles crash** — `SettingsStore` gains a `set(path, value)`
  method and `save()` now accepts an optional dict (defaults to the in-memory
  cache). Wrong KOS key `kos.cva_kos_enabled` corrected to `kos.cva_enabled`
  in all three built-in profiles.

- **#156 Profile overlay baked into base settings** — `load()` now applies the
  profile overlay to a *copy* only; the internal cache always holds raw base
  values. New `load_raw()` method exposed for callers (Settings dialog) that
  need to read without overlay. Save and Apply both call `load_raw()` so profile
  values are never permanently written to disk.

- **#157 ESI client-ID placeholder** — replaced misleading "Leave blank for
  built-in public client" text with "Required — register a free app at
  developers.eveonline.com"; added a muted help label with app type, callback
  URL, and format requirements.

- **#158 Heatmap Qt thread violations** — replaced cross-thread widget writes
  and `QTimer.singleShot` (which crashes from non-Qt threads) with a `Signal(object)`.
  Worker emits the result dict (or Exception) to `_on_heatmap_ready` which runs
  safely on the main Qt thread.

- **#159 Tests writing to real statistics.json** — `get_stats_path()` now
  respects `EVEALERT_STATS_PATH` env var; both test classes set it to a
  temp-dir path in setUp and unset it in tearDown.

- **#160 Blocking Discord webhook** — all three `dhooks_lite.execute()` calls
  wrapped in `loop.run_in_executor(None, ...)` so the asyncio detection loop
  is not stalled during HTTP round-trips.

- **#161 Hotkey remaps need restart** — bindings stored on `self._hotkey_alert_key` /
  `self._hotkey_faction_key`; listener closure reads them on every keypress. New
  `reload_hotkeys()` called from Settings Save and Apply so new bindings take
  effect immediately without restarting.

- **#162 Qt shell polish** — removed accumulating dead `accepted.connect` signal
  on every `_open_settings()` call; `_save_and_apply` now calls `self.hide()`
  (Save closes); `_apply_only` saves without closing; `_build_tray` wrapped in
  try/except with `None` fallback; all three `self._tray` call sites guarded.

- **#163 Heatmap zKB caps at 200 kills** — `_zkb_kills_for_system` now paginates
  up to 5 pages (≈ 1 000 kills) stopping early when a page returns fewer than
  200 entries; 500 ms polite pause between pages.

---

## [6.1.0] 2026-07-13

### Added

- **#145 Wormhole signature delta monitoring** — `DscanWatcher` now counts
  "Cosmic Signature" entries on each D-scan poll and fires `on_new_signature(old, new)`
  when the count increases. Alertmanager logs a red warning: *"NEW SIGNATURE
  DETECTED: 1 new cosmic sig(s) (2 → 3) — possible wormhole connection!"*.
  Configurable via `dscan.alert_new_signatures` (default True).

- **#150 Ship cross-reference via zKillboard** — After the zKillboard profile
  line, if the pilot's `top_ship` matches any type currently visible on D-scan,
  a red `⚠ MATCH: <pilot> typically flies <ship> — that type is on D-scan NOW`
  line is logged. `DscanWatcher` now also tracks type-column values in
  `_visible_types`; exposed via `current_visible_types` property.

- **#152 F4 status readout hotkey** — Press **F4** to have EVE Alert speak the
  current threat situation aloud: local hostile count, highest-urgency D-scan
  class, adjacent system kills, and composite threat score. Degrades gracefully
  when TTS is unavailable. Also logs the summary to the log pane.

- **#153 EVE automation bridge** — When `automation.enabled = true` and
  `automation.webhook_url` is set, EVE Alert POSTs `{type, text, timestamp}`
  JSON to the configured URL on every alarm. AutoHotkey / PyAutoGUI scripts can
  listen on localhost and trigger an in-game keypress (safe-spot warp, dock).
  The built-in web server also exposes `GET /api/alarm/latest` for polling-based
  consumers.

- **#148 Constellation threat heatmap** — New `evealert/tools/threat_heatmap.py`
  with `get_constellation_heatmap(system, days=7)`. Resolves the constellation
  via ESI, fetches per-system kill history from zKillboard, and bins kills into
  24-bucket UTC histograms. Results are session-cached for 1 hour. Accessible
  from the new **Threat Heatmap** tab in the Statistics window.

- **#151 Historical threat pattern alerts** — `_peak_hours_monitor()` async task
  runs hourly and checks the upcoming hour's kill rate against the 7-day average
  for the configured constellation. When the rate is ≥ `peak_threshold_multiplier`
  (default 1.5×), a yellow warning fires 15 minutes before the hour. Configurable
  via `intelligence.peak_hours_warning` and `intelligence.peak_threshold_multiplier`.

- **#149 Mobile notification setup wizard** — New `NotificationWizardDialog`:
  a 4-page guided QDialog that walks through Telegram, Pushover, or ntfy.sh
  setup with inline registration instructions and a live test step before saving.
  Accessible via **Settings → Alerts & Sound → "Setup Mobile Notifications…"**.

---

## [6.0.0] 2026-07-13

### Added — AFK Situational Awareness

- **#140 D-scan ship class classification** — `ShipThreatClass` enum (TACKLE /
  DICTOR / FORCE_RECON / COVERT_OPS / CYNO / COMBAT / INDUSTRIAL / UNKNOWN)
  with per-class urgency weights. `classify_ship()` maps ship names/types via an
  ordered `SHIP_CLASS_MAP` list. `DscanEntry` gains a `threat_class` field; D-scan
  log lines now include human-readable labels such as
  `D-SCAN RED: Sabre [DICTOR — bubble incoming]`.

- **#139 TTS voice alerts** — optional text-to-speech readout of alarm details
  using `pyttsx3` (Windows SAPI5, no extra install on Windows). `speak()` runs on
  a daemon thread so the detection loop is never blocked. Settings: **Alerts &
  Sound → Text-to-Speech** — enable toggle, speech rate (50–400 wpm), and Check /
  Test buttons. Install with `pip install "evealert[tts]"`.

- **#141 Composite threat score** — `compute_threat_score()` aggregates up to
  five signals (local hostile count, KOS status, zKillboard danger ratio, D-scan
  ship class, adjacent system kills) into a 1–10 score with a
  `CAUTION / HIGH / CRITICAL` label and reasoning list. Cynosural-field detection
  always returns 10 / CRITICAL. Logged after the ESI intel block on every Enemy
  alarm.

- **#144 Per-enemy re-alert after sustained presence** — a new
  `alerts.rearm_minutes` setting (0 = disabled) re-arms the alarm for a pilot
  who has been continuously present in local beyond the configured time window.
  Replaces the previous bare-float `_seen_enemies` dict with a `_EnemySighting`
  record that tracks `first_seen`, `last_alarm`, and `rearm_at`.

- **#143 Pre-configured space profiles + F3 hotkey** — three built-in profiles
  (Null-sec, Wormhole, High-sec) write a coordinated set of settings overrides in
  one call and reload the agent without restart. Press **F3** to cycle through
  profiles while the overlay is running; current profile is logged in cyan.
  Profiles tune D-scan alerts, escalation threshold, TTS, zKillboard, KOS, and
  re-alert interval.

- **#142 Intel channel improvements** — `intel_parser.py` parses free-text intel
  channel messages into `IntelReport` objects (system name, hostile count, clear
  signal, ship mentions). `IntelWatcher` gains an `on_intel(IntelReport)` callback
  that fires alongside the existing raw-line callback. Hostile reports are logged
  as `Intel: N hostile(s) in SYSTEM [ships]` in red; clear signals as
  `Intel: SYSTEM CLEAR` in green. When a home system is configured, an async ESI
  route lookup appends the jump count (`Intel: D7-ZAC is 3 jumps from 1DQ1-A`).

- **#146 Cynosural field detection** — when a cynosural field object or cyno ship
  appears on D-scan, an immediate CRITICAL alarm fires:
  `⚠ CYNO DETECTED: <name> — CAPITAL DROP IMMINENT — LEAVE NOW`. Bypasses the
  normal cooldown so each re-light triggers a fresh alarm. Also announces via TTS
  when TTS is enabled.

- **#147 Standings-aware local monitoring** — new
  `esi_oauth.standings_filter_blues` setting. When enabled alongside ESI OAuth,
  pilots with a personal standing ≥ +5.0 are labelled `[ALLY]` in green and
  excluded from KOS checks, threat-score counting, and hostile display — reducing
  noise in mixed-fleet space.

---

## [5.0.1] 2026-07-13

### Fixed

- **#132** — zKillboard revoked the `limit/` URL modifier; kills-on-alarm intel was completely broken. Removed the `/limit/{n}/` path segment; client-side slicing is unchanged.
- **#133** — zKillboard returns `[null]` for empty result sets. Added `clean_zkb_entries()` helper that normalises `[null]` → `[]`; applied at all 4 affected sites: `neighbor_monitor._kills_15min`, `universe._zkb_kills_last_hour`, `zkillboard._fetch_kills`, and `fleet_context._zkb_get`. Phantom adjacent-monitor alerts and false-positive route-threat warnings are gone.
- **#134** — zKillboard `topLists` category key is `"shipType"`, not `"ship"`. Top-ship intel line now resolves correctly. Kill/loss field names renamed from `kills_30d`/`losses_30d` to `kills_total`/`losses_total` (data is all-time, not 30-day); display strings updated to say `(all-time)`. `dangerRatio` read from zKB's own field when available.
- **#135** — CVA KOS domain (`kos.cva-eve.com`) is offline. `cva_enabled` now defaults to `False` in both `KosChecker` and `DEFAULT_SETTINGS`. A new `_dead_sources` set disables any KOS source that raises a connection error for the rest of the session (one warning log, no repeated attempts).
- **#136** — EVE SSO login would open the browser and hang 120 s with a fake placeholder client ID (`evealert_public_client`). `_DEFAULT_CLIENT_ID` is now `""`. `EsiAuth.login()` validates that the client ID is a 32-character lowercase hex string (the format issued by developers.eveonline.com) before opening the browser; blank or malformed IDs return `False` immediately with a descriptive log message.
- **#137** — HTTP User-Agent strings were stale, mismatched across modules, and missing entirely from several `httpx.AsyncClient` calls. Introduced `evealert/tools/http_common.py` with a canonical `USER_AGENT` and `DEFAULT_HEADERS`; applied to all `AsyncClient` constructions in `zkillboard`, `universe`, `esi_standings`, `fleet_context`, `wormhole`, `esi_auth`, and `neighbor_monitor`.
- **#138** — EVE SSO login in the Settings dialog crashed with `cannot import name 'ESIAuth'` (class is `EsiAuth`), then `load_token()` (method does not exist), then `start_oauth_flow()` (method does not exist, and `login()` is async). Fixed: uses `get_esi_auth()` factory, reads `auth.is_authenticated` / `auth.character_name` properties, and runs `asyncio.run(auth.login())` in a `_LoginThread(QThread)` so Qt's event loop is not blocked.

## [5.0.0] 2026-07-13

### Changed — UI completely rewritten (PySide6 migration, #123–#131)

- **Replaced customtkinter/Tkinter with PySide6 (Qt 6, LGPL)** — the entire
  presentation layer is rebuilt from scratch under `evealert/ui/`.  The detection
  engine (`evealert/manager/`, `evealert/tools/`) is unchanged.
- **New dark-themed UI** — 510-line QSS stylesheet; consistent color palette with
  primary/danger/warning button variants, stat cards, monospace log pane.
- **Main window** — status indicator (● Running / ● Stopped), Start/Stop/Exit row,
  Config Mode / Settings / Statistics buttons, region toggle buttons, scrollable
  colored log pane, system tray with Start/Stop/Show/Exit menu.
- **Settings dialog** — tabbed, scrollable, resizable (no more 1200 px fixed
  height overflow).  42 settings auto-generated from the field registry + all
  non-registry sections (regions, thresholds, sounds, webhooks, hotkeys, ESI
  OAuth).  All settings sections reachable on a 1080p display (#107).
- **Config mode** — fullscreen translucent QRubberBand drag-to-select overlay for
  Alert and Faction regions (replaces the previous guide-only dialog).  F1/F2
  hotkeys trigger the overlay directly; HiDPI scaling handled via
  `devicePixelRatio()`.
- **Statistics window** — 3×2 stat cards + sortable `QTableWidget` history; Sessions
  tab with View / Export CSV / Delete.
- **Image manager** — thumbnail list with 32×32 QIcon previews, 240×240 preview
  pane, Add (with cv2 validation) / Remove with live template reload.
- **Per-image threshold editor** — scrollable slider rows, per-image override or
  Clear to global.
- **System tray** — `QSystemTrayIcon` (replaces pystray daemon thread); double-click
  to restore, minimize-to-tray on close.
- **Engine/GUI decoupling** — `SettingsStore` owns all JSON persistence; `UIBridge`
  protocol routes all engine→UI calls through Qt signals (no `after(0, ...)`)
  (#124).

### Removed
- `customtkinter`, `pystray`, `screeninfo` dependencies removed from
  `pyproject.toml`.
- `evealert/menu/` package deleted (setting.py, main.py, config.py, statistics.py,
  image_manager.py, threshold_editor.py).
- `evealert/tray.py` (pystray) and `evealert/tools/overlay.py` (Tk marquee) deleted.

### Added
- `evealert/settings/store.py` — `SettingsStore` with atomic save, `changed` flag,
  dotted-path `get()`, and `DEFAULT_SETTINGS` (moved from setting.py).
- `evealert/settings/fields.py` — `FieldSpec` namedtuple, `FIELDS` registry (42
  entries), `TAB_ORDER`, `apply_registry_fields()` / `save_registry_fields()`.
- `evealert/bridge.py` — `UIBridge` protocol (toolkit-agnostic engine/GUI contract).
- `evealert/ui/` — Qt UI package: `app.py`, `theme.py` + `theme.qss`, `main_window.py`,
  `qt_bridge.py`, `tray.py`, `settings_dialog.py`, `config_dialog.py`,
  `region_overlay.py`, `statistics_window.py`, `image_manager.py`,
  `threshold_editor.py`.

## [4.2.0] 2026-07-12

### Added
- **Diagnostic mode** — new **Alerts & Sound → Diagnostics** settings section with:
  - "Enable diagnostic (verbose) logging" toggle: raises all app loggers to DEBUG for the duration of a session, capturing full call-path detail in the log files.
  - **Log Level** dropdown (Debug/Info/Warning/Error): surfaces the previously-hidden `log_level` setting.
  - **Export Diagnostics Bundle** button: packages all log files + a secrets-redacted copy of your settings + a system/environment info snapshot into a single `eve-alert-diagnostics-<timestamp>.zip` in the config directory, then reveals the file in your OS file manager.
  - Log path label showing where logs are stored.
- `EVEALERT_DEBUG=1` environment variable: enables verbose DEBUG logging before the UI loads (useful for diagnosing crashes at startup).
- `evealert/settings/diagnostics.py`: `gather_context()` (app version, OS, Python, monitors, EVE chatlog dir detection, OCR/Tesseract availability, feature flags), `_redact_settings()` (blanks push tokens, OAuth client ID, webhook URLs), `create_bundle()` (creates the export zip).

## [4.1.0] 2026-07-12

### Added
- **OCR pilot-name detection (#98)** — optionally reads pilot names from a configured Local-chat screen region on each Enemy alarm (via Tesseract/pytesseract) and merges them into the existing KOS / ESI / zKillboard intel pipeline. Off by default and import-guarded: degrades to a no-op with a log message when the Tesseract engine is not installed. New settings under **Intel & ESI → OCR Name Detection** (enable toggle + capture region x1/y1/x2/y2). Requires installing the Tesseract OCR engine separately.

### Fixed / Changed (post-4.0 hardening)
- Settings save no longer wipes saved profiles / per-image thresholds / active profile (#99, #108); settings UI writes now round-trip.
- Settings window rearchitected into a tabbed, scrollable layout with a persistent Save/Apply/Close footer and a declarative field registry (#107).
- ESI name→ID resolution migrated to `POST /universe/ids/` (removed public `/search/` endpoints) — restores zKillboard/pilot-intel/adjacent/route/sov features (#110).
- EVE SSO OAuth now works: PKCE, corp-structures scope, single-client structure fetch, JWT-based character identity, and per-login state validation (#104, #115, #105).
- External-API integrations fixed against real response shapes: Eve-Scout v2, embedded ZKB attackers, kills+losses feeds, honest WH class, KOS corp/alliance checks, D-scan UTF-16/type-column parsing (#101).
- Vision robustness: skips unreadable template images, correct debug window name, guarded error path (#111, #112, #113).
- Duplicate enemy alarms deduped by quantized position (#100); asyncio monitors cancelled cleanly on stop (#102); settings hot-reload no longer mutates Tk from the alert thread (#114); web dashboard HTML renders (#109); credential/SSRF hardening (#105); robustness polish incl. rate limiting and bounded caches (#106).
- Test suite expanded to 237 tests covering the v3.3–v4.1 modules (#103).

## [4.0.0] 2026-07-11

### Added
- **v3.3**: D-scan log watcher — tails EVE D-scan files, classifies ships into RED/ORANGE/YELLOW/GREEN threat tiers, fires probe detection alarm, maintains session timeline
- **v3.4**: KOS checker — auto-queries CVA KOS API and any configured custom KOS endpoints per pilot; local hostile list matching
- **v3.5**: Push notifications — Telegram Bot, Pushover, and ntfy.sh channels; auto-screenshot on alarm; alarm escalation counter
- **v3.6**: Wormhole awareness — Thera connection monitor (Eve-Scout API), WH static type inference, WH fleet drop heuristic
- **v3.7**: Fleet context — hostile fleet composition analysis, timezone activity profiling, killmail notifications for tracked characters
- **v4.0**: EVE SSO OAuth2 login — full authorization code flow via local callback server; access/refresh token lifecycle with auto-refresh; personal standings auto-classify in Local; fleet membership display on start; structure fuel-expiry warnings; standings-based color coding in pilot intel display



### Added
- **Neighboring system kill monitor** (#73) — Optional async background task polls Zkillboard every 2 minutes for kills in systems within a configurable jump radius (1–5). Per-system 10-minute cooldown prevents alert spam. Posts: `"Adjacent: N kill(s) in [System] (X jumps away)"`.
- **Route threat assessment** (#74) — "Check Route" button in Settings triggers a BFS path from the current system to a configured destination, checks each hop for kill activity (last hour via Zkillboard), and posts a summary with `[danger]`/`[caution]`/`safe` classification per hop.
- **Pipe/pocket detection** (#75) — On detection start, posts system type based on gate count: `"dead-end"` (1 gate), `"pipe"` (2 gates), `"crossroads"` (3+ gates). Helps assess whether incoming neutrals are through-traffic or specifically targeting you.
- **Sovereignty display** (#76) — On start, fetches the current system's sovereignty holder from the ESI bulk sov map and posts: `"Sov: Alliance [Ticker] — IHub: active | TCU: active"`. Re-polls every 5 minutes and posts a yellow `SOV CHANGE` alert if the controlling alliance changes.

### Changed
- `DEFAULT_SETTINGS` gains an `adjacent` block: `enabled`, `max_jumps`, `poll_interval`, `min_kills`, `destination_system`.
- Settings window height 1050 → 1200. New "Adjacent System Monitor" section with enable checkbox, max-jumps/min-kills/poll-interval entries, destination system field, and "Check Route" button.
- `AlertAgent.start()` now creates `_display_system_info()` (one-shot) and `_sov_monitor()` (background poll) tasks automatically.

### New files
- `evealert/tools/universe.py` — `UniverseCache` with BFS jump-graph, system ID/name resolution, gate counting, sovereignty lookup, route threat assessment; `SovInfo` and `RouteLeg` namedtuples
- `evealert/tools/neighbor_monitor.py` — `NeighborMonitor` async poll loop

## [3.1.0] 2026-07-11

### Added
- **Pilot background check** (#69) — ESI lookups now include character age (days since creation), total corps held (from corp history), and a cyno-alt heuristic: pilots < 30 days old trigger a "YOUNG PILOT — possible cyno/scout alt" warning.
- **Kill/death profile** (#70) — Zkillboard stats endpoint queried per pilot: 30-day kills, losses, danger ratio %, and top ship type posted below each pilot's corp/alliance line.
- **Alliance threat tier** (#71) — New "Threat Tiers" section in Settings. Add name/corp/alliance substrings mapped to red / orange / yellow tiers. Matched pilots are prefixed `⚠ [KOS-RED]`, `⚠ [HOSTILE]`, or `[CAUTION]`, and their log line is coloured accordingly.
- **Flashy security status alert** (#72) — New "Alert on flashy pilots (sec ≤ -5)" checkbox in Settings > ESI Augmentation. When enabled, pilots with security status ≤ -5.0 trigger a distinct red log line: "FLASHY: Name (sec: -7.2) — attackable in low-sec".

### Changed
- `CharacterInfo` NamedTuple extended with `age_days`, `security_status`, `corp_history_count`.
- `EsiLookup._fetch_character()` now makes an additional ESI call to `/v2/characters/{id}/corporationhistory/` to populate `corp_history_count`.
- `_augment_with_esi()` in `AlertAgent` fully rewritten to format all pilot intelligence into structured per-pilot log output.
- `DEFAULT_SETTINGS["esi"]` gains `alert_flashy: false`.
- `DEFAULT_SETTINGS` gains `threat_tiers: {}`.
- Settings window height 900 → 1050.

### New types/methods
- `KillProfile` NamedTuple: `kills_30d`, `losses_30d`, `top_ship`, `danger_ratio`
- `EsiLookup.get_zkillboard_profile(character_id)` — cached Zkillboard stats fetch
- `_compute_age_days(birthday_str)` — ISO-8601 → age in days helper

## [3.0.0] 2026-07-11

### Added
- **ESI augmentation** — When an Enemy alarm fires and ESI is enabled in Settings, a background task reads the Local chat log, extracts the names of recently joined characters, and looks them up via public ESI endpoints (no OAuth required). Corporation name and alliance name are posted to the log pane in cyan alongside the alarm. Configurable: separate toggles for show-corp and show-alliance. New `settings.json["esi"]` block.
- **Plugin system** — Drop any `.py` file into `~/.config/evealert/plugins/` to extend EVE Alert without modifying the core. Plugins may define `on_start()`, `on_stop()`, `on_enemy(system, timestamp)`, `on_faction(system, timestamp)`, and `on_intel(line)` hooks. Hooks run in a thread-pool executor so plugin errors are isolated. On startup the number of loaded plugins is shown in the log pane. New `settings.json["plugins"]` block.
- **Web status UI** — Optional local HTTP server (no extra dependencies) that serves a self-refreshing status dashboard at `http://127.0.0.1:<port>/`. Also exposes `GET /api/status` and `GET /api/log` JSON endpoints. Enabled via a new "Web Status UI" section in Settings (checkbox + port entry). New `settings.json["web_ui"]` block.
- `evealert/settings/helper.py`: `get_user_plugins_path()` — returns `~/.config/evealert/plugins/`, creating it on first use.

### Changed
- Settings window height increased from 720 to 900 to accommodate three new sections (ESI, Web UI).
- `DEFAULT_SETTINGS` gains: `esi`, `plugins`, `web_ui` blocks.
- `AlertAgent.stop()` now also stops the web server and calls `on_stop` plugin hook.
- `MainMenu.write_message()` mirrors every log line to the web server's in-memory circular buffer so the dashboard stays current.

### New files
- `evealert/tools/esi_standings.py` — `EsiLookup` async client + `CharacterInfo` namedtuple + `extract_joining_characters()` log parser
- `evealert/tools/plugin_loader.py` — `PluginManager` discovery/dispatch + `get_plugin_manager()` singleton
- `evealert/tools/web_server.py` — `WebStatusServer` async HTTP server + `append_to_log_buffer()`

## [2.6.0] 2026-07-11

### Added
- **Per-type sound cooldown** — separate cooldown timers for Enemy and Faction alarms. `cooldown_timer_enemy` and `cooldown_timer_faction` fields added to `settings.json`. Both default to 60 s. Configured via two new entry rows in Settings.
- **Custom webhook message template** — the Discord notification message is now a user-editable template stored in `settings.json["server"]["webhook_template"]`. Supported variables: `{alarm_type}`, `{system}`, `{time}`, `{count}`. Configurable via a new "Msg Template:" entry row in Settings.
- **Multiple webhook targets** — in addition to the existing "all events" webhook, users can now configure dedicated URLs for Enemy alarms and Faction alarms independently via new "Enemy Webhook / Faction Webhook" rows in Settings. Each target also supports a `min_count` threshold so the webhook only fires after a configurable number of session alarms of that type have occurred.
- **Startup version check** — on each detection start, an async background request to the GitHub Releases API compares the installed version against the latest release. If a newer version is available, a yellow message with the release URL is shown in the log pane. Completely non-blocking; silently suppressed if offline.

### Changed
- `DEFAULT_SETTINGS` gains: `cooldown_timer_enemy`, `cooldown_timer_faction`, `server.webhook_template`, `webhooks` block.
- Settings window height increased from 560 to 720 to accommodate the new rows.
- `AlertAgent.play_sound()` now uses per-type cooldown instead of a single shared value.
- `AlertAgent.send_webhook_message()` now formats the message using the template and dispatches to all configured targets (all-events + per-type), replacing the hardcoded `"Enemy Appears in {system}!"` string.
- Webhook reset message on alarm clear now uses the system name without the hardcoded "Alarm Reset:" prefix.

### New files
- `evealert/tools/update_checker.py` — `check_for_update()` async GitHub Releases version comparison

## [2.5.0] 2026-07-11

### Added
- **Stats persistence** — lifetime alarm totals (`total_alarms`, `total_by_type`) now survive application restarts. Stored atomically in the platformdirs config directory as `statistics.json`. Loaded back into `AlarmStatistics` on startup via `load_lifetime()`. Saved after every alarm and on clean stop.
- **Per-session reports** — each detection run is saved as `session_YYYYMMDD_HHMMSS.json` in a `sessions/` sub-directory alongside `settings.json`. Reports include start/end time, duration, alarm counts by type, and the full event history.
- **Statistics Sessions tab** — the Statistics window now has two tabs: "Live Stats" (the existing real-time view) and "Sessions" (a scrollable list of past session JSON files). Each session row has a View button (shows details in a text pane below the list) and a red Delete button. An "Open Folder" button opens the sessions directory in the OS file manager.
- **Zkillboard kill intelligence** — when "Enable Zkillboard lookup on alarm" is checked in Settings, the first Enemy alarm in a configurable cooldown window (default 5 min) triggers an async ESI + Zkillboard lookup for the configured system name. The top 3 recent kills (victim name, ship, ISK value, time) are posted to the log pane in yellow.
- **Intel channel log watcher** — when "Watch EVE intel chat log" is enabled in Settings, a background task tails the most-recently-modified EVE chat log whose filename contains the configured channel name (e.g. "Intel"). New chat lines are posted to the log pane in cyan in real-time as they appear.
- Intelligence section in the Settings window with two checkboxes (Zkillboard, Intel log) and an Intel Channel Name text field.
- `cyan` and `yellow` log colours registered in the main log textbox.

### Changed
- `DEFAULT_SETTINGS` gains an `intelligence` block: `zkillboard_enabled`, `zkillboard_cooldown`, `intel_log_enabled`, `intel_log_channel`.
- Statistics window geometry increased to 520×600 to accommodate the tabbed layout.
- `AlertAgent.stop()` now saves lifetime stats and a session report before shutting down.
- `AlertAgent.load_settings()` reads `intelligence` settings and sets internal flags.

### New files
- `evealert/settings/stats_store.py` — `load_lifetime_stats()`, `save_lifetime_stats()`, `save_session_report()`, `list_session_reports()`
- `evealert/tools/zkillboard.py` — `ZkillboardClient` with ESI system lookup + Zkillboard kill fetch; module-level `get_client()` singleton
- `evealert/tools/intel_watcher.py` — `IntelWatcher` async tail loop + `get_eve_chatlog_dir()` / `find_intel_log()` helpers

## [2.4.0] 2026-07-11

### Added
- **Named detection profiles** — save and load named snapshots of all detection settings (regions, thresholds, cooldown, webhook, hotkeys, sounds). Profile selector at the top of the Settings window with Save, New, Load, and Delete buttons. Profiles stored in `settings.json` under the `profiles` key.
- **Custom sound library** — browse for any WAV file to use as the enemy alarm or faction alarm. "Browse Alarm..." and "Browse Faction..." buttons in Settings. Custom sound paths stored in `settings.json["sounds"]`; automatically falls back to bundled sounds if the file is missing.
- **Per-image threshold control** — override the global detection threshold for individual template images. "Per-Image Thresholds..." button opens a modal editor with a toggle + slider per template. Stored in `settings.json["image_thresholds"]` as `{basename: int_or_null}`. `null` means use the global `detectionscale` value.
- **Image management UI** — "Image Manager" button in Config Mode. Add custom template images (copied to the platformdirs user `img/` directory), remove user-added images, reload the detection engine without restarting. Bundled images shown read-only.
- `get_user_img_path()` in `evealert/settings/helper.py` — returns the writable user image directory alongside `settings.json`; created automatically on first use.
- `_load_image_files()` now scans both the bundled `img/` directory and the user `img/` directory.

### Changed
- `DEFAULT_SETTINGS` gains: `active_profile`, `profiles`, `sounds`, `image_thresholds`
- `Vision.__init__` now stores `needle_paths` for per-image threshold filename lookup
- `Vision.vision_process()` accepts `per_image_thresholds` dict to override threshold per template
- `Vision.find()` and `find_faction()` accept `per_image_thresholds` and pass it through
- `AlertAgent.load_settings()` reads `sounds` and `image_thresholds` from settings
- `AlertAgent.run()` now passes `self.image_thresholds` to `find()` and `find_faction()`
- Settings window height increased from 400 to 560 to accommodate new rows
- `alertmanager.py` uses `self._alarm_sound` / `self._faction_sound` instance attributes instead of module-level constants for alarm playback

### New files
- `evealert/menu/image_manager.py` — `ImageManagerWindow`
- `evealert/menu/threshold_editor.py` — `ThresholdEditorWindow`

## [2.3.0] 2026-07-11

### Added
- **System tray** — EVE Alert now minimizes to the system tray instead of closing. The X button hides the window; the tray icon provides Show, Start Detection, Stop Detection, and Exit menu items. Requires `pystray>=0.19` (bundled in releases).
- **Auto-detect EVE window** — "Detect EVE Window" button in Config Mode finds the running EVE Online client and pre-fills both region coordinates with the full window bounds. Supported on Windows (`pygetwindow`) and macOS (`osascript`). Regions can still be refined with F1/F2.
- **Configurable hotkeys** — Alert Region and Faction Region keys are now configurable in Settings. Defaults remain F1/F2. Enter any key name (e.g. `f3`, `g`, `home`) and click Save. ESC remains hardcoded for aborting region selection.
- **Config popup screen clamping** — Config Mode and Settings windows no longer open partially off-screen when the main window is near the display edge.
- **Lazy window creation** — Config Mode and Settings windows are now created on first open rather than at startup, eliminating the macOS window flash on launch.

### Changed
- `pyproject.toml`: Added `pystray>=0.19` as a runtime dependency; added `[windows]` optional extra for `pygetwindow>=0.0.9`
- Release pipeline: Windows build now installs `.[build-windows,windows]` to bundle `pygetwindow`
- `DEFAULT_SETTINGS` now includes `hotkeys` section: `{"alert_region": "f1", "faction_region": "f2"}`

## [2.2.0] 2026-07-11

### Fixed (Critical)
- Thread safety: All Tkinter widget mutations from the alert daemon thread now go through `self._ui()` / `self.main.after(0, ...)` — prevents non-deterministic crashes on Windows and macOS
- OpenCV debug window (`cv.imshow`) calls moved out of the background thread path; `detection_image` variable is now always bound before the debug check
- Non-atomic settings write replaced with write-to-temp + `os.replace()` — crash mid-write no longer corrupts `settings.json`
- `os.listdir()` for template images moved from module import time into `AlertAgent.__init__()` with try/except — missing `img/` directory now shows a user-facing error instead of crashing before the UI opens
- `AlertAgent` coordinates (`x1`, `y1`, `x2`, `y2`) initialized to 0 in `__init__` — no more `AttributeError` if validation fails early
- `stop()` now guards against `self.loop is None` — safe to call before `start()`
- `vision_faction_thread` now resets `self.faction = False` on screenshot failure — prevents indefinite alarm loop

### Fixed (High)
- `run()` now catches `Exception` broadly — silent loop death on non-ValueError exceptions prevented
- Faction screenshot failure now resets stale `True` state
- Windows overlay region coordinates corrected: `_x_offset` cached at `create_overlay` time and applied consistently in `on_button_release`
- Vision detection threshold default changed from float `0.5` (→ 0.005 after /100 → clamped to 0.1) to int `50` (→ 0.50) — correct behavior
- `StatisticsWindow`: only one instance allowed at a time; re-clicking focuses existing window
- Double-start race: `self.alert.running = True` set before thread launch; Start button disabled on click
- `DEFAULT_COOLDOWN_TIMER` (constants.py) used as `cooldown_timer` default in `DEFAULT_SETTINGS` — single source of truth
- macOS release CI job now correctly installs `.[build-macos]` (was `.[build-windows]`)
- Release pipeline now requires `tests` workflow to pass before building binaries
- `asyncio.get_event_loop()` → `asyncio.get_running_loop()` (deprecated in 3.10+)

### Fixed (Medium/Low)
- `load_settings()` exception catch broadened from `FileNotFoundError` to `OSError`
- `save_settings()` wrapped in `try/except OSError`; success message only shown on confirmed write
- `write_message()` calls in pynput keyboard listener thread marshalled via `self.after(0, ...)`
- Settings validator now validates `volume` (0–100) and `log_level` (known level names)
- German UI labels `X-Achse`/`Y-Achse` changed to `X-Axis`/`Y-Axis`
- `setup_mac.py` reads `__version__` dynamically from `evealert/__init__.py`
- Typos fixed: `factiom_vision_opened` → `faction_vision_opened`, `detection_treshhold` → `detection_threshold`, `vison_t` → `vision_t`
- Dead code removed: `enemy == "Error"` check (Vision never returns a string), stale `needle_img`/`needle_w`/`needle_h` class attrs, module-level `now = datetime.now()`
- Redundant `f"{alarm_text}"` replaced with `alarm_text`
- `on_mouse_drag` guarded against `self.rect is None` during concurrent `clean_up()`
- Overlay monitor cached at `create_overlay` time for correct multi-monitor coordinate math
- Haystack normalisation moved outside the per-template loop (computed once per frame)

## [2.1.1] 2026-07-11

### Fixed
- Resource files (`img/`, `sound/`) not found on Windows when running the PyInstaller `--onefile` build. Root cause: `get_resource_path()` was looking in `Path(sys.executable).parent` (the folder next to the `.exe`) instead of `sys._MEIPASS` (the temp directory where PyInstaller extracts bundled assets at runtime).
- Dead code in `helper.py`: the development-mode path fallback block was stranded after an unreachable `return`, causing `get_resource_path()` to return `None` in development mode.

## [2.1.0] 2026-07-11

### Added
- macOS support: platform-conditional window icon (`iconphoto` + PNG on macOS, `iconbitmap` + ICO on Windows)
- `eve.png` icon generated from `eve.ico` for macOS/Linux
- GitHub Actions release pipeline: automated Windows `.exe` (PyInstaller) and macOS `.dmg` (py2app) builds triggered on `v*` tags
- `setup_mac.py` py2app configuration for macOS app bundle
- Local CI runner via Makefile (`make check`, `make test`, `make lint`, `make build-windows`, `make build-macos`)
- Platform-appropriate settings storage via `platformdirs` (`%APPDATA%\evealert` on Windows, `~/Library/Application Support/evealert` on macOS)

### Fixed
- asyncio event loop created in background thread using `new_event_loop()` instead of `get_event_loop()` — prevents incorrect loop reuse on Python 3.10+
- Removed permanently-held asyncio lock in `run()` that would deadlock on any restart attempt
- Audio playback moved to `run_in_executor` so vision detection is no longer paused during alarm sounds
- Settings schema unified: `server.webhook` key used consistently everywhere (validator now correctly validates the webhook URL)
- Log level key unified: `log_level` used in both settings file and logger (changing log level in UI now takes effect)
- `load_settings()` no longer writes to disk on every read — only explicit saves write the file
- `iconbitmap()` crash on macOS replaced with platform-conditional `iconphoto()`
- Status icon garbage-collection bug: `check_status()` was storing `self.offline` instead of `self.online`
- Platform-conditional pixel offsets in `overlay.py`: `+30` Y and `-10` X corrections now only applied on Windows
- `sounddevice` import wrapped in `try/except OSError` with clear PortAudio install instructions for macOS users
- mss instance reused across screen captures (was opened/closed 20×/second)
- Alarm trigger latency reduced from 2–3 seconds to ~200 ms
- Log textbox capped at 200 lines to prevent unbounded growth and UI slowdown
- TOML section scoping bug in `pyproject.toml` that caused `pip install` to fail
- Two stale tests updated to reflect intentional code changes

### Changed
- Removed `pyautogui` dependency — replaced with `pynput.mouse.Controller` (already required)
- Removed `CTkMessagebox` dependency — replaced with stdlib `tkinter.messagebox`
- Moved `pyinstaller` from runtime to `[project.optional-dependencies].build-windows`
- Added `py2app` as `[project.optional-dependencies].build-macos`
- Added `platformdirs>=4.0` as a runtime dependency
- Pinned `dhooks-lite>=0.2`
- Removed duplicate `requirements.txt` — `pyproject.toml` is now the single source of truth
- Updated project URLs to `github.com/bluhayz/EVE-Alert`
- German log messages replaced with English
- Mouse position label text changed from "Mausposition" to "Mouse Position"

## [In Development] - Unreleased

<!--
Section Order:

### Added
### Fixed
### Changed
### Removed
-->

## [2.0.2] 2026-01-03

## Added
- Makefile System

### Changed
- removed negative error handler in some cases it needs to be negative see [#51](https://github.com/Geuthur/EVE-Alert/issues/51)

## [2.0.1] 2025-11-24

### Fixed

- **Resource loading:** Completely rewritten `get_resource_path()` — the application now always reads resources from the running executable. This ensures `img/` and `sound/` are consistently loaded in both development and distribution builds.
- **Sound handling:** Corrected `SOUND_FOLDER` in `evealert/constants.py` to `sound`, so custom WAV files like `alarm.wav` are properly located and played.

## [2.0.0] 2025-11-22

Big Thanks to [@Gotarr](https://github.com/Gotarr) for improving the whole EVE-Alert System with many QoL changes, fixes, optimations

### Added

- Codecov Report
- Discord Badge
- Release Badge
- Licence Badge
- Complete test infrastructure (pytest, pytest-asyncio, pytest-cov, pre-commit)
- 57 new unit and integration tests (test coverage: 65%)
- Real-time statistics and history system (alarm counter, session tracking, export to CSV/JSON)
- Configuration validation for all settings (regions, scales, timers, webhooks, audio)
- Central management of all constants (constants.py)
- Developer documentation and AI agent instructions (copilot-instructions.md)
- Sprint summaries and improvement plan in the docs folder

### Changed

- Updated Codecov action from `v4` to `v5`
- Refactoring: Exception hierarchy, import organization, code formatting (Black, isort)
- Type hints and docstrings for all modules
- Logging system with rotating file handler and module loggers
- Settings can now be changed at runtime (without restarting)
- Audio system: mono/stereo conversion, error handling, test buttons in the UI
- CI/CD: automated tests and coverage checks via GitHub Actions

### Fixed

- Various bugs in settings, vision, overlay, and audio
- Improved error and validation messages

### Removed

- Unnecessary and duplicate code sections
- Obsolete socket functions

## [1.0.1] 2025-06-12

### Fixed

- [#32](https://github.com/Geuthur/EVE-Alert-Opensource/issues/32)

## [1.0.0] 2025-03-30

### Changed

- Removed Socket System
  - The Socket System has been removed and we now use Discord Webhook to share Intel.
- Setting Loader
  - Missing Keys will be created.
- Update Dependencies
  - Open-CV Updated to 4.11.\*
  - ScreenInfo to 0.8.1
  - MSS to 10.0.0

### Added

- Discord System
  - All alarms are sent to a Discord webhook with the system name.
- dhooks-lite
  - Discord Webhook library

## [0.9.0] - 2025-01-08

### Changed

- Setting Manager
  - The settings manager has been refactored to improve the handling and organization of settings.
- Config Mode
  - The configuration mode has been optimized to respond more flexibly to changes.
- Refactor of the entire system
  - The system has been refactored to improve code readability and maintainability.
- Logger System
  - The logger system has been enhanced to capture more detailed information about errors, aiding in better troubleshooting.
- Alert Sound
  - The alert sound will not interrupt the program after an error occurs.

### Fixed

- EveLocal not closing with Window Close:
  - An issue was fixed where the EveLocal window would not properly close when the window's close button was clicked. This fix ensures that the window is now correctly closed when the user attempts to exit.
- Settings not reloaded if changed:
  - A bug where settings were not being reloaded after being modified has been resolved. Now, when changes are made to the settings, they will be properly reloaded, reflecting the new configuration.
- Vision not reloaded if changed:
  - A similar issue was addressed where vision settings (likely related to display or graphical configurations) were not reloaded after changes. This fix ensures that any changes to vision settings are immediately applied and reflected.
- Overlay Window not fitting exactly to the monitor resolution:
  - The overlay window was previously not aligning correctly with the monitor's resolution. This issue has been fixed, ensuring that the overlay window now correctly fits and scales to the screen size, providing a more accurate and consistent user interface.

### Added

- Propertys for each system
  - New properties have been added to manage and configure each system individually. This allows for more flexibility and control over system settings and their states.
- Socket System (Test)
  - A new socket system has been implemented for testing purposes. This enables communication between different components or systems, facilitating data exchange and interactions.
- Cleanup Functions
  - Cleanup functions have been introduced to improve resource management. These functions help remove unnecessary data, free up memory, and ensure the application remains efficient by handling cleanup tasks properly.
- Set changed flag in the menu:
  - The changed flag is now set to True whenever a modification is made to the menu settings. This helps track changes and triggers actions like warnings before exiting without saving.
- Buttons State
  - All buttons now have a state color to indicate when they are pressed. This visual cue helps users easily identify the current state of the buttons, improving the user interface and overall user experience by providing clearer feedback during interaction.
- Message Box System
  - Implemented a single-instance error message box to prevent multiple error windows from opening simultaneously.

## [0.5.0] - 2024-11-29

### Changed

- Settings for Regions now have a visual interface
- Setting Region now work per `Marquee Selection`
- Enemy & Faction now work seperately

### Fixed

- Faction Region can't be open if a error occurs [#15](https://github.com/Geuthur/EVE-Alert-Opensource/issues/15)
- In some Cases Multiple Monitors not work [#15](https://github.com/Geuthur/EVE-Alert-Opensource/issues/15) (Testing)
- Vision System not work if Detection Scale is Zero or below
- Images with Alpha Channel triggers Error [#15](https://github.com/Geuthur/EVE-Alert-Opensource/issues/15)
- It is not possible to switch Windows recognition off/on during sound playback
- Overlapping Overlay when F1 and F2 was pressed
- Background width is not correct

### Added

- Abort Option on Settings with `ESC`
- Faction Detection Scale
- Overlay System

### Removed

- Drop Color Mode Support
- Screenshot Mode

## [0.4.4] - 2024-11-23

### Changed

- print to logger in regionmanager
- Alarm Sound

### Fixed

- Faction Region can't be open if a error occurs [#15](https://github.com/Geuthur/EVE-Alert-Opensource/issues/15)

## [0.4.3] - 2024-10-18

### Added

- Dependency Requirment Info

### Changed

- Requirments
- Moved from PyAudio to sounddevice and soundfile
- Update Preview Video
- Update Window Installer

### Fixed

- Icon Bitmap Error on Linux [#9](https://github.com/Geuthur/EVE-Alert-Opensource/issues/9)

## [0.4.2] - 2024-10-18

### Added

- Window Installation Guide

### Fixed

- Window Installer not working correctly if executed as Admin

## [0.4.1b1] - 2024-05-24

### Added

- Cooldown Function with optional cooldowns
- Log Message Function
- Status Icon for Running Alert
- Log Textfield

### Fixed

- Program Blocking on some situations
- Alert Text appears after Error
- Performance Issues

### Changed

- Moved AlertAgent to Async
- AlertAgent now started via seperate Thread
- save_settings function moved to SettingsManager
- Moved Play Sound to one Function

### Removed

- Socket Functions, Maybe Later i will Implemend this..
- Log System Label
- Unused Code

## Full Changelog

[1.0.1]: https://github.com/Geuthur/eve-alert/compare/v1.0.0...v1.0.1 "1.0.1"
[2.0.0]: https://github.com/Geuthur/eve-alert/compare/v1.0.1...v2.0.0 "2.0.0"
[2.0.1]: https://github.com/Geuthur/eve-alert/compare/v2.0.0...v2.0.1 "2.0.1"
[2.0.2]: https://github.com/Geuthur/eve-alert/compare/v2.0.1...v2.0.2 "2.0.2"
[in development]: https://github.com/Geuthur/eve-alert/compare/v2.0.2...HEAD "In Development"
[report any issues]: https://github.com/Geuthur/eve-alert/issues "report any issues"