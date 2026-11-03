"""LP-BE-506: guest cart merge on login and registration (G§7.7 merge)."""

from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomerTariff, TariffMarkup
from accounts.test_register import PHYSICAL
from accounts.testing import PASSWORD, make_customer
from cart.models import Cart, CartItem
from pricing.models import ExchangeRate
from pricing.rates import RateUnavailable

D = Decimal


@override_settings(PARTS_PROVIDER="demo", SMS_DEMO_MODE=True, ENABLE_DEMO_ENDPOINTS=True)
class MergeTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.customer = make_customer()

    def add(self, client, index=0, quantity=1, part="PART-A"):
        data = client.post("/api/parts/search/", {"part_number": part}, format="json").json()
        return client.post("/api/cart/items/", {"source": "search", "search_id": data["search_id"],
                                                "offer_id": data["offers"][index]["offer_id"], "quantity": quantity},
                           format="json")

    def login(self, client):
        response = client.post("/api/auth/login/", {"email": "nino@example.com", "password": PASSWORD}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def customer_client(self):
        client = APIClient()
        client.force_login(self.customer.user)
        return client

    def test_same_part_is_summed_and_merge_is_returned_once(self):
        self.add(self.customer_client())
        guest = APIClient()
        self.add(guest)
        self.add(guest, index=1)
        self.login(guest)
        cart = guest.get("/api/cart/").json()
        self.assertEqual([i["quantity"] for i in cart["items"]], [2, 1])
        self.assertEqual(cart["merge"], {"merged": True, "added_items": 1,
                                         "summed": [{"part_number": "PART-A", "from": 1, "to": 2}], "problems": []})
        self.assertIsNone(guest.get("/api/cart/").json()["merge"])
        self.assertEqual(Cart.objects.count(), 1)  # the guest cart is gone

    def test_partner_sees_tariff_prices_on_guest_lines(self):
        partner = CustomerTariff.objects.create(name="Partner")
        TariffMarkup.objects.create(tariff=partner, currency="GEL", markup_percent=D("5"))
        self.customer.tariff = partner
        self.customer.save()
        guest = APIClient()
        guest_price = self.add(guest).json()["items"][0]["unit_price"]
        self.login(guest)
        item = guest.get("/api/cart/").json()["items"][0]
        self.assertEqual((item["status"], item["previous_unit_price"]), ("price_changed", guest_price))
        self.assertLess(D(item["unit_price"]), D(guest_price))

    def test_same_tariff_keeps_ok_status(self):
        guest = APIClient()
        self.add(guest)
        self.login(guest)
        self.assertEqual(guest.get("/api/cart/").json()["items"][0]["status"], "ok")

    def test_quantity_is_capped_at_99(self):
        self.add(self.customer_client(), quantity=60)
        guest = APIClient()
        self.add(guest, quantity=60)
        self.login(guest)
        cart = guest.get("/api/cart/").json()
        self.assertEqual(cart["items"][0]["quantity"], 99)
        self.assertEqual(cart["merge"]["summed"], [{"part_number": "PART-A", "from": 60, "to": 99}])

    def test_new_customer_cart_keeps_the_guest_currency(self):
        guest = APIClient()
        self.add(guest)
        guest.patch("/api/cart/", {"currency": "USD"}, format="json")
        self.login(guest)
        cart = guest.get("/api/cart/").json()
        self.assertEqual((cart["currency"], cart["items"][0]["status"]), ("USD", "ok"))

    def test_without_a_guest_cart_nothing_happens(self):
        guest = APIClient()
        self.login(guest)
        self.assertIsNone(guest.get("/api/cart/").json()["merge"])
        self.assertFalse(Cart.objects.exists())

    def test_registration_merges_too(self):
        self.customer.user.delete()
        guest = APIClient()
        self.add(guest)
        code = guest.post("/api/auth/register/", PHYSICAL, format="json").json()["demo_code"]
        guest.post("/api/auth/register/verify-phone/", {"code": code}, format="json")
        cart = guest.get("/api/cart/").json()
        self.assertEqual((len(cart["items"]), cart["merge"]["added_items"]), (1, 1))
        self.assertEqual(Cart.objects.get().customer.user.email, "nino@example.com")

    def test_rate_outage_does_not_break_login(self):
        guest = APIClient()
        self.add(guest)
        with mock.patch("cart.services.get_usd_rate", side_effect=RateUnavailable()):
            self.login(guest)
        cart = guest.get("/api/cart/").json()
        line = CartItem.objects.get()
        self.assertEqual(cart["merge"]["problems"], [{"item_id": line.pk, "code": "rate_unavailable"}])
        self.assertEqual(cart["items"][0]["status"], "ok")
