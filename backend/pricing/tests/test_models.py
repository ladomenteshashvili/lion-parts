"""LP-BE-301: pricing app, models, seed and /api/meta/ (G§6.2, G§8.1)."""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from pricing.models import CurrencySettings, Discount, ExchangeRate, PickupPoint, SiteSettings


class SeedTests(TestCase):
    def test_seeded_rows(self):
        site = SiteSettings.get()
        self.assertEqual((site.pk, site.price_valid_minutes, site.rate_max_age_days), (1, 60, 3))
        self.assertEqual(site.default_shipping_usd_per_kg, Decimal("8.00"))
        self.assertIn("vake", [d["code"] for d in site.delivery_districts])
        self.assertEqual(site.invoice_seller_name_i18n["en"], "Lion Parts")
        self.assertEqual(dict(CurrencySettings.objects.values_list("currency", "vat_percent")),
                         {"GEL": Decimal("18.00"), "USD": Decimal("18.00")})
        self.assertEqual(PickupPoint.objects.get().name_i18n["en"], "Beliashvili pickup point")

    def test_site_settings_is_a_singleton(self):
        with self.assertRaises(ValidationError):
            SiteSettings(price_valid_minutes=30).save()
        self.assertEqual(SiteSettings.objects.count(), 1)
        site = SiteSettings.get()
        site.price_valid_minutes = 30
        site.save()
        self.assertEqual(SiteSettings.get().price_valid_minutes, 30)


class DiscountModelTests(TestCase):
    def test_part_number_is_normalized(self):
        rule = Discount.objects.create(name="x", kind="percent", value=5, part_number=" 17940-25.020 ")
        self.assertEqual(rule.part_number, "1794025020")

    def test_validation(self):
        now = timezone.now()
        for fields, field in (({"kind": "percent", "value": 101}, "value"),
                              ({"kind": "fixed", "value": -1}, "value"),
                              ({"kind": "percent", "value": 5, "valid_from": now, "valid_to": now}, "valid_to")):
            with self.subTest(**{k: str(v) for k, v in fields.items()}):
                with self.assertRaises(ValidationError) as ctx:
                    Discount(name="x", **fields).full_clean()
                self.assertIn(field, ctx.exception.message_dict)
        Discount(name="ok", kind="fixed", value=500, valid_from=now, valid_to=now + timedelta(days=1)).full_clean()


class MetaPricingTests(TestCase):
    def meta(self, lang):
        return self.client.get("/api/meta/", HTTP_ACCEPT_LANGUAGE=lang).json()

    def test_localized_names(self):
        en, ru, ka = self.meta("en"), self.meta("ru"), self.meta("ka")
        self.assertEqual(en["pickup_points"][0]["name"], "Beliashvili pickup point")
        self.assertEqual(ru["pickup_points"][0]["name"], "Пункт выдачи Белиашвили")
        self.assertNotEqual(ka["pickup_points"][0]["name"], en["pickup_points"][0]["name"])
        self.assertIn({"code": "vake", "city": "tbilisi", "name": "Vake"}, en["delivery"]["districts"])
        self.assertIn({"code": "vake", "city": "tbilisi", "name": "Ваке"}, ru["delivery"]["districts"])
        self.assertEqual(en["delivery"]["cities"], [{"code": "tbilisi", "name": "Tbilisi"}])
        self.assertEqual(set(en["pickup_points"][0]), {"id", "name", "address", "working_hours"})

    def test_only_active_currencies_and_points_and_latest_rate(self):
        CurrencySettings.objects.filter(currency="USD").update(is_active=False)
        PickupPoint.objects.create(name_i18n={"ka": "x"}, is_active=False)
        ExchangeRate.objects.create(date=date(2026, 10, 23), rate=Decimal("2.6000"))
        ExchangeRate.objects.create(date=date(2026, 10, 24), rate=Decimal("2.5971"))
        data = self.meta("en")
        self.assertEqual(data["currencies"], [{"code": "GEL", "symbol": "₾"}])
        self.assertEqual(len(data["pickup_points"]), 1)
        self.assertEqual(data["usd_rate"], {"rate": "2.5971", "date": "2026-10-24"})
