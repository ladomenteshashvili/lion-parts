"""LP-BE-103 (LPK-98): the legacy session_id API is not routed (docs/legacy-endpoints.md)."""

import importlib
import re
import uuid

from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, resolve

from api.legacy import LEGACY_URLCONFS, REMOVED_ROUTES

SAMPLES = {"int": "1", "uuid": str(uuid.uuid4()), "path": "a/b", "str": "LP-1", "slug": "x"}


def legacy_paths():
    yield from REMOVED_ROUTES
    for module, prefix in LEGACY_URLCONFS.items():
        for pattern in importlib.import_module(module).urlpatterns:
            route = re.sub(r"<(?:(\w+):)?\w+>", lambda m: SAMPLES[m.group(1) or "str"], str(pattern.pattern))
            yield prefix + route


class LegacyEndpointsDisabledTests(SimpleTestCase):
    def test_no_legacy_route_resolves(self):
        paths = list(legacy_paths())
        # Legacy routes whose paths the new contract does not serve again (accounts, parts, orders).
        self.assertGreaterEqual(len(paths), 25)
        for url in paths:
            with self.subTest(url=url), self.assertRaises(Resolver404):
                resolve(url)

    @override_settings(DEBUG=False)
    def test_legacy_routes_return_json_404_for_every_method(self):
        for url in legacy_paths():
            for method in ("get", "post", "delete"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(url)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json()["code"], "not_found")
