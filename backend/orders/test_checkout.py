"""LP-BE-603: create order (G§7.12, G§8.6, D15/D16)."""

import threading
import unittest
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomerTariff
from accounts.testing import make_customer
from cart.models import CartItem
from orders.models import Order, OrderItem
from payments.models import Payment
from payments.providers import PaymentInitError
from pricing.models import ExchangeRate, SiteSettings
from pricing.rates import RateUnavailable

D = Decimal
PICKUP = {"delivery_method": "pickup", "pickup_point_id": None, "payment_option": "full", "payment_method": "invoice",
          "terms_accepted": True}


class CheckoutMixin:
    def setUp(self):
        self.rate = ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.client = APIClient()
        self.client.force_login(self.customer.user)
        from parts.models import CarrierService
        from pricing.models import PickupPoint
        self.point = PickupPoint.objects.get()
        CarrierService.objects.create(name="Air", min_eta_days=9, max_eta_days=15, is_default=True)

    def fill_cart(self, *indexes, quantity=1, **search):
        for index in indexes or (0,):
            data = self.client.post("/api/parts/search/", {"part_number": "G9010-60020", **search}, format="json").json()
            self.client.post("/api/cart/items/", {"source": "search", "search_id": data["search_id"],
                                                  "offer_id": data["offers"][index]["offer_id"], "quantity": quantity},
                             format="json")

    def checkout(self, client=None, **changes):
        body = {**PICKUP, "pickup_point_id": self.point.pk, **changes}
        return (client or self.client).post("/api/orders/checkout/", body, format="json")


