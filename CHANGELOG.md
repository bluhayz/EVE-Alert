# Changelog

## [3.2.0] 2026-07-11

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