"""Tests for evealert.tools.space_profiles (#143)."""

import unittest


class ProfileTests(unittest.TestCase):
    def test_all_profiles_have_label(self):
        from evealert.tools.space_profiles import PROFILES
        for key, p in PROFILES.items():
            self.assertIn("label", p, f"Profile {key!r} missing 'label'")

    def test_cycle_wraps_around(self):
        from evealert.tools.space_profiles import PROFILE_CYCLE, next_profile
        last = PROFILE_CYCLE[-1]
        first = PROFILE_CYCLE[0]
        self.assertEqual(next_profile(last), first)

    def test_cycle_unknown_returns_first(self):
        from evealert.tools.space_profiles import PROFILE_CYCLE, next_profile
        self.assertEqual(next_profile(None), PROFILE_CYCLE[0])
        self.assertEqual(next_profile("bogus"), PROFILE_CYCLE[0])

    def test_cycle_advances(self):
        from evealert.tools.space_profiles import PROFILE_CYCLE, next_profile
        first = PROFILE_CYCLE[0]
        second = PROFILE_CYCLE[1]
        self.assertEqual(next_profile(first), second)

    def test_apply_profile_writes_settings(self):
        from unittest.mock import MagicMock, patch

        mock_store = MagicMock()
        mock_store.save = MagicMock()
        mock_store.set = MagicMock()

        with patch("evealert.settings.store.get_settings_store", return_value=mock_store):
            # also patch inside the profiles module's lazy import path
            import evealert.tools.space_profiles as _mod
            orig = getattr(_mod, "_patched_store", None)

            def _fake_store():
                return mock_store

            import evealert.settings.store as _store_mod
            real = _store_mod.get_settings_store
            _store_mod.get_settings_store = _fake_store
            try:
                from evealert.tools.space_profiles import apply_profile
                label = apply_profile("nullsec")
            finally:
                _store_mod.get_settings_store = real

        self.assertEqual(label, "Null-sec")
        mock_store.set.assert_called()
        mock_store.save.assert_called_once()

    def test_apply_invalid_profile_raises(self):
        from evealert.tools.space_profiles import apply_profile
        with self.assertRaises(KeyError):
            apply_profile("deep_space_nine")

    def test_nullsec_enables_dscan(self):
        from evealert.tools.space_profiles import PROFILES
        self.assertTrue(PROFILES["nullsec"]["dscan.enabled"])

    def test_highsec_disables_dscan(self):
        from evealert.tools.space_profiles import PROFILES
        self.assertFalse(PROFILES["highsec"]["dscan.enabled"])

    def test_wormhole_tts_enabled(self):
        from evealert.tools.space_profiles import PROFILES
        self.assertTrue(PROFILES["wormhole"]["notifications.tts_enabled"])


if __name__ == "__main__":
    unittest.main()
