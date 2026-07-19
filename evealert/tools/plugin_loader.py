"""Plugin loader for EVE Alert (v1: original; v2: #181, v8.0).

Discovers and loads Python plugin files from the user plugins directory
(``~/.config/evealert/plugins/``). Each plugin is a plain ``.py`` file
that may define any of the hook functions documented in
``evealert.plugin_api`` (v2, typed, ``ctx``-first) or the original v1
kwargs-based signatures -- see plugin_api's module docstring for both.

Hooks are called synchronously in a thread-pool executor so plugin
errors are isolated and cannot crash the detection loop. A plugin that
raises on _MAX_CONSECUTIVE_FAILURES consecutive calls to the SAME hook
is quarantined (disabled, with a log line) until re-enabled from
Settings > Plugins or the next full reload.
"""

import importlib.util
import inspect
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("alert.plugins")

_HOOKS = (
    "on_start", "on_stop", "on_enemy", "on_faction", "on_intel",
    # #181 (v8.0): new v2-only hooks -- no v1 equivalent, so no
    # backward-compat parameter-name check is needed for these two.
    "on_killmail", "on_threat_score",
)

# The exact parameter-name set each hook's ORIGINAL (v1) signature used.
# A loaded hook function is treated as v1 only when its parameters match
# this set exactly -- not just the parameter COUNT, since on_enemy/
# on_faction's v1 form (system, timestamp) and v2 form (ctx, event) both
# take exactly two parameters and can't be told apart by arity alone.
_V1_PARAM_NAMES: dict[str, frozenset] = {
    "on_start": frozenset(),
    "on_stop": frozenset(),
    "on_enemy": frozenset({"system", "timestamp"}),
    "on_faction": frozenset({"system", "timestamp"}),
    "on_intel": frozenset({"line"}),
}

_MAX_CONSECUTIVE_FAILURES = 3

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="plugin")


@dataclass
class PluginRecord:
    """One loaded plugin file's state -- discovery info the Settings >
    Plugins UI lists, plus the quarantine counters."""

    name: str
    path: Path
    version: str | None = None
    enabled: bool = True
    quarantined: bool = False
    hook_names: list[str] = field(default_factory=list)
    _hooks: dict[str, Callable] = field(default_factory=dict, repr=False)
    _is_v1: dict[str, bool] = field(default_factory=dict, repr=False)
    _consecutive_failures: dict[str, int] = field(default_factory=dict, repr=False)

    @property
    def status(self) -> str:
        if self.quarantined:
            return "quarantined"
        return "enabled" if self.enabled else "disabled"


def _is_v1_signature(hook: str, fn: Callable) -> bool:
    v1_names = _V1_PARAM_NAMES.get(hook)
    if v1_names is None:
        return False  # v2-only hook (on_killmail, on_threat_score)
    try:
        params = frozenset(inspect.signature(fn).parameters.keys())
    except (TypeError, ValueError):
        return False
    return params == v1_names


