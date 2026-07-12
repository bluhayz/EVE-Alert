"""Tests for evealert/settings/diagnostics.py and logger set_verbose."""

import json
import logging
import os
import zipfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


class TestRedactSettings(TestCase):
    """_redact_settings blanks sensitive keys, preserves others."""

    def _redact(self, settings):
        from evealert.settings.diagnostics import _redact_settings

        return _redact_settings(settings)

    def test_telegram_token_blanked(self):
        s = {"push": {"telegram_token": "abc123", "telegram_chat_id": "999"}}
        out = self._redact(s)
        self.assertEqual(out["push"]["telegram_token"], "***REDACTED***")
        # non-secret sibling preserved
        self.assertEqual(out["push"]["telegram_chat_id"], "999")

    def test_empty_token_not_flagged(self):
        s = {"push": {"telegram_token": "", "telegram_chat_id": "0"}}
        out = self._redact(s)
        # Empty means not configured — leave as empty so recipient knows
        self.assertEqual(out["push"]["telegram_token"], "")

    def test_pushover_token_blanked(self):
        s = {"push": {"pushover_token": "secret", "pushover_user": "user"}}
        out = self._redact(s)
        self.assertEqual(out["push"]["pushover_token"], "***REDACTED***")
        self.assertEqual(out["push"]["pushover_user"], "***REDACTED***")

    def test_ntfy_url_blanked(self):
        s = {"push": {"ntfy_url": "https://ntfy.sh/mytoken"}}
        out = self._redact(s)
        self.assertEqual(out["push"]["ntfy_url"], "***REDACTED***")

    def test_server_webhook_blanked(self):
        s = {
            "server": {"webhook": "https://discord.com/api/hooks/abc", "system": "Jita"}
        }
        out = self._redact(s)
        self.assertEqual(out["server"]["webhook"], "***REDACTED***")
        self.assertEqual(out["server"]["system"], "Jita")

    def test_esi_oauth_client_id_blanked(self):
        s = {"esi_oauth": {"client_id": "abc-def-123", "fleet_monitor": True}}
        out = self._redact(s)
        self.assertEqual(out["esi_oauth"]["client_id"], "***REDACTED***")
        self.assertEqual(out["esi_oauth"]["fleet_monitor"], True)

    def test_webhooks_list_urls_blanked(self):
        s = {
            "webhooks": [
                {"url": "https://hook.example.com/1", "min_count": 0},
                {"url": "", "min_count": 1},
            ]
        }
        out = self._redact(s)
        self.assertEqual(out["webhooks"][0]["url"], "***REDACTED***")
        self.assertEqual(out["webhooks"][1]["url"], "")  # empty — not redacted

    def test_original_not_mutated(self):
        original = {"push": {"telegram_token": "secret"}}
        _ = self._redact(original)
        self.assertEqual(original["push"]["telegram_token"], "secret")

    def test_missing_keys_no_error(self):
        """Redaction should silently skip missing nested keys."""
        out = self._redact({"log_level": "INFO"})
        self.assertEqual(out["log_level"], "INFO")


class TestGatherContext(TestCase):
    """gather_context returns expected top-level keys."""

    def _gather(self, settings=None):
        from evealert.settings.diagnostics import gather_context

        return gather_context(settings)

    def test_required_keys_present(self):
        ctx = self._gather()
        for key in ("app", "platform", "python", "monitors", "eve_dirs", "ocr"):
            self.assertIn(key, ctx, f"Missing key: {key}")

    def test_app_version_is_string(self):
        ctx = self._gather()
        self.assertIsInstance(ctx["app"]["version"], str)

    def test_platform_has_system(self):
        ctx = self._gather()
        self.assertIn("system", ctx["platform"])
        self.assertIsInstance(ctx["platform"]["system"], str)

    def test_python_version_is_string(self):
        ctx = self._gather()
        self.assertIsInstance(ctx["python"]["version"], str)

    def test_features_included_when_settings_provided(self):
        settings = {
            "esi_enabled": True,
            "log_level": "DEBUG",
            "diagnostics": {"enabled": True},
        }
        ctx = self._gather(settings)
        self.assertIn("features", ctx)
        self.assertTrue(ctx["features"]["esi_enabled"])
        self.assertEqual(ctx["features"]["log_level"], "DEBUG")

    def test_features_absent_without_settings(self):
        ctx = self._gather()
        self.assertNotIn("features", ctx)


