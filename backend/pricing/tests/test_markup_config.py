"""M2 review fix: the default tariff keeps a markup for every active currency; pricing never
falls back to zero markup."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomerTariff, TariffMarkup
from pricing.engine import PriceInput, PricingConfigurationError, price_offer
from pricing.models import CurrencySettings, ExchangeRate

D = Decimal


class AdminTestCase(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))

    def tariff_post(self, tariff, rows):
        """rows: [(markup or None, currency, percent, delete)]"""
        data = {"name": tariff.name, "payment_terms": tariff.payment_terms, "split_upfront_percent": "70",
                "benefits_text_i18n_0": "", "benefits_text_i18n_1": "", "benefits_text_i18n_2": "",
                "markups-TOTAL_FORMS": str(len(rows)),
                "markups-INITIAL_FORMS": str(sum(1 for r in rows if r[0])),
                "markups-MIN_NUM_FORMS": "0", "markups-MAX_NUM_FORMS": "1000"}
        if tariff.is_default:
            data["is_default"] = "on"
        for i, (markup, currency, percent, delete) in enumerate(rows):
            data.update({f"markups-{i}-id": markup.pk if markup else "", f"markups-{i}-tariff": tariff.pk,
                         f"markups-{i}-currency": currency, f"markups-{i}-markup_percent": percent})
            if delete:
                data[f"markups-{i}-DELETE"] = "on"
        return self.client.post(f"/admin/accounts/customertariff/{tariff.pk}/change/", data)


class DefaultMarkupAdminTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.default = CustomerTariff.objects.get(is_default=True)
        self.gel = TariffMarkup.objects.get(tariff=self.default, currency="GEL")
        self.usd = TariffMarkup.objects.get(tariff=self.default, currency="USD")

    def test_deleting_the_default_usd_markup_is_refused(self):
        response = self.tariff_post(self.default, [(self.gel, "GEL", "20", False), (self.usd, "USD", "20", True)])
        self.assertEqual(response.status_code, 200)  # re-rendered with the error
        self.assertContains(response, "USD")
        self.assertTrue(TariffMarkup.objects.filter(pk=self.usd.pk).exists())

    def test_changing_values_still_works(self):
        response = self.tariff_post(self.default, [(self.gel, "GEL", "25", False), (self.usd, "USD", "30", False)])
        self.assertEqual(response.status_code, 302)
        self.usd.refresh_from_db()
        self.assertEqual(self.usd.markup_percent, D("30.00"))

    def test_a_deactivated_currency_needs_no_default_markup(self):
        CurrencySettings.objects.filter(currency="USD").update(is_active=False)
        response = self.tariff_post(self.default, [(self.gel, "GEL", "20", False), (self.usd, "USD", "20", True)])
        self.assertEqual(response.status_code, 302)

    def test_other_tariffs_may_omit_currencies(self):
        partner = CustomerTariff.objects.create(name="Partner")
        response = self.tariff_post(partner, [(None, "GEL", "10", False)])
        self.assertEqual(response.status_code, 302)  # USD falls back to the default tariff's markup

    def test_making_a_tariff_default_requires_all_markups(self):
        partner = CustomerTariff.objects.create(name="Partner")
        partner.is_default = True  # posted with is_default on
        response = self.tariff_post(partner, [(None, "GEL", "10", False)])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(CustomerTariff.objects.get(name="Standard").is_default)

    def test_activating_a_currency_without_a_default_markup_is_refused(self):
        CurrencySettings.objects.filter(currency="USD").update(is_active=False)
        self.usd.delete()
        usd = CurrencySettings.objects.get(currency="USD")
        response = self.client.post(f"/admin/pricing/currencysettings/{usd.pk}/change/",
                                    {"currency": "USD", "vat_percent": "18", "is_active": "on"})
        self.assertEqual(response.status_code, 200)
        usd.refresh_from_db()
        self.assertFalse(usd.is_active)


@override_settings(PARTS_PROVIDER="demo")
class MissingMarkupIsNeverZeroTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        TariffMarkup.objects.filter(currency="USD").delete()  # bypassing admin, e.g. a bad data fix

    def test_engine_refuses_and_logs(self):
        with self.assertRaises(PricingConfigurationError), self.assertLogs("pricing.engine", "ERROR"):
            price_offer(PriceInput(D("100"), D("2"), D("8")), customer=None, currency="USD", part_number="P", brand="")

    def test_search_fails_instead_of_selling_without_markup(self):
        client = APIClient(raise_request_exception=False)
        with self.assertLogs("pricing.engine", "ERROR"), self.assertLogs("django.request", "ERROR"):
            response = client.post("/api/parts/search/", {"part_number": "P-1", "currency": "USD"}, format="json")
        self.assertEqual((response.status_code, response.json()["code"]), (500, "server_error"))
        gel = client.post("/api/parts/search/", {"part_number": "P-1"}, format="json")
        self.assertEqual(gel.status_code, 200)  # other currencies keep working
