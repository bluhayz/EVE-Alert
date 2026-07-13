"""Tests for the settings field registry (#107).

Pure/no-Tk: exercises path helpers, registry integrity, and the
apply/save round-trip using fake widgets.
"""

import unittest

from evealert.settings.fields import (
    FIELDS,
    TAB_ORDER,
    apply_registry_fields,
    save_registry_fields,
)
from evealert.settings.store import DEFAULT_SETTINGS, _get_by_path, _set_by_path


class PathHelperTests(unittest.TestCase):
    def test_set_creates_nested(self):
        d = {}
        _set_by_path(d, "a.b.c", 5)
        self.assertEqual(d, {"a": {"b": {"c": 5}}})

    def test_get_with_default(self):
        d = {"a": {"b": 1}}
        self.assertEqual(_get_by_path(d, "a.b", 0), 1)
        self.assertEqual(_get_by_path(d, "a.x", 99), 99)
        self.assertEqual(_get_by_path(d, "missing.path", "d"), "d")


class RegistryIntegrityTests(unittest.TestCase):
    def test_attrs_unique(self):
        attrs = [f.attr for f in FIELDS]
        self.assertEqual(len(attrs), len(set(attrs)), "duplicate widget attrs")

    def test_kinds_valid(self):
        for f in FIELDS:
            self.assertIn(f.kind, ("bool", "int", "str", "float"), f.path)

    def test_tabs_declared_in_order(self):
        for f in FIELDS:
            self.assertIn(f.tab, TAB_ORDER, f"{f.path} tab not in TAB_ORDER")

    def test_paths_exist_in_default_settings(self):
        for f in FIELDS:
            sentinel = object()
            self.assertIsNot(
                _get_by_path(DEFAULT_SETTINGS, f.path, sentinel),
                sentinel,
                f"{f.path} missing from DEFAULT_SETTINGS",
            )


class _FakeVar:
    def __init__(self, v=None):
        self._v = v

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


class _FakeEntry:
    def __init__(self, v=""):
        self._v = str(v)

    def get(self):
        return self._v

    def delete(self, *a):
        self._v = ""

    def insert(self, _idx, val):
        self._v = str(val)


class _Stub:
    """Minimal object with fake widget attributes for each FieldSpec."""

    def __init__(self):
        for f in FIELDS:
            setattr(self, f.attr, _FakeVar(False) if f.kind == "bool" else _FakeEntry())


class RegistryRoundTripTests(unittest.TestCase):
    def test_apply_then_save_round_trips(self):
        stub = _Stub()
        settings = {
            "dscan": {"enabled": True, "alert_red": False},
            "wormhole": {"thera_max_jumps": 9},
            "push": {"telegram_token": "SECRET"},
            "esi_oauth": {"client_id": "my-app"},
        }
        apply_registry_fields(stub, settings)
        self.assertTrue(stub.dscan_enabled_var.get())
        self.assertFalse(stub.dscan_red_var.get())
        self.assertEqual(stub.thera_max_jumps_entry.get(), "9")
        self.assertEqual(stub.telegram_token_entry.get(), "SECRET")

        out = {}
        save_registry_fields(stub, out)
        self.assertIs(out["dscan"]["enabled"], True)
        self.assertIs(out["dscan"]["alert_red"], False)
        self.assertEqual(out["wormhole"]["thera_max_jumps"], 9)
        self.assertEqual(out["push"]["telegram_token"], "SECRET")
        self.assertEqual(out["esi_oauth"]["client_id"], "my-app")

    def test_int_field_falls_back_to_default_on_garbage(self):
        stub = _Stub()
        stub.web_ui_port_entry = _FakeEntry("not-a-number")
        out = {}
        save_registry_fields(stub, out)
        self.assertEqual(out["web_ui"]["port"], 8765)


if __name__ == "__main__":
    unittest.main()