class PluginManager:
    """Discovers, loads, and dispatches calls to user-defined plugin modules."""

    def __init__(self) -> None:
        self._plugins: dict[str, PluginRecord] = {}

    # ------------------------------------------------------------------
    # Discovery & loading
    # ------------------------------------------------------------------

    def load_plugins(self, plugin_dir: Path) -> int:
        """Scan *plugin_dir* for ``.py`` files and register hook functions.

        Returns the number of plugin files successfully loaded. Loading
        again (e.g. a manual reload) replaces prior state for any plugin
        whose file still exists, preserving nothing from before -- a
        previously-quarantined plugin gets a fresh start on reload.
        """
        if not plugin_dir.is_dir():
            logger.debug("Plugin directory not found: %s", plugin_dir)
            return 0

        loaded = 0
        for py_file in sorted(plugin_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue  # skip __init__.py etc.
            try:
                module = self._load_module(py_file)
                record = self._build_record(module, py_file)
                if record.hook_names:
                    self._plugins[py_file.stem] = record
                    loaded += 1
                    logger.info(
                        "Plugin loaded: %s v%s (%d hook(s))",
                        py_file.stem, record.version or "?", len(record.hook_names),
                    )
            except Exception as exc:
                logger.warning("Failed to load plugin %s: %s", py_file.name, exc)

        return loaded

    def _load_module(self, path: Path) -> Any:
        spec = importlib.util.spec_from_file_location(
            f"evealert_plugin_{path.stem}", path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec for {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module

    def _build_record(self, module: Any, path: Path) -> PluginRecord:
        record = PluginRecord(
            name=path.stem, path=path,
            version=getattr(module, "__version__", None),
        )
        for hook in _HOOKS:
            fn = getattr(module, hook, None)
            if not callable(fn):
                continue
            record._hooks[hook] = fn
            record._is_v1[hook] = _is_v1_signature(hook, fn)
            record._consecutive_failures[hook] = 0
            record.hook_names.append(hook)
        return record

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def call(
        self, hook: str, *, ctx_settings: dict | None = None,
        log_fn: Callable[[str], None] | None = None, event: Any = None, **v1_kwargs,
    ) -> None:
        """Dispatch *hook* to every enabled, non-quarantined plugin that
        registered it, in the thread pool.

        v1 plugins are called with **v1_kwargs (the original kwargs
        convention, unchanged). v2 plugins are called positionally as
        fn(ctx) or fn(ctx, event) -- a PluginContext is built fresh per
        call from *ctx_settings*/*log_fn* (both required for any plugin
        directory that has v2 plugins registered for this hook; harmless
        to omit when only v1 plugins are loaded).

        Errors from individual plugins are logged but never propagated;
        see _safe_call for the quarantine bookkeeping.
        """
        for name, record in list(self._plugins.items()):
            if not record.enabled or record.quarantined:
                continue
            fn = record._hooks.get(hook)
            if fn is None:
                continue
            _executor.submit(
                self._safe_call, record, hook, fn, v1_kwargs, ctx_settings, log_fn, event
            )

    def _safe_call(
        self, record: PluginRecord, hook: str, fn: Callable,
        v1_kwargs: dict, ctx_settings: dict | None, log_fn, event: Any,
    ) -> None:
        try:
            if record._is_v1.get(hook):
                fn(**v1_kwargs)
            else:
                from evealert.plugin_api import PluginContext  # noqa: PLC0415

                ctx = PluginContext(
                    settings=ctx_settings or {}, log_fn=log_fn or (lambda _t: None)
                )
                if event is None:
                    fn(ctx)
                else:
                    fn(ctx, event)
            record._consecutive_failures[hook] = 0
        except Exception as exc:
            logger.warning("Plugin '%s' hook '%s' raised: %s", record.name, hook, exc)
            record._consecutive_failures[hook] = record._consecutive_failures.get(hook, 0) + 1
            if (
                record._consecutive_failures[hook] >= _MAX_CONSECUTIVE_FAILURES
                and not record.quarantined
            ):
                record.quarantined = True
                logger.warning(
                    "Plugin '%s' quarantined after %d consecutive '%s' failures -- "
                    "re-enable it from Settings > Plugins.",
                    record.name, _MAX_CONSECUTIVE_FAILURES, hook,
                )

    # ------------------------------------------------------------------
    # Introspection & control (Settings > Plugins UI, #181)
    # ------------------------------------------------------------------

    @property
    def loaded_names(self) -> list[str]:
        return list(self._plugins.keys())

    def hook_count(self, hook: str) -> int:
        return sum(1 for r in self._plugins.values() if hook in r.hook_names)

    def list_plugins(self) -> list[PluginRecord]:
        """Return all loaded plugin records, name-sorted -- what
        Settings > Plugins renders."""
        return [self._plugins[name] for name in sorted(self._plugins)]

    def get_plugin(self, name: str) -> PluginRecord | None:
        return self._plugins.get(name)

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Enable/disable a plugin by name. Returns False if no such
        plugin is loaded."""
        record = self._plugins.get(name)
        if record is None:
            return False
        record.enabled = enabled
        return True

    def reset_quarantine(self, name: str) -> bool:
        """Clear a plugin's quarantine flag and failure counters, re-
        enabling it. Returns False if no such plugin is loaded."""
        record = self._plugins.get(name)
        if record is None:
            return False
        record.quarantined = False
        for hook in record._consecutive_failures:
            record._consecutive_failures[hook] = 0
        return True


# Module-level singleton
_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    global _manager
    if _manager is None:
        _manager = PluginManager()
    return _manager
