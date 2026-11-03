"""LP-BE-303: price engine & snapshots (G§7.2)."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomerTariff, TariffMarkup
from accounts.testing import make_customer
from parts.models import CarrierService
from pricing.engine import PriceInput, PriceSnapshot, default_shipping_usd_per_kg, price_offer, reprice
from pricing.models import CurrencySettings, Discount, ExchangeRate, SiteSettings

D = Decimal
INP = PriceInput(D("100"), D("2"), D("8"))


class EngineTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.customer = make_customer()

    def price(self, currency="GEL", customer="default", **kwargs):
        customer = self.customer if customer == "default" else customer
        return price_offer(INP, customer=customer, currency=currency, part_number="P-1", brand="OEM", **kwargs)

    def test_worked_example_gel_and_usd(self):
        gel, usd = self.price("GEL"), self.price("USD")
        self.assertEqual(gel.list_unit_price, D("427.07"))   # round2(116 x 2.6 x 1.2 x 1.18) = round2(427.0656)
        self.assertEqual(usd.list_unit_price, D("164.26"))   # round2(116 x 1.2 x 1.18) = round2(164.256)
        self.assertEqual((gel.unit_price, gel.discount_unit_amount, gel.discount_id), (D("427.07"), D("0.00"), None))
        self.assertEqual((gel.markup_percent, gel.vat_percent, gel.exchange_rate), (D("20.00"), D("18.00"), D("2.6000")))
        self.assertEqual((usd.currency, usd.exchange_rate), ("USD", D("2.6000")))

    def test_usd_markup_changes_only_usd_prices(self):
        TariffMarkup.objects.filter(tariff__is_default=True, currency="USD").update(markup_percent=D("30"))
        self.assertEqual(self.price("GEL").list_unit_price, D("427.07"))
        self.assertEqual(self.price("USD").list_unit_price, D("177.94"))  # 116 x 1.3 x 1.18 = 177.944

    def test_vat_per_currency(self):
        CurrencySettings.objects.filter(currency="USD").update(vat_percent=D("0"))
        self.assertEqual(self.price("USD").list_unit_price, D("139.20"))
        self.assertEqual(self.price("GEL").list_unit_price, D("427.07"))

    def test_partner_tariff_and_fallback_to_the_default_markup(self):
        partner = CustomerTariff.objects.create(name="Partner")
        TariffMarkup.objects.create(tariff=partner, currency="GEL", markup_percent=D("10"))
        self.customer.tariff = partner
        self.customer.save()
        self.assertEqual(self.price("GEL").list_unit_price, D("391.48"))  # 301.6 x 1.1 x 1.18 = 391.4768
        self.assertEqual(self.price("USD").list_unit_price, D("164.26"))  # no USD row -> default tariff's 20%
        self.assertEqual(self.price("GEL", customer=None).list_unit_price, D("427.07"))  # guests: default tariff

    def test_discount_is_applied_and_snapshotted(self):
        rule = Discount.objects.create(name="10%", kind="percent", value=10)
        gel = self.price("GEL")
        self.assertEqual((gel.discount_unit_amount, gel.unit_price, gel.discount_id), (D("42.71"), D("384.36"), rule.id))
        fixed = Discount.objects.create(name="50 GEL", kind="fixed", value=50)
        usd = self.price("USD")
        self.assertEqual((usd.discount_unit_amount, usd.discount_id), (D("19.23"), fixed.id))  # 50 / 2.6

    def test_explicit_rate_skips_the_lookup(self):
        with mock.patch("pricing.engine.get_usd_rate") as get_rate:
            snap = self.price("GEL", usd_rate=D("2.5"))
        get_rate.assert_not_called()
        self.assertEqual(snap.list_unit_price, D("410.64"))  # 290 x 1.2 x 1.18

    def test_snapshot_json_round_trip(self):
        snap = self.price("GEL")
        data = snap.to_json()
        self.assertEqual((data["unit_price"], data["exchange_rate"], data["discount_id"]), ("427.07", "2.6000", None))
        self.assertEqual(PriceSnapshot.from_json(data), snap)

    def test_reprice_without_changes_is_identical(self):
        snap = self.price("GEL")
        line = {**snap.to_json(), "part_number": "P-1", "brand": "OEM"}
        self.assertEqual(reprice(line, customer=self.customer, currency="GEL"), snap)

    def test_reprice_uses_the_current_rate_and_converts_currency(self):
        snap = self.price("GEL")
        ExchangeRate.objects.create(date=date(2099, 1, 1), rate=D("2.7000"))  # newer rate
        line = {**snap.to_json(), "part_number": "P-1", "brand": "OEM"}
        again = reprice(line, customer=self.customer, currency="GEL")
        self.assertEqual((again.exchange_rate, again.list_unit_price), (D("2.7000"), D("443.49")))  # 116 x 2.7 x 1.2 x 1.18 = 443.4912
        self.assertEqual(reprice(line, customer=self.customer, currency="USD").list_unit_price, D("164.26"))

    def test_default_shipping(self):
        self.assertEqual(default_shipping_usd_per_kg(), D("8.00"))  # SiteSettings seed
        site = SiteSettings.get()
        site.default_shipping_usd_per_kg = D("9.50")
        site.save()
        self.assertEqual(default_shipping_usd_per_kg(), D("9.50"))
        CarrierService.objects.create(name="Air", usd_per_kg=D("7.25"), is_default=True)
        self.assertEqual(default_shipping_usd_per_kg(), D("7.25"))

    def test_rounding_half_up(self):
        # cost such that list = x.xx5 exactly: S=0.5, W=0, USD, M=0, T=0 -> 0.50; use VAT 1% on 0.5 -> 0.505
        TariffMarkup.objects.filter(tariff__is_default=True, currency="USD").update(markup_percent=D("0"))
        CurrencySettings.objects.filter(currency="USD").update(vat_percent=D("1"))
        snap = price_offer(PriceInput(D("0.5"), D("0"), D("8")), customer=None, currency="USD",
                           part_number="x", brand="")
        self.assertEqual(snap.list_unit_price, D("0.51"))