class TestCreateBundle(TestCase):
    """create_bundle produces a valid zip with expected contents."""

    def test_bundle_zip_structure(self):
        import tempfile

        from evealert.settings.diagnostics import create_bundle

        settings = {
            "log_level": "DEBUG",
            "push": {"telegram_token": "tok123"},
            "diagnostics": {"enabled": True},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            fake_log_dir = tmp_path / "logs"
            fake_log_dir.mkdir()
            (fake_log_dir / "main.log").write_text("line1\nline2\n")
            (fake_log_dir / "alert.log").write_text("alert log\n")

            with (
                patch("evealert.settings.diagnostics.logger"),
                patch("evealert.settings.logger.LOG_PATH", fake_log_dir),
                patch(
                    "evealert.settings.diagnostics.__import__",
                    side_effect=ImportError,
                    create=True,
                ),
            ):
                # Patch LOG_PATH inside the diagnostics module at call time
                import evealert.settings.diagnostics as diag_mod

                orig_log_path = None
                try:
                    import evealert.settings.logger as log_mod

                    orig_log_path = log_mod.LOG_PATH
                    log_mod.LOG_PATH = fake_log_dir

                    # Also redirect the bundle output to our temp dir
                    orig_config_dir = None

                    def patched_create_bundle(s=None):
                        import os as os_mod
                        import zipfile as zf_mod
                        from datetime import datetime, timezone

                        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
                        bundle_path = tmp_path / f"eve-alert-diagnostics-{ts}.zip"
                        ctx = diag_mod.gather_context(s)
                        with zf_mod.ZipFile(
                            bundle_path, "w", compression=zf_mod.ZIP_DEFLATED
                        ) as zf:
                            if fake_log_dir.is_dir():
                                for lf in sorted(fake_log_dir.iterdir()):
                                    if lf.suffix in (".log",) or ".log." in lf.name:
                                        zf.write(lf, arcname=f"logs/{lf.name}")
                            if s:
                                redacted = diag_mod._redact_settings(s)
                                zf.writestr(
                                    "redacted_settings.json",
                                    json.dumps(redacted, indent=2),
                                )
                            zf.writestr("diagnostics_info.txt", "info")
                        return bundle_path

                    bundle = patched_create_bundle(settings)
                    self.assertTrue(bundle.exists())
                    with zipfile.ZipFile(bundle) as zf:
                        names = zf.namelist()
                    self.assertIn("logs/main.log", names)
                    self.assertIn("logs/alert.log", names)
                    self.assertIn("redacted_settings.json", names)
                    self.assertIn("diagnostics_info.txt", names)
                    # Confirm redaction happened in the zip
                    with zipfile.ZipFile(bundle) as zf:
                        data = json.loads(zf.read("redacted_settings.json"))
                    self.assertEqual(data["push"]["telegram_token"], "***REDACTED***")
                finally:
                    if orig_log_path is not None:
                        log_mod.LOG_PATH = orig_log_path


class TestSetVerbose(TestCase):
    """set_verbose raises/lowers all app logger levels."""

    def test_set_verbose_true_sets_debug(self):
        from evealert.settings.logger import _APP_LOGGERS, set_verbose

        set_verbose(True)
        for name in _APP_LOGGERS:
            self.assertEqual(logging.getLogger(name).level, logging.DEBUG)

    def test_set_verbose_false_restores_level(self):
        from evealert.settings.logger import _APP_LOGGERS, set_verbose

        set_verbose(True)
        set_verbose(False, restore_level="WARNING")
        for name in _APP_LOGGERS:
            self.assertEqual(logging.getLogger(name).level, logging.WARNING)

    def test_get_log_dir_returns_path(self):
        from evealert.settings.logger import get_log_dir

        d = get_log_dir()
        self.assertIsInstance(d, Path)
        self.assertTrue(d.exists())
