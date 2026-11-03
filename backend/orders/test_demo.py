"""LP-BE-807: demo order endpoints (G§8.10)."""

import importlib
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import clear_url_caches
from django.utils import timezone
from rest_framework.test import APIClient

import config.urls
from accounts.testing import make_customer
from orders.models import Order, OrderItem, OrderItemEvent
from orders.testing import make_order
from parts.models import PartQuoteRequest
from payments.models import Payment
from payments.services import create_payment, start_payment
from pricing.models import ExchangeRate

D = Decimal
STATUS_FLOW = ("purchased", "received_usa", "shipped_to_georgia", "received_georgia", "ready_for_pickup", "completed")


def reload_urls():
    clear_url_caches()
    importlib.reload(config.urls)


@override_settings(ENABLE_DEMO_ENDPOINTS=True, PAYMENT_PROVIDER="fake")
class DemoOrderTests(TestCase):
    def setUp(self):
        reload_urls()
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.customer_client = APIClient()
        self.customer_client.force_login(self.customer.user)
        self.staff = APIClient()
        self.staff.force_login(get_user_model().objects.create_superuser("op", "op@example.com", "x"))
        self.order = make_order(self.customer, payment_method="card")
        Order.objects.filter(pk=self.order.pk).update(delivery_method="delivery", delivery_city="tbilisi")
        self.item = self.order.items.get()
        self.base = f"/api/demo/orders/{self.order.order_number}/"

    def tearDown(self):
        reload_urls()

    def pay_open_card_payment(self, kind_initial=True):
        if kind_initial:
            payment = create_payment(self.order, "card")
            start_payment(payment)
        else:
            payment_id = self.customer_client.post(f"/api/orders/{self.order.order_number}/payments/",
                                                   {"method": "card"}, format="json").json()["id"]
            payment = Payment.objects.get(pk=payment_id)
        response = self.customer_client.post(f"/api/payments/fake/{payment.pk}/complete/", {"status": "paid"},
                                             format="json")
        self.assertEqual(response.json()["status"], "paid")

    def demo(self, path, body, client=None):
        return (client or self.staff).post(self.base + path, body, format="json", headers={"accept-language": "en"})

    def test_processing_to_completed_with_a_decision_and_a_courier_fee(self):
        self.pay_open_card_payment()
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, "processing")
        body = self.demo(f"items/{self.item.pk}/action/", {"action_type": "price_change", "message": "New price",
                                                           "proposed_unit_price": "300.00"}).json()
        self.assertEqual((body["status"], body["items"][0]["action"]["new"]), ("action_required",
                                                                              {"unit_price": "300.00"}))
        decided = self.customer_client.post(f"/api/orders/items/{self.item.pk}/decision/", {"decision": "accept"},
                                            format="json").json()
        self.assertEqual(decided["balance_due"], "40.00")
        self.assertEqual(self.demo("courier-fee/", {"proposed_fee_gel": "15.00", "message": "Vake"}).json()
                         ["courier"]["proposed_fee"], "15.00")
        accepted = self.customer_client.post(f"/api/orders/{self.order.order_number}/courier-fee/accept/", {},
                                             format="json").json()
        self.assertEqual(accepted["balance_due"], "55.00")
        self.pay_open_card_payment(kind_initial=False)  # the balance payment
        for status in STATUS_FLOW:
            body = self.demo(f"items/{self.item.pk}/status/", {"item_status": status}).json()
        self.assertEqual((body["status"], body["payment_status"], body["balance_due"], body["progress"]),
                         ("completed", "paid", "0.00", {"step": 6, "of": 6}))
        operator_events = OrderItemEvent.objects.filter(item=self.item, actor_type="operator")
        self.assertEqual(set(operator_events.values_list("actor_name", flat=True)), {"op"})

    def test_status_endpoint_bypasses_the_balance_guard_only_here(self):
        self.pay_open_card_payment()
        Order.objects.filter(pk=self.order.pk).update(balance_due=D("10.00"))
        body = self.demo(f"items/{self.item.pk}/status/", {"item_status": "ready_for_pickup"}).json()
        self.assertEqual(body["items"][0]["item_status"], "ready_for_pickup")

    def test_service_errors_are_field_errors_and_change_nothing(self):
        response = self.demo(f"items/{self.item.pk}/status/", {"item_status": "purchased"})  # not paid yet
        self.assertEqual((response.status_code, response.json()["code"]), (400, "validation_error"))
        self.assertEqual(response.json()["fields"]["item_status"][0]["message"], "The order is not paid yet.")
        self.assertEqual(self.demo(f"items/{self.item.pk}/status/", {"item_status": "nope"}).status_code, 400)
        self.assertEqual(OrderItem.objects.get(pk=self.item.pk).item_status, "created")

    def test_item_of_another_order_is_404(self):
        other = make_order(self.customer).items.get()
        self.assertEqual(self.demo(f"items/{other.pk}/status/", {"item_status": "checking"}).status_code, 404)

    def test_customers_anonymous_and_staff_without_permission_are_refused(self):
        limited = get_user_model().objects.create_user("viewer", "v@example.com", "x", is_staff=True)
        limited.user_permissions.add(*Permission.objects.filter(codename__in=["view_order", "view_orderitem"]))
        viewer = APIClient()
        viewer.force_login(limited)
        for client, code in ((APIClient(), 401), (self.customer_client, 403), (viewer, 403)):
            for path, body in ((f"items/{self.item.pk}/status/", {"item_status": "checking"}),
                               (f"items/{self.item.pk}/action/", {"action_type": "other", "message": "x"}),
                               ("courier-fee/", {"proposed_fee_gel": "1.00"})):
                with self.subTest(path=path, code=code):
                    self.assertEqual(self.demo(path, body, client).status_code, code)
        self.assertFalse(OrderItemEvent.objects.filter(actor_type="operator").exists())

    def test_quote_ready(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        qr = PartQuoteRequest.objects.create(customer=self.customer, request_type="manual_search", part_number="P-1",
                                             supplier_price_usd=D("100.00"))
        response = self.staff.post(f"/api/demo/quote-requests/{qr.pk}/ready/", {"weight_kg": "2.0"}, format="json")
        self.assertEqual((response.status_code, response.json()["is_price_ready"]), (200, True))
        self.assertEqual(self.customer_client.post(f"/api/demo/quote-requests/{qr.pk}/ready/", {"weight_kg": "2.0"},
                                                   format="json").status_code, 403)


class DemoDisabledTests(TestCase):
    def test_demo_order_endpoints_are_not_mounted_by_default(self):
        with override_settings(ENABLE_DEMO_ENDPOINTS=False):
            reload_urls()
            staff = APIClient()
            staff.force_login(get_user_model().objects.create_superuser("op", "op@example.com", "x"))
            for path in ("orders/LP-1/items/1/status/", "orders/LP-1/items/1/action/", "orders/LP-1/courier-fee/",
                         "quote-requests/1/ready/"):
                self.assertEqual(staff.post(f"/api/demo/{path}", {}, format="json").status_code, 404)
        reload_urls()
