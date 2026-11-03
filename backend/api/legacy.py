"""Legacy session_id API, disabled since LP-BE-103 (LPK-98).

These URL modules are no longer included in config/urls.py: they identified customers by a
client-generated `session_id` and can't pass CsrfSessionAuthentication + IsCustomer.
Their views stay importable (the Django admin and later services reuse their logic) until each
replacement story lands; LP-BE-1201 deletes them. See docs/legacy-endpoints.md.
"""

import unittest

# urlconf module -> prefix it used to be mounted at
LEGACY_URLCONFS = {
    "parts.urls": "/api/parts/",
    "cart.urls": "/api/cart/",
    "orders.urls": "/api/orders/",
}

# Routes whose modules were already deleted (LP-BE-201: accounts views/serializers/urls).
REMOVED_ROUTES = [
    f"/api/accounts/{route}" for route in (
        "profile/", "profile/password/", "profile/legal-entity/", "profile/legal-entity/send-code/",
        "profile/legal-entity/verify-code/", "login-password/", "password-reset/send-code/",
        "password-reset/", "demo-login/", "send-code/", "verify-code/",
    )
]


def legacy_endpoint_test(replaced_by):
    """Skip a test of a disabled legacy endpoint, naming the story that re-implements the scenario."""
    return unittest.skip(f"legacy session_id endpoint disabled (LP-BE-103); scenario moves to {replaced_by}")
