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


if __name__ == "__main__":
    unittest.main()
