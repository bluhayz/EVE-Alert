"""Tests for evealert.tools.update_checker — version comparison and async API check."""

import unittest

import pytest
import respx
from httpx import Response


class TestVersionTuple(unittest.TestCase):
    def test_parses_without_prefix(self):
        from evealert.tools.update_checker import _version_tuple

        self.assertEqual(_version_tuple("2.6.0"), (2, 6, 0))
        self.assertEqual(_version_tuple("1.0.0"), (1, 0, 0))
        self.assertEqual(_version_tuple("3.0.1"), (3, 0, 1))

    def test_strips_v_prefix(self):
        from evealert.tools.update_checker import _version_tuple

        self.assertEqual(_version_tuple("v2.6.0"), (2, 6, 0))
        self.assertEqual(_version_tuple("v1.2.3"), (1, 2, 3))

    def test_handles_bad_input(self):
        from evealert.tools.update_checker import _version_tuple

        result = _version_tuple("not-a-version")
        self.assertEqual(result, (0,))

    def test_version_comparison(self):
        from evealert.tools.update_checker import _version_tuple

        self.assertGreater(_version_tuple("2.7.0"), _version_tuple("2.6.0"))
        self.assertLess(_version_tuple("2.5.0"), _version_tuple("2.6.0"))
        self.assertEqual(_version_tuple("2.6.0"), _version_tuple("v2.6.0"))


class TestCheckForUpdate(unittest.IsolatedAsyncioTestCase):
    @respx.mock
    async def test_returns_tag_when_newer(self):
        from evealert.tools.update_checker import _RELEASES_URL, check_for_update

        respx.get(_RELEASES_URL).mock(
            return_value=Response(200, json={"tag_name": "v99.0.0"})
        )
        result = await check_for_update("2.6.0")
        self.assertEqual(result, "v99.0.0")

    @respx.mock
    async def test_returns_none_when_same_version(self):
        from evealert.tools.update_checker import _RELEASES_URL, check_for_update

        respx.get(_RELEASES_URL).mock(
            return_value=Response(200, json={"tag_name": "v2.6.0"})
        )
        result = await check_for_update("2.6.0")
        self.assertIsNone(result)

    @respx.mock
    async def test_returns_none_when_older(self):
        from evealert.tools.update_checker import _RELEASES_URL, check_for_update

        respx.get(_RELEASES_URL).mock(
            return_value=Response(200, json={"tag_name": "v1.0.0"})
        )
        result = await check_for_update("2.6.0")
        self.assertIsNone(result)

    @respx.mock
    async def test_returns_none_on_http_error(self):
        from evealert.tools.update_checker import _RELEASES_URL, check_for_update

        respx.get(_RELEASES_URL).mock(return_value=Response(500))
        result = await check_for_update("2.6.0")
        self.assertIsNone(result)

    @respx.mock
    async def test_returns_none_on_missing_tag(self):
        from evealert.tools.update_checker import _RELEASES_URL, check_for_update

        respx.get(_RELEASES_URL).mock(
            return_value=Response(200, json={"other": "data"})
        )
        result = await check_for_update("2.6.0")
        self.assertIsNone(result)


class TestFetchChecksum(unittest.IsolatedAsyncioTestCase):
    @respx.mock
    async def test_returns_matching_hash(self):
        from evealert.tools.update_checker import _RELEASE_TAG_URL, fetch_checksum

        checksums_url = "https://example.com/checksums.txt"
        respx.get(_RELEASE_TAG_URL.format(tag="v8.0.0")).mock(
            return_value=Response(200, json={
                "assets": [
                    {"name": "EVE-Alert.exe", "browser_download_url": "https://example.com/exe"},
                    {"name": "checksums.txt", "browser_download_url": checksums_url},
                ]
            })
        )
        respx.get(checksums_url).mock(
            return_value=Response(
                200, text="abc123def456  EVE-Alert.exe\ndeadbeef  other-file.txt\n"
            )
        )
        result = await fetch_checksum("v8.0.0", "EVE-Alert.exe")
        self.assertEqual(result, "abc123def456")

    @respx.mock
    async def test_returns_none_when_no_checksums_asset(self):
        from evealert.tools.update_checker import _RELEASE_TAG_URL, fetch_checksum

        respx.get(_RELEASE_TAG_URL.format(tag="v8.0.0")).mock(
            return_value=Response(200, json={
                "assets": [
                    {"name": "EVE-Alert.exe", "browser_download_url": "https://example.com/exe"},
                ]
            })
        )
        result = await fetch_checksum("v8.0.0", "EVE-Alert.exe")
        self.assertIsNone(result)

    @respx.mock
    async def test_returns_none_when_asset_not_listed_in_checksums(self):
        from evealert.tools.update_checker import _RELEASE_TAG_URL, fetch_checksum

        checksums_url = "https://example.com/checksums.txt"
        respx.get(_RELEASE_TAG_URL.format(tag="v8.0.0")).mock(
            return_value=Response(200, json={
                "assets": [{"name": "checksums.txt", "browser_download_url": checksums_url}]
            })
        )
        respx.get(checksums_url).mock(
            return_value=Response(200, text="deadbeef  some-other-file.exe\n")
        )
        result = await fetch_checksum("v8.0.0", "EVE-Alert.exe")
        self.assertIsNone(result)

    @respx.mock
    async def test_returns_none_on_http_error(self):
        from evealert.tools.update_checker import _RELEASE_TAG_URL, fetch_checksum

        respx.get(_RELEASE_TAG_URL.format(tag="v8.0.0")).mock(return_value=Response(500))
        result = await fetch_checksum("v8.0.0", "EVE-Alert.exe")
        self.assertIsNone(result)


class TestVerifySha256(unittest.TestCase):
    def test_matching_hash_returns_true(self):
        import hashlib
        import tempfile
        from pathlib import Path

        from evealert.tools.update_checker import verify_sha256

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.bin"
            data = b"hello world" * 1000
            path.write_bytes(data)
            expected = hashlib.sha256(data).hexdigest()
            self.assertTrue(verify_sha256(path, expected))

    def test_mismatched_hash_returns_false(self):
        import tempfile
        from pathlib import Path

        from evealert.tools.update_checker import verify_sha256

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.bin"
            path.write_bytes(b"hello world")
            self.assertFalse(verify_sha256(path, "0" * 64))

    def test_case_insensitive_comparison(self):
        import hashlib
        import tempfile
        from pathlib import Path

        from evealert.tools.update_checker import verify_sha256

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.bin"
            path.write_bytes(b"data")
            expected = hashlib.sha256(b"data").hexdigest().upper()
            self.assertTrue(verify_sha256(path, expected))


if __name__ == "__main__":
    unittest.main()
