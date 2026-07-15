"""Tests for ESI login behaviour in SettingsDialog (#200).

Verifies that clicking "Login with EVE SSO" with a blank client-ID field
reaches get_esi_auth() (and therefore uses the embedded default) instead of
hitting the old early-return guard.

Uses the offscreen Qt platform so no display is needed in CI.
"""

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_dialog():
    """Return a SettingsDialog instance backed by a temp-file store."""
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    QApplication.instance() or QApplication([])  # ensure app exists

    import evealert.tools.esi_auth as _esi_mod  # noqa: PLC0415

    _esi_mod._auth = None  # reset singleton so each test starts clean

    from evealert.settings.store import reset_settings_store  # noqa: PLC0415
    from evealert.ui.settings_dialog import SettingsDialog  # noqa: PLC0415

    td = tempfile.mkdtemp()
    store = reset_settings_store(td)
    dlg = SettingsDialog(parent=None, store=store)
    return dlg


class EsiLoginBlankFieldTests(unittest.TestCase):
    def setUp(self):
        import evealert.tools.esi_auth as _esi_mod  # noqa: PLC0415

        self._reset_patch = mock.patch.object(
            type(None).__class__, "__bool__", lambda: True
        )
        _esi_mod._auth = None

    def tearDown(self):
        import evealert.tools.esi_auth as _esi_mod  # noqa: PLC0415

        _esi_mod._auth = None

    def test_blank_field_calls_get_esi_auth_not_early_return(self):
        """With blank client ID, _esi_login() must call get_esi_auth(), not return early."""
        dlg = _make_dialog()
        dlg._esi_client_id.setText("")  # blank — simulates a fresh install

        fake_auth = mock.MagicMock()
        fake_thread = mock.MagicMock()
        fake_thread.finished = mock.MagicMock()
        fake_thread.finished.connect = mock.MagicMock()

        with mock.patch(
            "evealert.tools.esi_auth.get_esi_auth", return_value=fake_auth
        ) as mock_get_esi_auth, mock.patch(
            "evealert.ui.settings_dialog._LoginThread", return_value=fake_thread
        ):
            dlg._esi_login()

        # get_esi_auth must have been called (empty string passed → uses embedded default)
        mock_get_esi_auth.assert_called_once_with("")
        # The login thread must have been started
        fake_thread.start.assert_called_once()
        # Button must be disabled while waiting
        self.assertFalse(dlg._esi_login_btn.isEnabled())

    def test_custom_client_id_passed_through(self):
        """A non-empty client ID must be forwarded to get_esi_auth() unchanged."""
        dlg = _make_dialog()
        custom_id = "a" * 32
        dlg._esi_client_id.setText(custom_id)

        fake_auth = mock.MagicMock()
        fake_thread = mock.MagicMock()
        fake_thread.finished = mock.MagicMock()
        fake_thread.finished.connect = mock.MagicMock()

        with mock.patch(
            "evealert.tools.esi_auth.get_esi_auth", return_value=fake_auth
        ) as mock_get_esi_auth, mock.patch(
            "evealert.ui.settings_dialog._LoginThread", return_value=fake_thread
        ):
            dlg._esi_login()

        mock_get_esi_auth.assert_called_once_with(custom_id)
        fake_thread.start.assert_called_once()

    def test_placeholder_text_does_not_say_required(self):
        """Placeholder must no longer instruct the user that a client ID is required."""
        dlg = _make_dialog()
        placeholder = dlg._esi_client_id.placeholderText().lower()
        self.assertNotIn("required", placeholder)
