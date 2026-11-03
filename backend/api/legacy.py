"""Legacy session_id API, disabled since LP-BE-103 (LPK-98).

These URL modules are no longer included in config/urls.py: they identified customers by a
client-generated `session_id` and can't pass CsrfSessionAuthentication + IsCustomer.
Their views stay importable (the Django admin and later services reuse their logic) until each
replacement story lands; LP-BE-1201 deletes them. See docs/legacy-endpoints.md.
"""

import unittest

# urlconf module -> prefix it used to be mounted at
LEGACY_URLCONFS = {
}

# Routes whose modules were already deleted (LP-BE-201 accounts, LP-BE-402 parts, LP-BE-601 orders).
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


def legacy_endpoint_test(replaced_by):
    """Skip a test of a disabled legacy endpoint, naming the story that re-implements the scenario."""
    return unittest.skip(f"legacy session_id endpoint disabled (LP-BE-103); scenario moves to {replaced_by}")
