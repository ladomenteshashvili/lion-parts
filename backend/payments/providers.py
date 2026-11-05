"""Payment provider contract (G§7.10). The real card adapter (Flitt) is delivered separately and
registers itself in PROVIDERS; this backend ships only FakeProvider for development, staging and tests.
An adapter must charge exactly payment.amount in payment.currency (minor units) and verify the
signature, amount and currency of every callback."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

PLATFORM_STATUSES = {"pending", "paid", "failed", "expired", "refunded"}


class PaymentInitError(Exception):
    """The provider could not open a payment page; nothing was charged."""


class PaymentProvider:
    name = ""

    def start(self, payment):
        """-> checkout_url. Raises PaymentInitError. Called only outside DB transactions."""
        raise NotImplementedError

    def handle_callback(self, request):
        """-> (payment, status). MUST verify signature, amount and currency; raise on anything invalid."""
        raise NotImplementedError

    def fetch_status(self, payment):
        """-> pending / paid / failed / expired, from an authenticated query of the provider's current state, never a
        cache (reconciliation and the admin re-check; a repeat call settles a conflict with a callback). `paid` only
        after verifying amount and currency. Called only outside DB transactions; raise on anything unverifiable,
        never guess. Must not put credentials in exception messages."""
        raise NotImplementedError


class FakeProvider(PaymentProvider):
    """No money moves. The demo endpoint POST /api/payments/fake/<id>/complete/ plays the callback."""

    name = "fake"

    def start(self, payment):
        base = settings.FRONTEND_BASE_URL.rstrip("/")
        return f"{base}/payment-result?order={payment.order.order_number}&payment={payment.pk}&fake=1"

    def handle_callback(self, request):
        raise ValueError("the fake provider has no provider callback; use the demo complete endpoint")

    def fetch_status(self, payment):
        # the demo complete endpoint is the only source of results: `unknown` has no answer yet
        return "pending" if payment.status == "unknown" else payment.status


PROVIDERS = {"fake": FakeProvider}


def get_provider():
    try:
        return PROVIDERS[settings.PAYMENT_PROVIDER]()
    except KeyError:
        raise ImproperlyConfigured(f"Unknown PAYMENT_PROVIDER {settings.PAYMENT_PROVIDER!r}") from None
