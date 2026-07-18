"""Tests for the shared ESI name→ID resolver (issue #110).

Verifies the migration off the removed GET /search/ endpoints to
POST /universe/ids/.
"""

import asyncio
import unittest

import respx
from httpx import Response

from evealert.tools.universe import resolve_ids, resolve_names, resolve_single_id

_IDS_URL = "https://esi.evetech.net/latest/universe/ids/"
_NAMES_URL = "https://esi.evetech.net/latest/universe/names/"


class ResolveIdsTests(unittest.TestCase):
    def test_resolve_single_system_exact_match(self):
        with respx.mock:
            respx.post(_IDS_URL).mock(
                return_value=Response(
                    200, json={"systems": [{"id": 30000142, "name": "Jita"}]}
                )
            )
            result = asyncio.run(resolve_single_id("Jita", "systems"))
        self.assertEqual(result, 30000142)

    def test_resolve_single_case_insensitive(self):
        with respx.mock:
            respx.post(_IDS_URL).mock(
                return_value=Response(
                    200, json={"systems": [{"id": 30000142, "name": "Jita"}]}
                )
            )
            result = asyncio.run(resolve_single_id("jita", "systems"))
        self.assertEqual(result, 30000142)

    def test_resolve_single_character_category(self):
        with respx.mock:
            respx.post(_IDS_URL).mock(
                return_value=Response(
                    200,
                    json={"characters": [{"id": 95465499, "name": "CCP Bartender"}]},
                )
            )
            result = asyncio.run(resolve_single_id("CCP Bartender", "characters"))
        self.assertEqual(result, 95465499)

    def test_resolve_single_returns_none_when_category_empty(self):
        with respx.mock:
            respx.post(_IDS_URL).mock(return_value=Response(200, json={}))
            result = asyncio.run(resolve_single_id("Nowhere", "systems"))
        self.assertIsNone(result)

    def test_resolve_single_returns_none_on_http_error(self):
        with respx.mock:
            respx.post(_IDS_URL).mock(return_value=Response(500))
            result = asyncio.run(resolve_single_id("Jita", "systems"))
        self.assertIsNone(result)

    def test_resolve_ids_empty_names_makes_no_call(self):
        with respx.mock:
            # No route registered — any HTTP call would raise.
            result = asyncio.run(resolve_ids([]))
        self.assertEqual(result, {})

    def test_resolve_single_falls_back_to_first_when_no_exact_name(self):
        # Server returns a system whose name doesn't case-match the query.
        with respx.mock:
            respx.post(_IDS_URL).mock(
                return_value=Response(
                    200, json={"systems": [{"id": 30002187, "name": "Amarr"}]}
                )
            )
            result = asyncio.run(resolve_single_id("amar", "systems"))
        self.assertEqual(result, 30002187)


class ResolveNamesTests(unittest.TestCase):
    """#173: resolve_names() -- the inverse ID -> name batch resolver."""

    def test_resolves_multiple_ids(self):
        with respx.mock:
            respx.post(_NAMES_URL).mock(
                return_value=Response(
                    200,
                    json=[
                        {"id": 95465499, "name": "CCP Bartender", "category": "character"},
                        {"id": 30000142, "name": "Jita", "category": "solar_system"},
                    ],
                )
            )
            result = asyncio.run(resolve_names([95465499, 30000142]))
        self.assertEqual(
            result, {95465499: "CCP Bartender", 30000142: "Jita"}
        )

    def test_empty_ids_makes_no_call(self):
        with respx.mock:
            # No route registered -- any HTTP call would raise.
            result = asyncio.run(resolve_names([]))
        self.assertEqual(result, {})

    def test_returns_empty_dict_on_http_error(self):
        with respx.mock:
            respx.post(_NAMES_URL).mock(return_value=Response(500))
            result = asyncio.run(resolve_names([12345]))
        self.assertEqual(result, {})

    def test_unresolved_ids_simply_absent_not_erroring(self):
        with respx.mock:
            respx.post(_NAMES_URL).mock(
                return_value=Response(
                    200, json=[{"id": 1, "name": "Known", "category": "character"}]
                )
            )
            result = asyncio.run(resolve_names([1, 999999]))
        self.assertEqual(result, {1: "Known"})
        self.assertNotIn(999999, result)


if __name__ == "__main__":
    unittest.main()
