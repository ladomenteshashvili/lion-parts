"""LP-BE-402: offer builder on the price engine (G§7.6, G§8.0 Offer)."""

from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from parts.amt_provider import AMTProviderError
from parts.models import CarrierService
from parts.providers import OFFER_KEYS, PartsProviderError, build_offers, fetch_rows, public_offer
from pricing.engine import PriceInput, price_offer
from pricing.models import ExchangeRate
from pricing.rates import RateUnavailable

D = Decimal
AMT_ROWS = [
    {"brand": "Chrysler", "descr": "NO_PART_FOUND", "list_price": "", "oem": "X", "weight": 0},
    {"brand": "Chrysler", "descr": "BRACKET-HEADLAMP", "list_price": 47.29, "oem": "68275354AC", "replace": "", "weight": 0.0},
    {"brand": "Mopar", "descr": "BRACKET", "list_price": "52.10", "oem": "68275354AD", "replace": "68275354AE",
     "weight": "1.25"},
]


class OfferBuilderTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        CarrierService.objects.create(name="Air", usd_per_kg=D("8.00"), min_eta_days=9, max_eta_days=15,
                                      is_default=True)

    def offers(self, part_number="P-1", currency="GEL"):
        provider, raw, rows = fetch_rows(part_number)
        return build_offers(rows, customer=None, currency=currency, part_number=part_number)

    @override_settings(PARTS_PROVIDER="demo")
    def test_demo_offers_use_the_engine(self):
        offers = self.offers()
        first = offers[0]
        expected = price_offer(PriceInput(D("180.00"), D("2.00"), D("8.00")), customer=None, currency="GEL",
                               part_number="P-1", brand="OEM")
        self.assertEqual((first["status"], first["unit_price"]), ("ready", str(expected.unit_price)))
        self.assertEqual(first["snapshot"], expected.to_json())
        self.assertEqual((first["eta_min_days"], first["eta_max_days"], first["weight_kg"]), (9, 15, "2.00"))
        checking = offers[-1]
        self.assertEqual((checking["status"], checking["unit_price"], checking["list_unit_price"],
                          checking["requires_weight"], checking["weight_kg"]), ("checking", None, None, True, None))

    @override_settings(PARTS_PROVIDER="demo")
    def test_demo_not_found(self):
        self.assertEqual(self.offers("NF-123"), [])

    @override_settings(PARTS_PROVIDER="amt")
    def test_amt_rows(self):
        with mock.patch("parts.providers.get_price_by_oem", return_value=AMT_ROWS):
            provider, raw, rows = fetch_rows("68275354AC")
            offers = build_offers(rows, customer=None, currency="USD", part_number="68275354AC")
        self.assertEqual((provider, raw), ("amt", AMT_ROWS))
        self.assertEqual([o["offer_id"] for o in offers], ["AMT-2-68275354AC", "AMT-3-68275354AD"])  # index of the raw row
        no_weight, priced = offers
        self.assertEqual((no_weight["status"], no_weight["unit_price"]), ("checking", None))
        self.assertEqual((priced["status"], priced["currency"], priced["note"]), ("ready", "USD", "Replace: 68275354AE"))
        expected = price_offer(PriceInput(D("52.10"), D("1.25"), D("8.00")), customer=None, currency="USD",
                               part_number="68275354AC", brand="Mopar")
        self.assertEqual(priced["unit_price"], str(expected.unit_price))

    @override_settings(PARTS_PROVIDER="amt")
    def test_supplier_failure_is_502_supplier_unavailable(self):
        with mock.patch("parts.providers.get_price_by_oem", side_effect=AMTProviderError("down")), \
                self.assertRaises(PartsProviderError) as ctx:
            fetch_rows("X")
        self.assertEqual((ctx.exception.code, ctx.exception.status_code), ("supplier_unavailable", 502))

    @override_settings(PARTS_PROVIDER="demo")
    def test_rate_failure_propagates_as_rate_unavailable(self):
        ExchangeRate.objects.all().delete()
        with mock.patch("pricing.rates.fetch_nbg_usd", side_effect=RateUnavailable()), \
                self.assertRaises(RateUnavailable):
            self.offers()

    @override_settings(PARTS_PROVIDER="demo")
    def test_public_offer_has_exactly_the_contract_keys(self):
        for offer in self.offers():
            public = public_offer(offer, in_cart_quantity=2, image_urls=["http://x/1.png"])
            self.assertEqual(tuple(public), OFFER_KEYS)
            self.assertEqual(set(public), {
                "offer_id", "name", "brand", "condition", "status", "list_unit_price", "discount_unit_amount",
                "unit_price", "currency", "eta_min_days", "eta_max_days", "weight_kg", "weight_source",
                "requires_weight", "in_cart_quantity", "image_urls", "customer_notice", "note"})
            self.assertNotIn("supplier_price_usd", public)  # supplier cost never leaves the server
            self.assertNotIn("snapshot", public)

    @override_settings(PARTS_PROVIDER="demo")
    def test_without_a_default_carrier_eta_is_null(self):
        CarrierService.objects.all().delete()
        self.assertEqual((self.offers()[0]["eta_min_days"], self.offers()[0]["eta_max_days"]), (None, None))

    @override_settings(PARTS_PROVIDER="demo")
    def test_weight_override_marks_the_customer_weight(self):
        provider, raw, rows = fetch_rows("P-1")
        offer = build_offers(rows[-1:], customer=None, currency="GEL", part_number="P-1", weight_source="customer",
                             weight_override=D("2.4"))[0]
        self.assertEqual((offer["status"], offer["weight_kg"], offer["weight_source"]), ("ready", "2.40", "customer"))
