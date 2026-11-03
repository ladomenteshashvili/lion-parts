"""M2 review fix 3: an offer from a guest search, added after login, is priced for the customer."""

from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomerTariff
from accounts.testing import PASSWORD, make_customer
from cart.models import CartItem
from pricing.engine import PriceInput, convert, price_offer
from pricing.models import Discount, ExchangeRate

D = Decimal


@override_settings(PARTS_PROVIDER="demo")
class GuestSearchAddedAfterLoginTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        partner = CustomerTariff.objects.create(name="Partner", markup_percent=D("5"))
        self.customer = make_customer(tariff=partner)
        self.client = APIClient()

    def search(self):
        return self.client.post("/api/parts/search/", {"part_number": "G9010-60020"}, format="json").json()

    def add(self, data):
        return self.client.post("/api/cart/items/", {"source": "search", "search_id": data["search_id"],
                                                     "offer_id": data["offers"][0]["offer_id"]}, format="json").json()

    def login(self):
        self.client.post("/api/auth/login/", {"email": "nino@example.com", "password": PASSWORD}, format="json")

    def partner_price(self):
        return convert(price_offer(PriceInput(D("180.00"), D("2.00"), D("8.00")), customer=self.customer,
                                   part_number="G9010-60020", brand="OEM"), "GEL", D("2.6")).unit_price

    def test_partner_gets_their_tariff_price_not_the_guest_price(self):
        guest_search = self.search()
        guest_price = guest_search["offers"][0]["unit_price"]
        self.login()
        item = self.add(guest_search)["items"][0]
        self.assertEqual(item["unit_price"], str(self.partner_price()))
        self.assertEqual((item["status"], item["previous_unit_price"]), ("price_changed", guest_price))
        self.assertEqual(CartItem.objects.get().markup_percent, D("5.00"))

    def test_customer_discount_applies_after_login(self):
        Discount.objects.create(name="Nino -7%", kind="percent", value=7, customer=self.customer)
        guest_search = self.search()
        self.login()
        line = self.add(guest_search) and CartItem.objects.get()
        self.assertGreater(line.discount_unit_amount, 0)
        self.assertIsNotNone(line.discount_id)

    def test_own_search_is_not_repriced(self):
        self.login()
        own_search = self.search()
        item = self.add(own_search)["items"][0]
        self.assertEqual((item["unit_price"], item["status"], item["previous_unit_price"]),
                         (own_search["offers"][0]["unit_price"], "ok", None))

    def test_guest_keeps_the_guest_price(self):
        guest_search = self.search()
        item = self.add(guest_search)["items"][0]
        self.assertEqual((item["unit_price"], item["status"]), (guest_search["offers"][0]["unit_price"], "ok"))

    def test_regular_customer_same_price_stays_ok(self):
        CustomerTariff.objects.filter(name="Partner").delete()  # customer falls back to the default tariff
        guest_search = self.search()
        self.login()
        item = self.add(guest_search)["items"][0]
        self.assertEqual((item["status"], item["unit_price"]), ("ok", guest_search["offers"][0]["unit_price"]))
