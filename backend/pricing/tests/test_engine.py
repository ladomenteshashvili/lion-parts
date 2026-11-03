"""Price engine (G§7.2): canonical USD price, GEL as a pure conversion of it."""

from decimal import Decimal

from django.test import TestCase

from accounts.models import CustomerTariff
from accounts.testing import make_customer
from parts.models import CarrierService
from pricing.engine import PriceInput, PriceSnapshot, convert, default_shipping_usd_per_kg, price_offer, reprice
from pricing.models import Discount, SiteSettings

D = Decimal
INP = PriceInput(D("100"), D("2"), D("8"))


class CanonicalPriceTests(TestCase):
    def setUp(self):
        self.customer = make_customer()

    def price(self, customer="default"):
        return price_offer(INP, customer=self.customer if customer == "default" else customer,
                           part_number="P-1", brand="OEM")

    def test_worked_example(self):
        snap = self.price()
        # round2((100 + 2 x 8) x 1.20 x 1.18) = round2(164.256)
        self.assertEqual((snap.list_price_usd, snap.discount_usd, snap.unit_price_usd, snap.discount_id),
                         (D("164.26"), D("0.00"), D("164.26"), None))
        self.assertEqual((snap.markup_percent, snap.vat_percent), (D("20.00"), D("18.00")))

    def test_gel_is_a_pure_conversion_of_the_usd_price(self):
        snap = self.price()
        gel = convert(snap, "GEL", D("2.6000"))
        self.assertEqual((gel.unit_price, gel.list_unit_price, gel.discount_unit_amount, gel.exchange_rate),
                         (D("427.08"), D("427.08"), D("0.00"), D("2.6000")))  # round2(164.26 x 2.6) = round2(427.076)
        usd = convert(snap, "USD", D("2.6000"))
        self.assertEqual((usd.unit_price, usd.exchange_rate), (D("164.26"), D("1")))

    def test_the_usd_price_never_depends_on_the_rate(self):
        snap = self.price()
        for rate in (D("2.5"), D("2.7"), D("3.1234")):
            self.assertEqual(convert(snap, "GEL", rate).unit_price, (snap.unit_price_usd * rate).quantize(D("0.01")))
        self.assertEqual(self.price(), snap)

    def test_discounted_line_adds_up_in_gel(self):
        Discount.objects.create(name="10%", kind="percent", value=10)
        snap = self.price()
        self.assertEqual((snap.discount_usd, snap.unit_price_usd), (D("16.43"), D("147.83")))
        gel = convert(snap, "GEL", D("2.6"))
        # the GEL unit price converts the USD unit price; the GEL discount is what makes the line add up
        self.assertEqual((gel.list_unit_price, gel.unit_price, gel.discount_unit_amount),
                         (D("427.08"), D("384.36"), D("42.72")))
        self.assertEqual(gel.list_unit_price - gel.discount_unit_amount, gel.unit_price)

    def test_partner_markup_and_fallback_to_the_default(self):
        partner = CustomerTariff.objects.create(name="Partner", markup_percent=D("10"))
        self.customer.tariff = partner
        self.customer.save()
        self.assertEqual(self.price().list_price_usd, D("150.57"))  # 116 x 1.1 x 1.18 = 150.568
        CustomerTariff.objects.filter(pk=partner.pk).update(markup_percent=None)
        self.customer.refresh_from_db()
        self.assertEqual(self.price().list_price_usd, D("164.26"))  # the default tariff's 20 %
        self.assertEqual(self.price(customer=None).list_price_usd, D("164.26"))  # guests: default tariff

    def test_vat_is_one_site_setting(self):
        site = SiteSettings.get()
        site.vat_percent = D("0")
        site.save()
        self.assertEqual(self.price().list_price_usd, D("139.20"))

    def test_snapshot_json_round_trip_and_reprice(self):
        snap = self.price()
        self.assertEqual(PriceSnapshot.from_json(snap.to_json()), snap)
        line = {**snap.to_json(), "part_number": "P-1", "brand": "OEM"}
        self.assertEqual(reprice(line, customer=self.customer), snap)

    def test_rounding_half_up(self):
        site = SiteSettings.get()
        site.vat_percent = D("1")
        site.save()
        CustomerTariff.objects.filter(is_default=True).update(markup_percent=D("0"))
        snap = price_offer(PriceInput(D("0.5"), D("0"), D("8")), customer=None, part_number="x", brand="")
        self.assertEqual(snap.list_price_usd, D("0.51"))  # 0.505 -> 0.51
        self.assertEqual(convert(snap, "GEL", D("2.5")).unit_price, D("1.28"))  # 1.275 -> 1.28

    def test_default_shipping(self):
        self.assertEqual(default_shipping_usd_per_kg(), D("8.00"))
        CarrierService.objects.create(name="Air", usd_per_kg=D("7.25"), is_default=True)
        self.assertEqual(default_shipping_usd_per_kg(), D("7.25"))
