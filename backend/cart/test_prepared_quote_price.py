"""Prepared quotes (owner-approved rules): the operator-approved USD price is the base while the quote
is valid; GEL is converted at the current rate (also in the cart); an expired quote needs operator
re-approval."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomerTariff
from accounts.testing import PASSWORD, make_customer
from cart.models import CartItem
from parts.models import PartQuoteRequest
from parts.services import mark_quote_price_ready
from pricing.models import ExchangeRate, SiteSettings

D = Decimal


@override_settings(PARTS_PROVIDER="demo")
class ApprovedQuoteTests(TestCase):
    def setUp(self):
        self.rate = ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.customer = make_customer(is_phone_verified=True)
        self.qr = PartQuoteRequest.objects.create(customer=self.customer, request_type="weight_price",
                                                  part_number="Q-1", brand="TOYOTA", supplier_price_usd=D("50"),
                                                  prepared_weight_kg=D("3"), currency="GEL")
        mark_quote_price_ready(self.qr)
        self.qr.refresh_from_db()
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def add(self):
        return self.client.post("/api/cart/items/", {"source": "prepared_quote", "quote_request_id": self.qr.pk},
                                format="json")

    def new_rate(self, value):
        self.rate.rate = D(value)
        self.rate.save()

    def test_the_usd_base_never_changes_but_gel_follows_the_rate(self):
        self.assertEqual(self.add().status_code, 201)
        CustomerTariff.objects.filter(is_default=True).update(markup_percent=D("50"))  # config changes later
        self.new_rate("2.8000")
        item = self.client.get("/api/cart/").json()["items"][0]
        line = CartItem.objects.get()
        self.assertEqual(line.unit_price_usd, self.qr.unit_price_usd)  # operator-approved base kept
        self.assertEqual(item["unit_price"], f"{(self.qr.unit_price_usd * D('2.8')).quantize(D('0.01'))}")
        self.assertEqual((item["status"], item["previous_unit_price"]), ("ok", None))
        listed = self.client.get("/api/parts/quote-requests/").json()["results"][0]
        self.assertEqual(listed["unit_price"], item["unit_price"])  # the quote itself shows today's GEL too

    def test_quote_in_usd_into_a_gel_cart_is_converted(self):
        SiteSettings.objects.update(multi_currency_enabled=True)
        PartQuoteRequest.objects.filter(pk=self.qr.pk).update(currency="USD")
        self.client.patch("/api/cart/", {"currency": "GEL"}, format="json")
        self.assertEqual(self.add().status_code, 201)
        line = CartItem.objects.get()
        self.assertEqual((line.unit_price_usd, line.unit_price),
                         (self.qr.unit_price_usd, (self.qr.unit_price_usd * D("2.6")).quantize(D("0.01"))))

    def test_validity_is_the_quote_validity(self):
        self.add()
        self.assertEqual(CartItem.objects.get().price_valid_until, self.qr.price_valid_until)

    def test_expired_quote_needs_operator_reapproval(self):
        PartQuoteRequest.objects.filter(pk=self.qr.pk).update(price_valid_until=timezone.now() - timedelta(seconds=1))
        response = self.add()
        self.assertEqual((response.status_code, response.json()["code"]), (409, "offer_expired"))
        self.assertEqual(APIClient().get(f"/api/public/q/{self.qr.notification_token}/").status_code, 404)
        history = self.client.get("/api/parts/history/").json()["results"][0]
        self.assertEqual(history["status"], "expired")
        self.qr.refresh_from_db()
        mark_quote_price_ready(self.qr)  # the operator re-approves
        self.assertEqual(self.add().status_code, 201)

    def test_expired_line_takes_a_reapproval_on_refresh_without_a_supplier_call(self):
        self.add()
        CartItem.objects.update(price_valid_until=timezone.now() - timedelta(seconds=1))
        PartQuoteRequest.objects.filter(pk=self.qr.pk).update(price_valid_until=timezone.now() - timedelta(seconds=1))
        line_id = CartItem.objects.get().pk
        self.assertEqual(self.client.post(f"/api/cart/items/{line_id}/refresh/").json()["items"][0]["status"], "expired")
        self.qr.refresh_from_db()
        mark_quote_price_ready(self.qr)
        item = self.client.post(f"/api/cart/items/{line_id}/refresh/").json()["items"][0]
        self.assertEqual(item["status"], "ok")

    def test_login_merge_keeps_the_usd_base(self):
        self.add()
        CustomerTariff.objects.filter(is_default=True).update(markup_percent=D("50"))
        guest = APIClient()
        data = guest.post("/api/parts/search/", {"part_number": "G-1"}, format="json").json()
        guest.post("/api/cart/items/", {"source": "search", "search_id": data["search_id"],
                                        "offer_id": data["offers"][0]["offer_id"]}, format="json")
        guest.post("/api/auth/login/", {"email": "nino@example.com", "password": PASSWORD}, format="json")
        quote_line = CartItem.objects.get(source="prepared_quote")
        self.assertEqual((quote_line.unit_price_usd, quote_line.availability_status), (self.qr.unit_price_usd, "ok"))
