"""Tests for evealert.ui.update_dialog (#178, v8.0 sha256 verification).

Uses the offscreen Qt platform so no display is needed in CI. Exercises
_DownloadWorker.run() directly (not through the full QThread machinery)
since it's a plain method that emits Qt signals -- connecting to those
signals with plain Python callables works without an event loop.
"""

import hashlib
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qapp():
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    return QApplication.instance() or QApplication([])


class DownloadWorkerTestCase(unittest.TestCase):
    def setUp(self):
        _qapp()
        # cleanup_temp_download() (called on a checksum mismatch) always
        # targets self_updater.temp_download_path()'s fixed path, not
        # whatever dest a caller happens to pass -- use the real one so
        # this test exercises that interaction faithfully, same as
        # UpdateDialog does in production.
        from evealert.tools.self_updater import temp_download_path

        self.dest = temp_download_path()
        self.addCleanup(lambda: self.dest.unlink(missing_ok=True))

    def tearDown(self):
        pass

    def _run_worker(self, expected_sha256=None, write_bytes=b"fake exe contents"):
        from evealert.ui.update_dialog import _DownloadWorker

        async def _fake_download(url, dest, progress_cb=None):
            dest.write_bytes(write_bytes)

        results = {"finished": None, "failed": None}
        worker = _DownloadWorker("https://example.com/exe", self.dest, expected_sha256)
        worker.finished.connect(lambda p: results.__setitem__("finished", p))
        worker.failed.connect(lambda m: results.__setitem__("failed", m))

        with patch(
            "evealert.ui.update_dialog.download_release", side_effect=_fake_download
        ):
            worker.run()
        return results


class Sha256VerificationTests(DownloadWorkerTestCase):
    def test_matching_checksum_emits_finished(self):
        data = b"fake exe contents"
        expected = hashlib.sha256(data).hexdigest()

        results = self._run_worker(expected_sha256=expected, write_bytes=data)

        self.assertEqual(results["finished"], self.dest)
        self.assertIsNone(results["failed"])
        self.assertTrue(self.dest.exists())

    def test_mismatched_checksum_emits_failed_and_deletes_file(self):
        results = self._run_worker(expected_sha256="0" * 64, write_bytes=b"corrupted data")

        self.assertIsNone(results["finished"])
        self.assertIsNotNone(results["failed"])
        self.assertIn("checksum", results["failed"].lower())
        self.assertFalse(self.dest.exists())  # #178 AC: no replaced binary

    def test_no_checksum_available_proceeds_unverified(self):
        results = self._run_worker(expected_sha256=None)

        self.assertEqual(results["finished"], self.dest)
        self.assertIsNone(results["failed"])

    def test_empty_string_checksum_proceeds_unverified(self):
        """fetch_checksum() can resolve to "" in edge cases -- must be
        treated the same as None (falsy), not compared as a real hash."""
        results = self._run_worker(expected_sha256="")

        self.assertEqual(results["finished"], self.dest)
        self.assertIsNone(results["failed"])


if __name__ == "__main__":
    unittest.main()
