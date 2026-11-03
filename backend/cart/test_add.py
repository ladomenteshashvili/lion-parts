"""LP-BE-502: add to cart by reference, never by price (G§7.7, G§8.5).
Restores the legacy add_cart_item / add_same_cart_item scenarios."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomerTariff
from accounts.testing import make_customer
from cart.models import Cart, CartItem
from parts.models import PartQuoteRequest, PartSearchLog
from parts.services import mark_quote_price_ready
from pricing.engine import PriceInput, price_offer
from pricing.models import ExchangeRate

D = Decimal
CART_KEYS = {"currency", "items", "summary", "payment_terms", "checkout_blocked", "checkout_blocked_reasons", "merge"}


@override_settings(PARTS_PROVIDER="demo")
class AddToCartTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.client = APIClient()

    def search(self, client=None, **body):
        data = (client or self.client).post("/api/parts/search/", {"part_number": "G9010-60020", **body},
                                            format="json").json()
        return data["search_id"], data["offers"]

    def add(self, client=None, **body):
        return (client or self.client).post("/api/cart/items/", body, format="json")

    def test_adds_from_the_stored_offer_and_ignores_price_fields(self):
        search_id, offers = self.search()
        response = self.add(source="search", search_id=search_id, offer_id=offers[0]["offer_id"], quantity=2,
                            unit_price="0.01", final_price_gel="0.01", list_unit_price="0.01", line_total="0.02",
                            discount_unit_amount="999")
        self.assertEqual(response.status_code, 201, response.content)
        cart = response.json()
        self.assertEqual(set(cart), CART_KEYS)
        item = cart["items"][0]
        self.assertEqual((item["unit_price"], item["quantity"], item["line_total"]),
                         (offers[0]["unit_price"], 2, f"{D(offers[0]['unit_price']) * 2:.2f}"))
        line = CartItem.objects.get()
        self.assertEqual((line.unit_price, line.supplier_price_usd, line.exchange_rate),
                         (D(offers[0]["unit_price"]), D("180.00"), D("2.6000")))
        self.assertEqual(line.cart.session_key, self.client.session.session_key)

    def test_same_offer_adds_quantity_capped_at_99(self):
        search_id, offers = self.search()
        body = {"source": "search", "search_id": search_id, "offer_id": offers[0]["offer_id"]}
        self.add(**body, quantity=1)
        self.assertEqual(self.add(**body, quantity=1).json()["items"][0]["quantity"], 2)
        response = self.add(**body, quantity=98)
        self.assertEqual(response.json()["fields"]["quantity"][0]["code"], "invalid_quantity")
        self.assertEqual(CartItem.objects.get().quantity, 2)

    def test_invalid_quantity(self):
        search_id, offers = self.search()
        for quantity in (0, 100, "x", -1):
            with self.subTest(quantity=quantity):
                response = self.add(source="search", search_id=search_id, offer_id=offers[0]["offer_id"],
                                    quantity=quantity)
                self.assertEqual(response.json()["fields"]["quantity"][0]["code"], "invalid_quantity")

    def test_another_sessions_search_is_404(self):
        search_id, offers = self.search(APIClient())
        response = self.add(source="search", search_id=search_id, offer_id=offers[0]["offer_id"])
        self.assertEqual((response.status_code, response.json()["code"]), (404, "not_found"))
        self.assertEqual(self.add(source="search", search_id=search_id + 1000, offer_id="x").status_code, 404)

    def test_unknown_offer_404_expired_409_checking_409(self):
        search_id, offers = self.search()
        self.assertEqual(self.add(source="search", search_id=search_id, offer_id="NOPE").status_code, 404)
        response = self.add(source="search", search_id=search_id, offer_id=offers[-1]["offer_id"])
        self.assertEqual((response.status_code, response.json()["code"]), (409, "offer_not_ready"))
        PartSearchLog.objects.filter(pk=search_id).update(price_valid_until=timezone.now() - timedelta(seconds=1))
        response = self.add(source="search", search_id=search_id, offer_id=offers[0]["offer_id"])
        self.assertEqual((response.status_code, response.json()["code"]), (409, "offer_expired"))

    def test_required_fields(self):
        codes = lambda r: {k: [e["code"] for e in v] for k, v in r.json()["fields"].items()}
        self.assertEqual(codes(self.add(source="search")), {"search_id": ["required"], "offer_id": ["required"]})
        self.assertEqual(codes(self.add(source="prepared_quote")), {"quote_request_id": ["required"]})
        self.assertEqual(codes(self.add(source="other")), {"source": ["invalid"]})

    def test_search_offer_in_another_currency_is_repriced_into_the_cart_currency(self):
        self.add(**{"source": "search", "search_id": self.search(currency="USD")[0], "offer_id": "DEMO-1-G9010-60020"})
        line = CartItem.objects.get()
        self.assertEqual(line.cart.currency, "GEL")
        expected = price_offer(PriceInput(D("180.00"), D("2.00"), D("8.00")), customer=None, currency="GEL",
                               part_number="G9010-60020", brand="OEM")
        self.assertEqual(line.unit_price, expected.unit_price)

    def test_search_shows_in_cart_quantity(self):
        search_id, offers = self.search()
        self.add(source="search", search_id=search_id, offer_id=offers[0]["offer_id"], quantity=3)
        _, offers = self.search()
        self.assertEqual([o["in_cart_quantity"] for o in offers], [3, 0, 0, 0])

    def test_csrf_is_required(self):
        response = self.add(APIClient(enforce_csrf_checks=True), source="search", search_id=1, offer_id="x")
        self.assertEqual(response.json()["code"], "csrf_failed")


@override_settings(PARTS_PROVIDER="demo")
class AddCustomerSourcesTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.customer = make_customer(is_phone_verified=True)
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def test_weight_calculation_result_keeps_the_customer_weight(self):
        CustomerTariff.objects.filter(is_default=True).update(can_enter_weight=True)
        data = self.client.post("/api/parts/search/", {"part_number": "P-1"}, format="json").json()
        calc = self.client.post("/api/parts/calculate-price/", {
            "search_id": data["search_id"], "offer_id": data["offers"][-1]["offer_id"], "weight_kg": "2.4",
            "weight_consent": True}, format="json").json()
        cart = self.client.post("/api/cart/items/", {"source": "search", "search_id": calc["search_id"],
                                                     "offer_id": calc["offer"]["offer_id"]}, format="json").json()
        item = cart["items"][0]
        self.assertEqual((item["weight_kg"], item["weight_source"], item["unit_price"]),
                         ("2.40", "customer", calc["offer"]["unit_price"]))
        self.assertTrue(item["customer_notice"])
        self.assertEqual(Cart.objects.get().customer, self.customer)

    def test_prepared_quote(self):
        qr = PartQuoteRequest.objects.create(customer=self.customer, request_type="weight_price", part_number="Q-1",
                                             name="Hose", brand="TOYOTA", supplier_price_usd=D("50"),
                                             prepared_weight_kg=D("3"))
        response = self.client.post("/api/cart/items/", {"source": "prepared_quote", "quote_request_id": qr.pk},
                                    format="json")
        self.assertEqual((response.status_code, response.json()["code"]), (409, "offer_not_ready"))
        mark_quote_price_ready(qr)
        response = self.client.post("/api/cart/items/", {"source": "prepared_quote", "quote_request_id": qr.pk},
                                    format="json")
        self.assertEqual(response.status_code, 201)
        item = response.json()["items"][0]
        qr.refresh_from_db()
        self.assertEqual((item["source"], item["unit_price"], item["weight_kg"], item["weight_source"]),
                         ("prepared_quote", f"{qr.unit_price:.2f}", "3.00", "operator"))
        line = CartItem.objects.get()
        self.assertGreater(line.price_valid_until, timezone.now() + timedelta(minutes=59))

    def test_someone_elses_or_a_guests_prepared_quote_is_404(self):
        other = make_customer(email="o@example.com", phone="599000000")
        qr = PartQuoteRequest.objects.create(customer=other, request_type="weight_price", part_number="Q-1",
                                             supplier_price_usd=D("50"), prepared_weight_kg=D("3"))
        mark_quote_price_ready(qr)
        body = {"source": "prepared_quote", "quote_request_id": qr.pk}
        self.assertEqual(self.client.post("/api/cart/items/", body, format="json").status_code, 404)
        self.assertEqual(APIClient().post("/api/cart/items/", body, format="json").status_code, 404)
