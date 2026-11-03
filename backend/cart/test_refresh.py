"""LP-BE-505: refresh a cart line (G§7.5)."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomerTariff
from accounts.testing import make_customer
from cart.models import CartItem
from parts.amt_provider import AMTProviderError
from parts.models import PartSearchLog
from parts.providers import DEMO_ROWS
from pricing.models import ExchangeRate

D = Decimal


def rows(**changes_by_index):
    out = [dict(r) for r in DEMO_ROWS]
    for index, change in changes_by_index.items():
        out[int(index[1:])].update(change)
    return out


@override_settings(PARTS_PROVIDER="demo")
class RefreshTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.client = APIClient()
        data = self.client.post("/api/parts/search/", {"part_number": "G9010-60020"}, format="json").json()
        cart = self.client.post("/api/cart/items/", {"source": "search", "search_id": data["search_id"],
                                                     "offer_id": data["offers"][0]["offer_id"]}, format="json").json()
        self.item_id = cart["items"][0]["id"]
        CartItem.objects.update(price_valid_until=timezone.now() - timedelta(minutes=1))  # expired line

    def refresh(self, demo_rows=None):
        with mock.patch("parts.providers.DEMO_ROWS", DEMO_ROWS if demo_rows is None else demo_rows):
            return self.client.post(f"/api/cart/items/{self.item_id}/refresh/")

    def test_same_price_is_ok_and_valid_again(self):
        before = CartItem.objects.get()
        cart = self.refresh().json()
        item = cart["items"][0]
        self.assertEqual((item["status"], item["previous_unit_price"], item["unit_price"]),
                         ("ok", None, f"{before.unit_price:.2f}"))
        self.assertFalse(cart["checkout_blocked"])
        line = CartItem.objects.get()
        self.assertGreater(line.price_valid_until, timezone.now())
        self.assertEqual(line.search_log, PartSearchLog.objects.latest("id"))  # saved as a new log

    def test_supplier_price_change_is_price_changed_with_the_old_price(self):
        old = CartItem.objects.get().unit_price
        item = self.refresh(rows(r0={"supplier_price_usd": "200.00"})).json()["items"][0]
        self.assertEqual((item["status"], item["previous_unit_price"]), ("price_changed", f"{old:.2f}"))
        self.assertGreater(D(item["unit_price"]), old)

    def test_offer_gone_is_unavailable_and_blocks_checkout(self):
        cart = self.refresh([]).json()  # the supplier returns nothing for the part any more
        self.assertEqual(cart["items"][0]["status"], "unavailable")
        self.assertEqual(cart["checkout_blocked_reasons"], [{"item_id": self.item_id, "code": "unavailable"}])

    def test_same_brand_fallback_when_the_offer_id_changed(self):
        CartItem.objects.update(offer_id="AMT-9-RENUMBERED")  # the supplier no longer returns this id
        item = self.refresh().json()["items"][0]  # -> first offer of the same brand (OEM, same price)
        self.assertEqual(item["status"], "ok")

    @override_settings(PARTS_PROVIDER="amt")
    def test_supplier_failure_is_502_and_the_line_is_unchanged(self):
        before = CartItem.objects.values().get()
        with mock.patch("parts.providers.get_price_by_oem", side_effect=AMTProviderError("down")):
            response = self.client.post(f"/api/cart/items/{self.item_id}/refresh/")
        self.assertEqual((response.status_code, response.json()["code"]), (502, "supplier_unavailable"))
        self.assertEqual(CartItem.objects.values().get(), before)

    def test_another_carts_line_is_404(self):
        self.assertEqual(APIClient().post(f"/api/cart/items/{self.item_id}/refresh/").status_code, 404)

    def test_refresh_shares_the_search_throttle(self):
        statuses = [self.refresh().status_code for _ in range(30)]  # setUp used 1 of the 30/min
        self.assertEqual((statuses[-2], statuses[-1]), (200, 429))



@override_settings(PARTS_PROVIDER="demo")
class RefreshKeepsTheCustomerWeightTests(TestCase):
    def test_customer_weight_line(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        CustomerTariff.objects.filter(is_default=True).update(can_enter_weight=True)
        client = APIClient()
        client.force_login(make_customer().user)
        data = client.post("/api/parts/search/", {"part_number": "P-1"}, format="json").json()
        calc = client.post("/api/parts/calculate-price/", {"search_id": data["search_id"],
                           "offer_id": data["offers"][-1]["offer_id"], "weight_kg": "2.4", "weight_consent": True},
                           format="json").json()
        cart = client.post("/api/cart/items/", {"source": "search", "search_id": calc["search_id"],
                                                "offer_id": calc["offer"]["offer_id"]}, format="json").json()
        item = client.post(f"/api/cart/items/{cart['items'][0]['id']}/refresh/").json()["items"][0]
        self.assertEqual((item["status"], item["weight_kg"], item["weight_source"]), ("ok", "2.40", "customer"))
