# EVE Alert Plugin API (v2)

Plugins are single `.py` files dropped into your plugins folder. EVE
Alert discovers and loads every `.py` file there (except files starting
with `_`) on startup and calls whichever hook functions the file
defines.

**Plugins folder:** Settings → Alerts & Sound → Plugins → Plugin Manager…
→ "Open Plugins Folder" (or `%APPDATA%\evealert\plugins\` on Windows,
`~/.config/evealert/plugins/` on Linux, `~/Library/Application
Support/evealert/plugins/` on macOS).

A plugin needs no registration step beyond existing in that folder — add
a file, restart EVE Alert (or click "Reload All" in the Plugin Manager),
and any hook functions it defines start receiving calls.

## Quick start

```python
# my_plugin.py
__version__ = "1.0"

def on_start(ctx):
    ctx.log("my_plugin is running")

def on_enemy(ctx, event):
    ctx.log(f"Enemy alarm in {event.system} at {event.timestamp}")
```

That's it — no imports of EVE Alert internals required. Everything a
hook needs arrives as arguments.

## Hooks

Define any subset of these. A plugin with none of them loads with 0
hooks and is simply ignored (no error).

| Hook | Signature | Fires |
|---|---|---|
| `on_start` | `on_start(ctx)` | Detection engine starts |
| `on_stop` | `on_stop(ctx)` | Detection engine stops |
| `on_enemy` | `on_enemy(ctx, event: AlarmEvent)` | An Enemy alarm fires |
| `on_faction` | `on_faction(ctx, event: AlarmEvent)` | A Faction alarm fires |
| `on_intel` | `on_intel(ctx, report: IntelReport)` | A new intel-channel chat line arrives |
| `on_killmail` | `on_killmail(ctx, km: KillmailEvent)` | A live killmail matches your R2Z2 watch radius/watchlist |
| `on_threat_score` | `on_threat_score(ctx, assessment: ThreatScoreEvent)` | The composite threat score (0-10) is computed for an Enemy alarm |

All hooks run in a small background thread pool — never on the
detection loop or the Qt UI thread. **A hook must not block for long**
(no `time.sleep()`, no slow network calls without a timeout): with only
2 worker threads shared across every plugin, a hung hook delays every
other plugin's calls.

### `ctx` — PluginContext

Every hook's first argument.

```python
ctx.version           # this API's version string, e.g. "2.0"
ctx.settings           # read-only dict snapshot of settings.json
ctx.log(text)           # write a line to the EVE Alert log pane
ctx.speak(text)         # speak via the app's configured TTS, no-op if TTS is off
ctx.fire_webhook(url, payload)  # POST payload (dict) as JSON to url, fire-and-forget
```

`ctx.log`, `ctx.speak`, and `ctx.fire_webhook` never raise — a bad
webhook URL or a missing audio device is swallowed, not propagated into
your hook.

### Event types

```python
@dataclass(frozen=True)
class AlarmEvent:
    alarm_type: str        # "Enemy" | "Faction"
    system: str
    timestamp: str          # "HH:MM:SS"
    client_name: str | None # set only for an extra multi-client alarm

@dataclass(frozen=True)
class IntelReport:
    line: str                # raw chat line
    system: str | None        # not populated on the on_intel hook today
    pilot: str | None         # not populated on the on_intel hook today

@dataclass(frozen=True)
class KillmailEvent:
    killmail_id: int
    system_id: int | None
    system_name: str | None
    victim_ship_type_id: int | None
    attacker_character_ids: tuple[int, ...]
    jump_distance: int | None  # jumps from your current system, if known

@dataclass(frozen=True)
class ThreatScoreEvent:
    score: int                  # 1-10
    label: str                   # "CAUTION" | "HIGH" | "CRITICAL"
    reasons: tuple[str, ...]
    behavioral_label: str | None
```

All fields are exactly what's shown — nothing more is added silently in
a later release; new *fields* may be appended (with defaults) in a minor
version bump, existing ones never change meaning within API 2.x.

### Plugin version

Set `__version__ = "..."` at module scope (any format you like) and it
shows up in Settings → Plugins → Plugin Manager. Purely informational —
EVE Alert never checks it against anything.

## Reliability: quarantine

If a plugin's hook raises an exception on **3 consecutive calls to that
same hook**, the whole plugin is quarantined: it stops receiving *any*
hook calls (not just the one that failed) until you re-enable it. A log
line names the plugin and hook. Re-enable it from Settings → Plugins →
Plugin Manager → select the plugin → "Reset Quarantine" (or restart the
app, which reloads every plugin fresh).

A single failure doesn't quarantine anything — only 3 in a row on the
same hook. A success in between resets the streak to zero.

## Enable / disable

Settings → Alerts & Sound → Plugins has the master "Enable plugins"
toggle (loads no plugins at all when off) and a "Plugin Manager…"
button listing every discovered plugin with its own enable/disable
switch, independent of the others.

## v1 compatibility

The original (pre-v2) hook signatures still work, unchanged, forever:

```python
def on_start() -> None: ...
def on_stop() -> None: ...
def on_enemy(system: str, timestamp: str) -> None: ...
def on_faction(system: str, timestamp: str) -> None: ...
def on_intel(line: str) -> None: ...
```

EVE Alert tells v1 and v2 hooks apart by inspecting each function's
declared parameter names (not just how many it takes) — you never need
to opt in or mark a plugin as "v2"; just write `ctx`-first signatures
and they're picked up automatically. `on_killmail` and
`on_threat_score` are new in v2 and have no v1 form.

## Packaging notes

- One plugin = one `.py` file. No manifest, no `__init__.py`, no
  packaging step.
- If your plugin needs a third-party package (e.g. `requests`), it must
  already be importable in EVE Alert's own environment — a plugin
  cannot declare or install its own dependencies. Stick to the standard
  library and whatever EVE Alert already depends on (`httpx` is
  available) unless you're running from source with extra packages
  installed into the same environment.
- Read module-level state (like a `calls = []` list) is per-plugin,
  per-process — it resets on every app restart and on every "Reload
  All" in the Plugin Manager.
- Don't import `evealert.manager.alertmanager` or reach into engine
  internals — nothing there is a stable API. Everything you need comes
  through `ctx` and the event object.

## Examples

Three complete, runnable plugins are in [`examples/plugins/`](../examples/plugins/):

- **`discord_rich_alarm.py`** — posts a Discord embed (via
  `ctx.fire_webhook`) with color-coded threat tiers on every Enemy alarm.
- **`sound_per_tier.py`** — plays a different sound file depending on
  the threat-score label (CAUTION / HIGH / CRITICAL).
- **`csv_event_log.py`** — appends every alarm/intel/killmail event to a
  CSV file for your own analysis, independent of EVE Alert's own
  statistics.

Copy one into your plugins folder, edit the constants at the top, and
restart (or Reload All).
