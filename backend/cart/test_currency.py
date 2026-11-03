"""Cart currency (owner-approved rules, G§4.6/§7.7): USD is canonical; GEL is a conversion at the
current NBG rate; switching is possible only with multi-currency enabled."""

from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomerTariff
from accounts.testing import make_customer
from cart.models import CartItem
from pricing.models import Discount, ExchangeRate, SiteSettings

D = Decimal


def round2(value):
    return value.quantize(D("0.01"))


@override_settings(PARTS_PROVIDER="demo")
class CurrencyModeTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.client = APIClient()

    def add(self, index=0, **search):
        data = self.client.post("/api/parts/search/", {"part_number": "G9010-60020", **search}, format="json").json()
        return self.client.post("/api/cart/items/", {"source": "search", "search_id": data["search_id"],
                                                     "offer_id": data["offers"][index]["offer_id"]}, format="json")

    def switch(self, currency):
        return self.client.patch("/api/cart/", {"currency": currency}, format="json")

    def test_multi_currency_off_everything_is_gel_and_switching_is_refused(self):
        self.add()
        self.assertEqual(self.client.get("/api/cart/").json()["currency"], "GEL")
        for currency in ("USD", "EUR"):
            response = self.switch(currency)
            self.assertEqual((response.status_code, response.json()["fields"]["currency"][0]["code"]), (400, "invalid"))
        self.assertEqual(self.client.get("/api/cart/").json()["currency"], "GEL")

    def test_multi_currency_on_usd_is_the_default(self):
        SiteSettings.objects.update(multi_currency_enabled=True)
        self.assertEqual(APIClient().get("/api/cart/").json()["currency"], "USD")
        self.add()
        line = CartItem.objects.get()
        self.assertEqual((line.cart.currency, line.unit_price, line.exchange_rate), ("USD", line.unit_price_usd, D("1")))

    def test_switching_converts_from_the_canonical_usd_price(self):
        SiteSettings.objects.update(multi_currency_enabled=True)
        Discount.objects.create(name="-7 USD", kind="fixed", value=7, brand="OEM")
        self.add(index=0)
        self.add(index=1)
        usd = self.client.get("/api/cart/").json()
        gel = self.switch("GEL").json()
        for u, g in zip(usd["items"], gel["items"]):
            self.assertEqual(g["unit_price"], f"{round2(D(u['unit_price']) * D('2.6'))}")
            self.assertEqual(g["list_unit_price"], f"{round2(D(u['list_unit_price']) * D('2.6'))}")
            self.assertEqual(D(g["list_unit_price"]) - D(g["discount_unit_amount"]), D(g["unit_price"]))
            self.assertEqual((g["status"], g["previous_unit_price"]), ("ok", None))  # a conversion is no price change
        self.assertEqual(gel["items"][0]["discount_unit_amount"], "18.20")  # 7 USD x 2.6
        back = self.switch("USD").json()
        self.assertEqual([i["unit_price"] for i in back["items"]], [i["unit_price"] for i in usd["items"]])
        usd_amounts = list(CartItem.objects.values_list("unit_price_usd", "price_valid_until"))
        self.switch("GEL")
        self.assertEqual(list(CartItem.objects.values_list("unit_price_usd", "price_valid_until")), usd_amounts)

    def test_disabling_multi_currency_moves_usd_carts_to_gel(self):
        SiteSettings.objects.update(multi_currency_enabled=True)
        self.add()
        SiteSettings.objects.update(multi_currency_enabled=False)
        cart = self.client.get("/api/cart/").json()
        line = CartItem.objects.get()
        self.assertEqual((cart["currency"], cart["items"][0]["unit_price"]),
                         ("GEL", f"{round2(line.unit_price_usd * D('2.6'))}"))

    def test_customer_tariffs_and_discounts_carry_into_both_currencies(self):
        SiteSettings.objects.update(multi_currency_enabled=True)
        partner = CustomerTariff.objects.create(name="Partner", markup_percent=D("5"))
        customer = make_customer(tariff=partner)
        Discount.objects.create(name="Nino 10%", kind="percent", value=10, customer=customer)
        self.client.force_login(customer.user)
        self.add()
        line = CartItem.objects.get()
        # (180 + 2 x 8) x 1.05 x 1.18 = 242.844 -> 242.84; 10 % = 24.28; unit 218.56 USD
        self.assertEqual((line.list_price_usd, line.discount_usd, line.unit_price_usd),
                         (D("242.84"), D("24.28"), D("218.56")))
        gel = self.switch("GEL").json()["items"][0]
        self.assertEqual((gel["unit_price"], gel["list_unit_price"]), ("568.26", "631.38"))  # x 2.6


@override_settings(PARTS_PROVIDER="demo")
class ModeSwitchedOffShowsGelEverywhereTests(TestCase):
    def test_usd_quotes_and_searches_are_shown_in_gel_once_the_mode_is_off(self):
        from parts.models import PartQuoteRequest
        from parts.services import mark_quote_price_ready

        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        SiteSettings.objects.update(multi_currency_enabled=True)
        customer = make_customer(is_phone_verified=True)
        client = APIClient()
        client.force_login(customer.user)
        client.post("/api/parts/search/", {"part_number": "USD-1"}, format="json")  # a USD search
        qr = PartQuoteRequest.objects.create(customer=customer, request_type="weight_price", part_number="Q-1",
                                             supplier_price_usd=D("50"), prepared_weight_kg=D("3"), currency="USD")
        mark_quote_price_ready(qr)
        qr.refresh_from_db()
        SiteSettings.objects.update(multi_currency_enabled=False)
        listed = client.get("/api/parts/quote-requests/").json()["results"][0]
        self.assertEqual((listed["currency"], listed["unit_price"]),
                         ("GEL", f"{round2(qr.unit_price_usd * D('2.6'))}"))
        self.assertEqual({i["currency"] for i in client.get("/api/parts/history/").json()["results"]}, {"GEL"})
        public = APIClient().get(f"/api/public/q/{qr.notification_token}/").json()
        self.assertEqual(public["currency"], "GEL")