@override_settings(PARTS_PROVIDER="demo", FRONTEND_BASE_URL="https://lionparts.ge")
class CheckoutTests(CheckoutMixin, TestCase):
    def test_invoice_order_freezes_the_cart(self):
        self.fill_cart(0, 1, quantity=2)
        cart = self.client.get("/api/cart/").json()
        lines = list(CartItem.objects.order_by("id").values("unit_price_usd", "unit_price", "list_unit_price"))
        response = self.checkout()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        order, payment = body["order"], body["payment"]
        self.assertEqual((order["total"], order["items_total"], order["discount_total"]),
                         (cart["summary"]["total"], cart["summary"]["items_total"], cart["summary"]["discount_total"]))
        self.assertEqual((order["status"], order["currency"], order["amount_due_now"], order["payment_status"]),
                         ("payment_pending", "GEL", cart["summary"]["total"], "unpaid"))
        self.assertEqual((payment["provider"], payment["status"], payment["checkout_url"], payment["amount"],
                          payment["currency"]), ("invoice", "pending", None, cart["summary"]["total"], "GEL"))
        stored = Order.objects.get()
        self.assertEqual((stored.exchange_rate, stored.terms_accepted_at is not None), (D("2.6000"), True))
        self.assertEqual(list(OrderItem.objects.order_by("id").values("unit_price_usd", "unit_price", "list_unit_price")),
                         lines)  # snapshot per item
        self.assertEqual(stored.billing_snapshot["email"], "nino@example.com")
        self.assertFalse(CartItem.objects.exists())
        self.assertEqual(set(stored.items.values_list("item_status", flat=True)), {"created"})
        self.assertEqual(order["items"][0]["events"][0]["event_type"], "created")
        self.assertIsNotNone(order["items"][0]["expected_arrival_from"])

    def test_later_rate_changes_never_touch_the_order(self):
        self.fill_cart()
        order = self.checkout().json()["order"]
        ExchangeRate.objects.create(date=timezone.localdate() + timedelta(days=1), rate=D("3.1000"))
        from io import StringIO
        from django.core.management import call_command
        with mock.patch("pricing.rates.requests.get") as get:
            get.return_value.json.return_value = [{"currencies": [{"code": "USD", "quantity": 1, "rate": 3.2,
                                                                  "validFromDate": f"{timezone.localdate()}T00:00:00Z"}]}]
            call_command("fetch_nbg_rates", stdout=StringIO())
        stored = Order.objects.get()
        self.assertEqual((f"{stored.total:.2f}", stored.exchange_rate), (order["total"], D("2.6000")))
        self.assertEqual(f"{Payment.objects.get().amount:.2f}", order["total"])
        self.assertIn(order["total"].split(".")[0], self.client.get(
            f"/api/orders/{order['order_number']}/invoice/?lang=en").content.decode())

    def test_card_payment_is_started_after_commit(self):
        self.fill_cart()
        seen = {}

        baseline = len(connection.atomic_blocks)  # TestCase's own transaction

        def start(provider, payment):
            seen["depth"] = len(connection.atomic_blocks)
            return f"https://lionparts.ge/payment-result?order={payment.order.order_number}&payment={payment.pk}&fake=1"

        with mock.patch("payments.providers.FakeProvider.start", start):
            body = self.checkout(payment_method="card").json()
        self.assertEqual(seen["depth"], baseline)  # not inside checkout's transaction (no locks held)
        self.assertEqual((body["payment"]["provider"], body["payment"]["status"]), ("fake", "pending"))
        self.assertIn("payment-result?order=", body["payment"]["checkout_url"])
        self.assertNotIn("code", body)

    def test_card_start_failure_keeps_the_order(self):
        self.fill_cart()
        with mock.patch("payments.providers.FakeProvider.start", side_effect=PaymentInitError("down")):
            response = self.checkout(payment_method="card")
        body = response.json()
        self.assertEqual((response.status_code, body["code"], body["payment"]["status"], body["payment"]["checkout_url"]),
                         (201, "payment_init_failed", "failed", None))
        self.assertTrue(body["detail"])
        self.assertEqual(Order.objects.get().status, "payment_pending")

    def test_split_payment(self):
        CustomerTariff.objects.filter(is_default=True).update(payment_terms="split_70_30")
        self.fill_cart()
        total = D(self.client.get("/api/cart/").json()["summary"]["total"])
        body = self.checkout(payment_option="split_70_30").json()
        due = (total * D("0.7")).quantize(D("0.01"))
        self.assertEqual((body["order"]["amount_due_now"], body["payment"]["amount"]), (f"{due:.2f}",) * 2)

    def test_split_on_a_full_tariff_is_not_allowed(self):
        self.fill_cart()
        response = self.checkout(payment_option="split_70_30")
        self.assertEqual(response.json()["fields"]["payment_option"][0]["code"], "not_allowed")
        self.assertFalse(Order.objects.exists())

    def test_delivery_validation(self):
        self.fill_cart()
        codes = lambda r: {k: [e["code"] for e in v] for k, v in r.json()["fields"].items()}
        cases = [
            ({"city": "tbilisi", "address": "Street 1"}, {"delivery.district": ["required"]}),
            ({"city": "batumi", "district": "vake", "address": "x"}, {"delivery.city": ["invalid"]}),
            ({"city": "tbilisi", "district": "nowhere", "address": "x"}, {"delivery.district": ["invalid"]}),
            ({"city": "tbilisi", "district": "vake", "address": ""}, {"delivery.address": ["required"]}),
        ]
        for delivery, expected in cases:
            with self.subTest(delivery=delivery):
                self.assertEqual(codes(self.checkout(delivery_method="delivery", delivery=delivery)), expected)
        self.assertEqual(codes(self.checkout(delivery_method="delivery")),
                         {"delivery.city": ["required"], "delivery.district": ["required"], "delivery.address": ["required"]})
        ok = self.checkout(delivery_method="delivery", pickup_point_id=None,
                           delivery={"city": "tbilisi", "district": "vake", "address": "Street 1", "comment": "<b>x</b>"})
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.json()["order"]["delivery"]["district"], "vake")

    def test_other_validation(self):
        self.fill_cart()
        codes = lambda r: {k: [e["code"] for e in v] for k, v in r.json()["fields"].items()}
        self.assertEqual(codes(self.checkout(terms_accepted=False)), {"terms_accepted": ["required"]})
        self.assertEqual(codes(self.checkout(pickup_point_id=999)), {"pickup_point_id": ["invalid"]})
        self.assertEqual(codes(self.checkout(payment_method="cash")), {"payment_method": ["invalid"]})

    def test_client_prices_rates_and_states_are_ignored(self):
        self.fill_cart()
        expected = self.client.get("/api/cart/").json()["summary"]["total"]
        body = self.checkout(total="0.01", unit_price="0.01", exchange_rate="0.0001", amount_due_now="0.01",
                             status="processing", currency="USD").json()
        self.assertEqual((body["order"]["total"], body["order"]["status"], body["order"]["currency"]),
                         (expected, "payment_pending", "GEL"))

    def test_empty_cart_and_a_second_checkout(self):
        self.assertEqual(self.checkout().json()["code"], "cart_empty")
        self.fill_cart()
        self.assertEqual(self.checkout().status_code, 201)
        response = self.checkout()
        self.assertEqual((response.status_code, response.json()["code"]), (409, "cart_empty"))
        self.assertEqual(Order.objects.count(), 1)

    def test_expired_line_blocks_checkout(self):
        self.fill_cart(0, 1)
        expired = CartItem.objects.order_by("id").first()
        CartItem.objects.filter(pk=expired.pk).update(price_valid_until=timezone.now() - timedelta(seconds=1))
        response = self.checkout()
        self.assertEqual((response.status_code, response.json()["code"], response.json()["reasons"]),
                         (409, "cart_invalid", [{"item_id": expired.pk, "code": "expired"}]))
        self.assertEqual((Order.objects.count(), CartItem.objects.count()), (0, 2))

    def test_unavailable_rate_creates_nothing(self):
        self.fill_cart()
        self.rate.date = timezone.localdate() - timedelta(days=10)
        self.rate.save()
        with mock.patch("pricing.rates.fetch_nbg_usd", side_effect=RateUnavailable()):
            response = self.checkout()
        self.assertEqual((response.status_code, response.json()["code"]), (502, "rate_unavailable"))
        self.assertEqual((Order.objects.count(), CartItem.objects.count()), (0, 1))

    def test_rate_is_the_current_one_at_checkout(self):
        self.fill_cart()
        self.rate.rate = D("2.7000")
        self.rate.save()
        body = self.checkout().json()
        line_usd = OrderItem.objects.get().unit_price_usd
        self.assertEqual((Order.objects.get().exchange_rate, body["order"]["items"][0]["unit_price"]),
                         (D("2.7000"), f"{(line_usd * D('2.7')).quantize(D('0.01'))}"))

    def test_multi_currency_usd_order(self):
        SiteSettings.objects.update(multi_currency_enabled=True)
        self.fill_cart(currency="USD")
        body = self.checkout(payment_method="card").json()
        order = Order.objects.get()
        self.assertEqual((order.currency, order.exchange_rate, body["payment"]["currency"]), ("USD", D("1"), "USD"))
        self.assertEqual(body["payment"]["amount"], f"{order.items.get().unit_price_usd:.2f}")

    def test_multi_currency_off_forces_gel(self):
        SiteSettings.objects.update(multi_currency_enabled=True)
        self.fill_cart(currency="USD")
        self.client.patch("/api/cart/", {"currency": "USD"}, format="json")
        SiteSettings.objects.update(multi_currency_enabled=False)
        self.checkout()
        self.assertEqual((Order.objects.get().currency, Payment.objects.get().currency), ("GEL", "GEL"))

    def test_requires_a_fully_verified_customer(self):
        self.assertEqual(APIClient().post("/api/orders/checkout/", PICKUP, format="json").status_code, 401)
        client = APIClient()
        client.force_login(make_customer(email="u@example.com", phone="599000000", is_phone_verified=True).user)
        response = self.checkout(client)
        self.assertEqual((response.status_code, response.json()["code"]), (403, "verification_required"))


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL (CI)")
@override_settings(PARTS_PROVIDER="demo")
class ParallelCheckoutTests(CheckoutMixin, TransactionTestCase):
    serialized_rollback = True

    def test_two_parallel_checkouts_create_one_order(self):
        self.fill_cart()
        results, barrier = [], threading.Barrier(2)

        def run():
            client = APIClient()
            client.force_login(self.customer.user)
            barrier.wait()
            try:
                results.append(self.checkout(client).status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [201, 409])
        self.assertEqual(Order.objects.count(), 1)
