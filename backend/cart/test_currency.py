"""LP-BE-504: cart currency switch (G§7.7)."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from cart.models import CartItem
from pricing.engine import PriceInput, price_offer
from pricing.models import CurrencySettings, Discount, ExchangeRate

D = Decimal


@override_settings(PARTS_PROVIDER="demo")
class CurrencySwitchTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.client = APIClient()
        for index in (0, 1):
            data = self.client.post("/api/parts/search/", {"part_number": "G9010-60020"}, format="json").json()
            self.client.post("/api/cart/items/", {"source": "search", "search_id": data["search_id"],
                                                  "offer_id": data["offers"][index]["offer_id"]}, format="json")

    def switch(self, currency):
        return self.client.patch("/api/cart/", {"currency": currency}, format="json")

    def prices(self, cart):
        return [(i["list_unit_price"], i["unit_price"]) for i in cart["items"]]

    def test_round_trip_returns_the_original_prices(self):
        Discount.objects.create(name="fixed", kind="fixed", value=10, brand="OEM")  # fixed GEL, converted for USD
        gel_before = self.prices(self.switch("GEL").json())  # reprice once so the GEL lines carry the discount
        usd = self.switch("USD").json()
        self.assertEqual(usd["currency"], "USD")
        expected_usd = price_offer(PriceInput(D("180.00"), D("2.00"), D("8.00")), customer=None, currency="USD",
                                   part_number="G9010-60020", brand="OEM")
        self.assertEqual(usd["items"][0]["unit_price"], str(expected_usd.unit_price))
        self.assertEqual(usd["items"][0]["discount_unit_amount"], "3.85")  # 10 GEL / 2.6
        self.assertEqual(self.prices(self.switch("GEL").json()), gel_before)

    def test_validity_and_status_are_kept(self):
        before = {line.pk: line.price_valid_until for line in CartItem.objects.all()}
        self.switch("USD")
        for line in CartItem.objects.all():
            self.assertEqual((line.price_valid_until, line.availability_status), (before[line.pk], "ok"))

    def test_unsupported_or_inactive_currency(self):
        self.assertEqual(self.switch("EUR").json()["fields"]["currency"][0]["code"], "invalid")
        CurrencySettings.objects.filter(currency="USD").update(is_active=False)
        response = self.switch("USD")
        self.assertEqual((response.status_code, response.json()["code"]), (400, "validation_error"))
        self.assertEqual(self.client.get("/api/cart/").json()["currency"], "GEL")

    def test_guest_without_a_cart(self):
        response = APIClient().patch("/api/cart/", {"currency": "USD"}, format="json")
        self.assertEqual((response.status_code, response.json()["currency"], response.json()["items"]), (200, "USD", []))
