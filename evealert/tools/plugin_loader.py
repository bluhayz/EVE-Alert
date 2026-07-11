"""Plugin loader for EVE Alert.

Discovers and loads Python plugin files from the user plugins directory
(``~/.config/evealert/plugins/``). Each plugin is a plain ``.py`` file
that may define any of the following hook functions:

    def on_start() -> None: ...
    def on_stop() -> None: ...
    def on_enemy(system: str, timestamp: str) -> None: ...
    def on_faction(system: str, timestamp: str) -> None: ...
    def on_intel(line: str) -> None: ...

Hooks are called synchronously in a thread-pool executor so plugin errors
are isolated and cannot crash the detection loop. If a hook raises an
exception it is logged at WARNING level and silently suppressed.
"""

import importlib.util
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("alert.plugins")

_HOOKS = ("on_start", "on_stop", "on_enemy", "on_faction", "on_intel")
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="plugin")


class PluginManager:
    """Discovers, loads, and dispatches calls to user-defined plugin modules."""

    def __init__(self) -> None:
        # {hook_name: [callable, ...]}
        self._hooks: dict[str, list[Callable]] = {h: [] for h in _HOOKS}
        self._loaded_names: list[str] = []

    # ------------------------------------------------------------------
    # Discovery & loading
    # ------------------------------------------------------------------

    def load_plugins(self, plugin_dir: Path) -> int:
        """Scan *plugin_dir* for ``.py`` files and register hook functions.

        Returns the number of plugin files successfully loaded.
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
                registered = self._register_hooks(module, py_file.stem)
                if registered:
                    self._loaded_names.append(py_file.stem)
                    loaded += 1
                    logger.info(
                        "Plugin loaded: %s (%d hook(s))", py_file.stem, registered
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

    def _register_hooks(self, module: Any, name: str) -> int:
        count = 0
        for hook in _HOOKS:
            fn = getattr(module, hook, None)
            if callable(fn):
                self._hooks[hook].append(fn)
                count += 1
        return count

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def call(self, hook: str, **kwargs) -> None:
        """Call all registered handlers for *hook* in the thread pool.

        Errors from individual plugins are logged but never propagated.
        """
        for fn in self._hooks.get(hook, []):
            _executor.submit(self._safe_call, fn, hook, kwargs)

    @staticmethod
    def _safe_call(fn: Callable, hook: str, kwargs: dict) -> None:
        try:
            fn(**kwargs)
        except Exception as exc:
            logger.warning("Plugin hook '%s' raised: %s", hook, exc)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def loaded_names(self) -> list[str]:
        return list(self._loaded_names)

    def hook_count(self, hook: str) -> int:
        return len(self._hooks.get(hook, []))


# Module-level singleton
_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    global _manager
    if _manager is None:
        _manager = PluginManager()
    return _manager
