"""G§8.11 / LP-BE-1201: the legacy session-token API is gone. Its views, serializers, URLs and the session shim
were deleted; these routes (frozen here) must stay unreachable. Paths the new contract serves again
(checkout, order detail, invoice, cart, search, support messages) are not listed."""

from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, resolve

REMOVED_ROUTES = [
    f"/api/accounts/{route}" for route in (
        "profile/", "profile/password/", "profile/legal-entity/", "profile/legal-entity/send-code/",
        "profile/legal-entity/verify-code/", "login-password/", "password-reset/send-code/",
        "password-reset/", "demo-login/", "send-code/", "verify-code/",
    )
] + [
    # LP-BE-402 deleted the legacy parts views; /api/parts/search/ etc. come back with the new contract
    # (LP-BE-403..406), so only routes that are never reused are listed here.
    f"/api/parts/{route}" for route in (
        "feed/", "public/quote-requests/00000000-0000-0000-0000-000000000000/",
        "public/quote-requests/00000000-0000-0000-0000-000000000000/acknowledge/",
    )
] + [
    # LP-BE-601 deleted the legacy orders views; paths the new contract serves again (checkout, order
    # detail, invoice, support messages) are not listed.
    f"/api/orders/{route}" for route in (
        "public/notifications/00000000-0000-0000-0000-000000000000/",
        "public/notifications/00000000-0000-0000-0000-000000000000/acknowledge/",
        "LP-1/verify-payment/", "LP-1/demo-confirm-payment/", "LP-1/resolve-courier-fee/",
        "LP-1/decline-courier-fee/", "items/1/resolve-action/", "items/1/cancel-action/",
        "items/1/acknowledge-action/", "items/1/demo-request-change/", "items/1/demo-update-status/",
    )
]


class LegacyEndpointsRemovedTests(SimpleTestCase):
    def test_no_legacy_route_resolves(self):
        self.assertGreaterEqual(len(REMOVED_ROUTES), 25)
        for url in REMOVED_ROUTES:
            with self.subTest(url=url), self.assertRaises(Resolver404):
                resolve(url)

    @override_settings(DEBUG=False)
    def test_legacy_routes_return_json_404_for_every_method(self):
        for url in REMOVED_ROUTES:
            for method in ("get", "post", "delete"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(url)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json()["code"], "not_found")
