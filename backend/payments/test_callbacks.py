"""LP-BE-704: provider callback and the demo fake completion (G§8.8, D3)."""

import importlib
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import clear_url_caches
from rest_framework.test import APIClient

import config.urls
from accounts.testing import make_customer
from orders.testing import make_order
from payments.models import Payment
from payments.providers import FakeProvider

D = Decimal


def reload_urls():
    clear_url_caches()
    importlib.reload(config.urls)


class CallbackTests(TestCase):
    def setUp(self):
        self.order = make_order(make_customer())
        self.payment = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=self.order.total,
                                              currency="GEL", status="pending")

    def post(self, provider="fake"):
        return APIClient(enforce_csrf_checks=True).post(f"/api/payments/{provider}/callback/", {"x": 1}, format="json")

    def test_verified_callback_marks_the_payment_without_csrf_or_login(self):
        with mock.patch.object(FakeProvider, "handle_callback", return_value=(self.payment, "paid")):
            response = self.post()
        self.assertEqual((response.status_code, response.json()), (200, {"ok": True}))
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual((self.payment.status, self.order.status), ("paid", "processing"))

    def test_duplicate_callback_is_harmless(self):
        with mock.patch.object(FakeProvider, "handle_callback", return_value=(self.payment, "paid")):
            self.post()
            self.post()
        self.order.refresh_from_db()
        self.assertEqual(self.order.amount_paid, self.order.total)

    def test_adapter_failure_is_400_and_changes_nothing(self):
        with mock.patch.object(FakeProvider, "handle_callback", side_effect=ValueError("bad signature")), \
                self.assertLogs("payments.views", "WARNING"):
            response = self.post()
        self.assertEqual((response.status_code, response.json()["code"]), (400, "invalid_callback"))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "pending")

    def test_unexpected_status_is_rejected(self):
        with mock.patch.object(FakeProvider, "handle_callback", return_value=(self.payment, "created")), \
                self.assertLogs("payments.views", "WARNING"):
            self.assertEqual(self.post().status_code, 400)

    def test_fake_provider_has_no_real_callback(self):
        with self.assertLogs("payments.views", "WARNING"):
            self.assertEqual(self.post().status_code, 400)

    def test_other_provider_name_is_404(self):
        self.assertEqual(self.post("flitt").status_code, 404)


@override_settings(ENABLE_DEMO_ENDPOINTS=True)
class FakeCompleteTests(TestCase):
    def setUp(self):
        reload_urls()
        self.customer = make_customer()
        self.order = make_order(self.customer)
        self.payment = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=self.order.total,
                                              currency="GEL", status="pending")
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def tearDown(self):
        reload_urls()

    def complete(self, status="paid", client=None, payment=None):
        return (client or self.client).post(f"/api/payments/fake/{(payment or self.payment).pk}/complete/",
                                            {"status": status}, format="json")

    def test_owner_pays_and_the_order_moves_to_processing(self):
        response = self.complete()
        self.assertEqual((response.status_code, response.json()["status"]), (200, "paid"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "processing")

    def test_not_pending_is_409(self):
        self.complete("failed")
        response = self.complete("paid")
        self.assertEqual((response.status_code, response.json()["code"]), (409, "nothing_to_pay"))

    def test_other_customer_anonymous_and_real_payments(self):
        other = APIClient()
        other.force_login(make_customer(email="o@example.com", phone="599000000").user)
        self.assertEqual(self.complete(client=other).status_code, 404)
        self.assertEqual(APIClient().post(f"/api/payments/fake/{self.payment.pk}/complete/", {"status": "paid"},
                                          format="json").status_code, 401)
        invoice = Payment.objects.create(order=self.order, kind="balance", provider="invoice", amount=D("1"),
                                         currency="GEL", status="pending")
        self.assertEqual(self.complete(payment=invoice).status_code, 404)
        self.assertEqual(self.complete("refunded").json()["fields"]["status"][0]["code"], "invalid")

    def test_csrf_is_required(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.customer.user)
        self.assertEqual(self.complete(client=client).json()["code"], "csrf_failed")


class FakeCompleteMountTests(TestCase):
    def test_not_mounted_without_demo_endpoints(self):
        with override_settings(ENABLE_DEMO_ENDPOINTS=False):
            reload_urls()
            self.assertEqual(APIClient().post("/api/payments/fake/1/complete/", {}, format="json").status_code, 404)
        reload_urls()
