"""M2 review fix 1: an operator-approved prepared quote keeps its approved price in the cart."""

from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import TariffMarkup
from accounts.testing import PASSWORD, make_customer
from cart.models import CartItem
from parts.models import PartQuoteRequest
from parts.services import mark_quote_price_ready
from pricing.models import ExchangeRate

D = Decimal


@override_settings(PARTS_PROVIDER="demo")
class ApprovedQuotePriceTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.customer = make_customer(is_phone_verified=True)
        self.qr = PartQuoteRequest.objects.create(customer=self.customer, request_type="weight_price",
                                                  part_number="Q-1", brand="TOYOTA", supplier_price_usd=D("50"),
                                                  prepared_weight_kg=D("3"))
        mark_quote_price_ready(self.qr)
        self.qr.refresh_from_db()
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def add(self, client=None):
        return (client or self.client).post("/api/cart/items/", {"source": "prepared_quote",
                                                                 "quote_request_id": self.qr.pk}, format="json")

    def test_approved_snapshot_is_kept_after_config_changes(self):
        TariffMarkup.objects.filter(tariff__is_default=True).update(markup_percent=D("50"))
        ExchangeRate.objects.create(date=timezone.localdate() + timezone.timedelta(days=1), rate=D("3.0000"))
        self.assertEqual(self.add().status_code, 201)
        line = CartItem.objects.get()
        self.assertEqual((line.unit_price, line.markup_percent, line.exchange_rate, line.availability_status),
                         (self.qr.unit_price, self.qr.markup_percent, self.qr.exchange_rate, "ok"))

    def test_login_merge_does_not_reprice_the_quote_line(self):
        self.add()
        guest = APIClient()
        data = guest.post("/api/parts/search/", {"part_number": "G-1"}, format="json").json()
        guest.post("/api/cart/items/", {"source": "search", "search_id": data["search_id"],
                                        "offer_id": data["offers"][0]["offer_id"]}, format="json")
        TariffMarkup.objects.filter(tariff__is_default=True).update(markup_percent=D("50"))
        guest.post("/api/auth/login/", {"email": "nino@example.com", "password": PASSWORD}, format="json")
        quote_line = CartItem.objects.get(source="prepared_quote")
        self.assertEqual((quote_line.unit_price, quote_line.availability_status, quote_line.previous_unit_price),
                         (self.qr.unit_price, "ok", None))
        search_line = CartItem.objects.get(source="search")
        self.assertEqual(search_line.availability_status, "price_changed")  # ordinary lines still reprice

    def test_quote_in_another_currency_than_the_cart_is_not_converted(self):
        self.client.patch("/api/cart/", {"currency": "USD"}, format="json")
        response = self.add()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["fields"]["quote_request_id"][0]["code"], "not_allowed")
        self.assertFalse(CartItem.objects.exists())
        self.client.patch("/api/cart/", {"currency": "GEL"}, format="json")
        self.assertEqual(self.add().status_code, 201)
