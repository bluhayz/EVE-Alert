"""Tests for the #181 Plugin Manager dialog.

Uses the offscreen Qt platform so no display is needed in CI.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qapp():
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    return QApplication.instance() or QApplication([])


def _write_plugin(plugin_dir: Path, name: str, source: str) -> None:
    (plugin_dir / f"{name}.py").write_text(source, encoding="utf-8")


V1_PLUGIN = '''
__version__ = "1.0"

def on_start():
    pass

def on_enemy(system, timestamp):
    pass
'''


class PluginManagerDialogTestCase(unittest.TestCase):
    def setUp(self):
        _qapp()
        self.temp_dir = Path(tempfile.mkdtemp())
        from evealert.tools.plugin_loader import PluginManager
        import evealert.tools.plugin_loader as loader_mod

        self._orig_manager = loader_mod._manager
        loader_mod._manager = PluginManager()

    def tearDown(self):
        import evealert.tools.plugin_loader as loader_mod

        loader_mod._manager = self._orig_manager
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class PluginManagerDialogTests(PluginManagerDialogTestCase):
    def test_empty_state_shown_when_no_plugins(self):
        from evealert.ui.plugin_manager_dialog import PluginManagerDialog

        dlg = PluginManagerDialog(None)
        self.assertEqual(dlg._table.rowCount(), 1)
        self.assertIn("No plugins found", dlg._table.item(0, 0).text())
        dlg.deleteLater()

    def test_lists_loaded_plugin(self):
        from evealert.tools.plugin_loader import get_plugin_manager
        from evealert.ui.plugin_manager_dialog import PluginManagerDialog

        _write_plugin(self.temp_dir, "p", V1_PLUGIN)
        get_plugin_manager().load_plugins(self.temp_dir)

        dlg = PluginManagerDialog(None)
        self.assertEqual(dlg._table.rowCount(), 1)
        self.assertEqual(dlg._table.item(0, 0).text(), "p")
        self.assertEqual(dlg._table.item(0, 1).text(), "1.0")
        self.assertEqual(dlg._table.item(0, 3).text(), "enabled")
        dlg.deleteLater()

    def test_reload_after_empty_state_clears_span_and_shows_all_columns(self):
        """#248 regression: the empty-state row's 1x4 span used to
        persist after a later reload populated real rows, hiding every
        column but the first for row 0."""
        from evealert.tools.plugin_loader import get_plugin_manager
        from evealert.ui.plugin_manager_dialog import PluginManagerDialog

        dlg = PluginManagerDialog(None)  # starts empty -> sets the span
        self.assertGreater(dlg._table.columnSpan(0, 0), 1)

        _write_plugin(self.temp_dir, "p", V1_PLUGIN)
        get_plugin_manager().load_plugins(self.temp_dir)
        dlg._reload_all()

        self.assertEqual(dlg._table.columnSpan(0, 0), 1)
        self.assertEqual(dlg._table.item(0, 1).text(), "1.0")
        self.assertEqual(dlg._table.item(0, 2).text(), "on_start, on_enemy")
        dlg.deleteLater()

    def test_toggle_selected_disables_plugin(self):
        from evealert.tools.plugin_loader import get_plugin_manager
        from evealert.ui.plugin_manager_dialog import PluginManagerDialog

        _write_plugin(self.temp_dir, "p", V1_PLUGIN)
        get_plugin_manager().load_plugins(self.temp_dir)

        dlg = PluginManagerDialog(None)
        dlg._table.setCurrentCell(0, 0)
        dlg._toggle_selected()

        self.assertFalse(get_plugin_manager().get_plugin("p").enabled)
        self.assertEqual(dlg._table.item(0, 3).text(), "disabled")
        dlg.deleteLater()

    def test_reset_quarantine_clears_flag(self):
        from evealert.tools.plugin_loader import get_plugin_manager
        from evealert.ui.plugin_manager_dialog import PluginManagerDialog

        _write_plugin(self.temp_dir, "p", V1_PLUGIN)
        get_plugin_manager().load_plugins(self.temp_dir)
        get_plugin_manager().get_plugin("p").quarantined = True

        dlg = PluginManagerDialog(None)
        dlg._table.setCurrentCell(0, 0)
        dlg._reset_selected()

        self.assertFalse(get_plugin_manager().get_plugin("p").quarantined)
        self.assertEqual(dlg._table.item(0, 3).text(), "enabled")
        dlg.deleteLater()

    def test_no_selection_toggle_is_a_noop(self):
        from evealert.ui.plugin_manager_dialog import PluginManagerDialog

        dlg = PluginManagerDialog(None)
        dlg._toggle_selected()  # must not raise with nothing selected
        dlg._reset_selected()
        dlg.deleteLater()


if __name__ == "__main__":
    unittest.main()
