"""LP-BE-703: start payments & order payment endpoints (G§7.10, G§8.7)."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import make_customer
from orders.models import Order
from orders.services import cancel_order, set_item_status
from orders.testing import make_order
from payments.models import Payment
from payments.providers import PaymentInitError
from payments.services import create_payment, mark_payment, start_payment

D = Decimal
PAYMENT_KEYS = {"id", "kind", "provider", "status", "amount", "currency", "checkout_url", "paid_at", "created_at"}


@override_settings(FRONTEND_BASE_URL="https://lionparts.ge")
class OrderPaymentTests(TestCase):
    def setUp(self):
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.client = APIClient()
        self.client.force_login(self.customer.user)
        self.order = make_order(self.customer, payment_option="split_70_30", items=(("A", D("100"), 1),))  # 260.00
        Order.objects.filter(pk=self.order.pk).update(amount_due_now=D("182.00"))
        self.order.refresh_from_db()

    def pay(self, method="card", client=None):
        return (client or self.client).post(f"/api/orders/{self.order.order_number}/payments/", {"method": method},
                                            format="json")

    def test_retry_while_waiting_is_an_initial_payment_for_amount_due_now(self):
        body = self.pay().json()
        self.assertEqual(set(body), PAYMENT_KEYS)
        self.assertEqual((body["kind"], body["amount"], body["currency"], body["status"], body["provider"]),
                         ("initial", "182.00", "GEL", "pending", "fake"))
        self.assertIn(f"order={self.order.order_number}", body["checkout_url"])

    def test_balance_payment_for_exactly_balance_due_in_the_order_currency(self):
        initial = Payment.objects.create(order=self.order, kind="initial", provider="invoice", amount=D("182.00"),
                                         currency="GEL", status="pending")
        mark_payment(initial, "paid")
        body = self.pay("invoice").json()
        self.assertEqual((body["kind"], body["amount"], body["currency"], body["provider"], body["status"],
                          body["checkout_url"]), ("balance", "78.00", "GEL", "invoice", "pending", None))

    def test_payment_in_progress_returns_the_open_payment(self):
        first = self.pay().json()
        response = self.pay()
        self.assertEqual((response.status_code, response.json()["code"], response.json()["payment"]["id"]),
                         (409, "payment_in_progress", first["id"]))
        Payment.objects.filter(pk=first["id"]).update(created_at=timezone.now() - timedelta(minutes=31))
        self.assertEqual(self.pay().status_code, 409)  # the customer may still be paying it: no second charge

    def test_nothing_to_pay(self):
        initial = Payment.objects.create(order=self.order, kind="initial", provider="invoice", amount=D("260.00"),
                                         currency="GEL", status="pending")
        mark_payment(initial, "paid")
        response = self.pay()
        self.assertEqual((response.status_code, response.json()["code"]), (409, "nothing_to_pay"))
        cancelled = make_order(self.customer, status=Order.STATUS_CANCELLED)
        response = self.client.post(f"/api/orders/{cancelled.order_number}/payments/", {"method": "card"}, format="json")
        self.assertEqual(response.json()["code"], "nothing_to_pay")

    def test_start_is_never_called_inside_a_transaction(self):
        baseline, seen = len(connection.atomic_blocks), {}

        def start(provider, payment):
            seen["depth"] = len(connection.atomic_blocks)
            return "https://lionparts.ge/x"

        with mock.patch("payments.providers.FakeProvider.start", start):
            self.pay()
        self.assertEqual(seen["depth"], baseline)

    def test_get_payment_and_ownership(self):
        payment_id = self.pay().json()["id"]
        url = f"/api/orders/{self.order.order_number}/payments/{payment_id}/"
        self.assertEqual(self.client.get(url).json()["id"], payment_id)
        other = APIClient()
        other.force_login(make_customer(email="o@example.com", phone="599000000", is_phone_verified=True,
                                        is_email_verified=True).user)
        self.assertEqual(other.get(url).status_code, 404)
        self.assertEqual(self.pay(client=other).status_code, 404)
        self.assertEqual(APIClient().get(url).status_code, 401)
        other_order = make_order(self.customer)
        self.assertEqual(self.client.get(f"/api/orders/{other_order.order_number}/payments/{payment_id}/").status_code, 404)

    def test_invalid_method(self):
        self.assertEqual(self.pay("cash").json()["fields"]["method"][0]["code"], "invalid")


@override_settings(FRONTEND_BASE_URL="https://lionparts.ge")
class DelayedProviderStartTests(TestCase):
    """LPK-179: start_payment holds no lock during the provider call, so the payment can change meanwhile. A late
    answer must not reopen it, publish its checkout_url or report a failure that did not happen."""

    URL = "https://lionparts.ge/late"

    def setUp(self):
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.client = APIClient()
        self.client.force_login(self.customer.user)
        self.order = make_order(self.customer, items=(("A", D("100"), 1), ("B", D("50"), 1)))
        self.payment = create_payment(self.order, "card")

    def late_provider(self, during, error=False):
        def start(provider, payment):
            during()
            if error:
                raise PaymentInitError("down")
            return self.URL
        return mock.patch("payments.providers.FakeProvider.start", start)

    def start(self, during, error=False):
        with self.late_provider(during, error):
            started = start_payment(self.payment)
        self.payment.refresh_from_db()
        return started

    def cancel(self):
        cancel_order(self.order.pk, "Changed my mind", actor="customer")

    def test_a_closed_payment_never_becomes_pending_or_gets_a_checkout_url(self):
        for status in ("failed", "expired"):
            with self.subTest(status=status):
                self.payment = create_payment(self.order, "card")
                self.assertFalse(self.start(lambda s=status: mark_payment(self.payment, s)))
                self.assertEqual((self.payment.status, self.payment.checkout_url), (status, ""))

    def test_an_order_cancelled_during_the_call_stays_closed(self):
        self.assertFalse(self.start(self.cancel))
        self.assertEqual((self.payment.status, self.payment.checkout_url), ("expired", ""))

    def test_the_endpoint_publishes_no_checkout_url_after_a_cancellation(self):
        self.payment.delete()
        with self.late_provider(self.cancel):
            response = self.client.post(f"/api/orders/{self.order.order_number}/payments/", {"method": "card"},
                                        format="json")
        body = response.json()
        self.assertEqual((response.status_code, body["status"], body["checkout_url"]), (201, "expired", None))

    def test_an_amount_reset_during_the_call_stays_closed_and_allows_the_new_amount(self):
        item = self.order.items.get(part_number="A")
        self.assertFalse(self.start(lambda: set_item_status(item.pk, "cancelled", reason="Out of stock")))
        self.assertEqual((self.payment.status, self.payment.checkout_url), ("expired", ""))
        self.order.refresh_from_db()
        body = self.client.post(f"/api/orders/{self.order.order_number}/payments/", {"method": "card"},
                                format="json").json()
        self.assertEqual((body["status"], body["amount"]), ("pending", f"{self.order.amount_due_now:.2f}"))

    def test_a_late_provider_error_does_not_replace_the_real_result(self):
        self.assertFalse(self.start(self.cancel, error=True))
        self.assertEqual(self.payment.status, "expired")

    def test_a_payment_already_paid_is_not_reported_as_a_failure(self):
        self.assertTrue(self.start(lambda: mark_payment(self.payment, "paid")))
        self.assertEqual((self.payment.status, self.payment.checkout_url), ("paid", ""))

    def test_a_provider_pending_callback_during_the_call_keeps_the_checkout_url(self):
        self.assertTrue(self.start(lambda: mark_payment(self.payment, "pending")))
        self.assertEqual((self.payment.status, self.payment.checkout_url), ("pending", self.URL))

    def test_a_provider_error_still_fails_an_open_payment(self):
        self.assertFalse(self.start(lambda: None, error=True))
        self.assertEqual((self.payment.status, self.payment.checkout_url), ("failed", ""))
