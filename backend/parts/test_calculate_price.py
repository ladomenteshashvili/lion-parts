"""LP-BE-405: weight price calculation (G§8.4 calculate-price, G§7.6).
Restores the legacy calculate_part_price_* scenarios."""

from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone, translation
from rest_framework.test import APIClient

from accounts.models import CustomerTariff
from accounts.testing import make_customer
from orders.texts import EVENT_TEXTS
from parts.models import PartSearchLog
from parts.providers import DEMO_ROWS
from pricing.engine import PriceInput, price_offer
from pricing.models import ExchangeRate

D = Decimal


@override_settings(PARTS_PROVIDER="demo")
class CalculatePriceTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        CustomerTariff.objects.filter(is_default=True).update(can_enter_weight=True)
        self.customer = make_customer()
        self.client = APIClient()
        self.client.force_login(self.customer.user)
        data = self.client.post("/api/parts/search/", {"part_number": "68275354AC"}, format="json").json()
        self.search_id, self.offer_id = data["search_id"], data["offers"][-1]["offer_id"]  # checking row

    def calc(self, client=None, **body):
        payload = {"search_id": self.search_id, "offer_id": self.offer_id, "weight_kg": "2.4", "weight_consent": True}
        return (client or self.client).post("/api/parts/calculate-price/", {**payload, **body}, format="json",
                                            HTTP_ACCEPT_LANGUAGE="en")

    def test_prices_the_offer_with_the_customer_weight(self):
        response = self.calc()
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(set(data), {"search_id", "price_valid_until", "offer"})
        offer = data["offer"]
        expected = price_offer(PriceInput(D(DEMO_ROWS[-1]["supplier_price_usd"]), D("2.4"), D("8.00")),
                               customer=self.customer, currency="GEL", part_number="68275354AC", brand="Denso")
        self.assertEqual((offer["status"], offer["unit_price"], offer["weight_kg"], offer["weight_source"]),
                         ("ready", str(expected.unit_price), "2.40", "customer"))
        with translation.override("en"):
            self.assertEqual(offer["customer_notice"], str(EVENT_TEXTS["weight_customer_notice"]))
        log = PartSearchLog.objects.get(pk=data["search_id"])
        self.assertEqual((log.kind, log.customer, len(log.offers())), ("weight_calc", self.customer, 1))
        self.assertNotIn("customer_notice", log.offers()[0])  # stored as weight_source only

    def test_notice_follows_the_request_language(self):
        ka = self.client.post("/api/parts/calculate-price/", {"search_id": self.search_id, "offer_id": self.offer_id,
                              "weight_kg": "2.4", "weight_consent": True}, format="json", HTTP_ACCEPT_LANGUAGE="ka")
        en = self.calc()
        self.assertNotEqual(ka.json()["offer"]["customer_notice"], en.json()["offer"]["customer_notice"])

    def test_fresh_supplier_price_is_used(self):
        rows = [{**DEMO_ROWS[-1], "supplier_price_usd": "120.00"}]
        with mock.patch("parts.providers.DEMO_ROWS", DEMO_ROWS[:-1] + rows):
            offer = self.calc().json()["offer"]
        expected = price_offer(PriceInput(D("120.00"), D("2.4"), D("8.00")), customer=self.customer, currency="GEL",
                               part_number="68275354AC", brand="Denso")
        self.assertEqual(offer["unit_price"], str(expected.unit_price))

    def test_without_permission_403(self):
        CustomerTariff.objects.filter(is_default=True).update(can_enter_weight=False)
        response = self.calc()
        self.assertEqual((response.status_code, response.json()["code"]), (403, "weight_entry_not_allowed"))
        self.assertEqual(APIClient().post("/api/parts/calculate-price/", {}, format="json").status_code, 401)

    def test_foreign_search_or_unknown_offer_404(self):
        other = APIClient()
        other.force_login(make_customer(email="o@example.com", phone="599000000").user)
        self.assertEqual(self.calc(other).status_code, 404)
        self.assertEqual(self.calc(offer_id="NOPE").status_code, 404)
        self.assertEqual(self.calc(search_id=999999).status_code, 404)

    def test_validation(self):
        codes = lambda r: {k: [e["code"] for e in v] for k, v in r.json()["fields"].items()}
        for weight in ("0", "-1", "500.01", "abc", ""):
            with self.subTest(weight=weight):
                self.assertEqual(codes(self.calc(weight_kg=weight)), {"weight_kg": ["invalid_weight"]})
        self.assertEqual(codes(self.calc(weight_consent=False)), {"weight_consent": ["required"]})
        self.assertEqual(self.calc(weight_kg="500").status_code, 200)

    def test_shares_the_search_throttle(self):
        statuses = [self.calc().status_code for _ in range(30)]  # setUp used 1 of the 30/min
        self.assertEqual((statuses[-2], statuses[-1]), (200, 429))
