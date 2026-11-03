"""Exchange-rate changes (owner-approved rules): uncommitted GEL prices follow the current NBG rate,
canonical USD prices never do; an unavailable rate fails safely; orders are never touched."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

import requests

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import make_customer
from cart.models import CartItem
from orders.models import Order, Payment
from parts.models import PartSearchLog
from pricing.models import ExchangeRate, SiteSettings
from pricing.rates import RateUnavailable

D = Decimal


def nbg(rate):
    response = mock.Mock(status_code=200)
    response.json.return_value = [{"currencies": [{"code": "USD", "quantity": 1, "rate": rate,
                                                   "validFromDate": f"{timezone.localdate()}T00:00:00.000Z"}]}]
    response.raise_for_status.return_value = None
    return mock.patch("pricing.rates.requests.get", return_value=response)


@override_settings(PARTS_PROVIDER="demo")
class RateChangeTests(TestCase):
    def setUp(self):
        self.rate = ExchangeRate.objects.create(date=timezone.localdate() - timedelta(days=1), rate=D("2.6000"))
        self.client = APIClient()
        data = self.client.post("/api/parts/search/", {"part_number": "G9010-60020"}, format="json").json()
        self.search = data
        self.client.post("/api/cart/items/", {"source": "search", "search_id": data["search_id"],
                                              "offer_id": data["offers"][0]["offer_id"]}, format="json")
        self.line = CartItem.objects.get()

    def gel(self, rate):
        return f"{(self.line.unit_price_usd * D(rate)).quantize(D('0.01'))}"

    def test_reading_the_cart_uses_the_current_rate(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.7000"))
        cart = self.client.get("/api/cart/").json()
        self.assertEqual((cart["items"][0]["unit_price"], cart["items"][0]["status"]), (self.gel("2.7"), "ok"))
        line = CartItem.objects.get()
        self.assertEqual((line.unit_price_usd, line.exchange_rate, line.price_valid_until),
                         (self.line.unit_price_usd, D("2.7000"), self.line.price_valid_until))  # validity not extended

    def test_search_results_and_history_show_the_current_rate(self):
        customer = make_customer()
        client = APIClient()
        client.force_login(customer.user)
        data = client.post("/api/parts/search/", {"part_number": "H-1"}, format="json").json()
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.7000"))
        item = client.get("/api/parts/history/").json()["results"][0]
        cheapest_usd = min(o["snapshot"]["unit_price_usd"] for o in PartSearchLog.objects.get(pk=data["search_id"]).offers()
                           if o["snapshot"])
        self.assertEqual(item["status"], "ready")  # FX alone is not a price change
        self.assertEqual(item["unit_price"], f"{(D(cheapest_usd) * D('2.7')).quantize(D('0.01'))}")

    def test_command_refreshes_gel_carts_and_is_idempotent(self):
        with nbg(2.7):
            out = StringIO()
            call_command("fetch_nbg_rates", stdout=out)
        self.assertIn("updated", out.getvalue())
        self.assertIn("1 GEL cart line", out.getvalue())
        line = CartItem.objects.get()
        self.assertEqual((line.exchange_rate, f"{line.unit_price:.2f}", line.unit_price_usd),
                         (D("2.7000"), self.gel("2.7"), self.line.unit_price_usd))
        with nbg(2.7):
            out = StringIO()
            call_command("fetch_nbg_rates", stdout=out)
        self.assertIn("unchanged", out.getvalue())

    def test_unavailable_rate_is_not_an_unchanged_rate(self):
        ExchangeRate.objects.update(date=timezone.localdate() - timedelta(days=10))  # stale
        with mock.patch("pricing.rates.requests.get", side_effect=requests.ConnectionError), \
                self.assertRaises(CommandError) as ctx, \
                self.assertLogs("pricing.rates", "WARNING"):
            call_command("fetch_nbg_rates", stdout=StringIO())
        self.assertIn("unavailable", str(ctx.exception))
        with mock.patch("pricing.rates.fetch_nbg_usd", side_effect=RateUnavailable()):
            response = self.client.get("/api/cart/")
        self.assertEqual((response.status_code, response.json()["code"]), (502, "rate_unavailable"))
        self.assertEqual(CartItem.objects.get().unit_price, self.line.unit_price)  # nothing silently changed

    def test_usd_carts_need_no_rate(self):
        SiteSettings.objects.update(multi_currency_enabled=True)
        self.client.patch("/api/cart/", {"currency": "USD"}, format="json")
        ExchangeRate.objects.update(date=timezone.localdate() - timedelta(days=10))
        with mock.patch("pricing.rates.fetch_nbg_usd", side_effect=RateUnavailable()):
            response = self.client.get("/api/cart/")
        self.assertEqual((response.status_code, response.json()["items"][0]["unit_price"]),
                         (200, f"{self.line.unit_price_usd:.2f}"))

    def test_orders_and_payments_are_never_touched(self):
        order = Order.objects.create(order_number="LP-2026-00001", session_id="s", customer_name="Nino",
                                     customer_phone="555123456", payment_type=Order.PAYMENT_FULL,
                                     status=Order.STATUS_PAYMENT_PENDING, total_gel=D("427.08"))
        payment = Payment.objects.create(order=order, payment_reference="PAY-1", provider=Payment.PROVIDER_DEMO,
                                         amount_gel=D("427.08"))
        before = (Order.objects.values().get(), Payment.objects.values().get())
        with nbg(3.1):
            call_command("fetch_nbg_rates", stdout=StringIO())
        self.client.get("/api/cart/")
        self.assertEqual((Order.objects.values().get(), Payment.objects.values().get()), before)
        self.assertEqual(payment.amount_gel, D("427.08"))
