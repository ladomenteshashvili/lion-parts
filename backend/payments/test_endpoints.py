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
from orders.testing import make_order
from payments.models import Payment
from payments.services import mark_payment

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
        self.assertEqual(self.pay().status_code, 201)  # an older pending payment no longer blocks

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
