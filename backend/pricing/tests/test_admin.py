"""LP-BE-1101: configuration admin (G§10)."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomerTariff, TariffMarkup
from pricing.engine import PriceInput, price_offer
from pricing.models import CurrencySettings, Discount, ExchangeRate, SiteSettings
from pricing.rates import RateUnavailable

D = Decimal


class AdminTestCase(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))


class OperatorChangesPricesTests(AdminTestCase):
    def usd_price(self):
        return price_offer(PriceInput(D("100"), D("2"), D("8")), customer=None, currency="USD", part_number="P",
                           brand="", usd_rate=D("2.6")).list_unit_price

    def test_usd_markup_and_vat_changes_reach_the_next_price(self):
        self.assertEqual(self.usd_price(), D("164.26"))
        tariff = CustomerTariff.objects.get(is_default=True)
        markups = list(TariffMarkup.objects.filter(tariff=tariff).order_by("currency"))  # GEL, USD
        data = {"name": tariff.name, "is_default": "on", "payment_terms": "full", "split_upfront_percent": "70",
                "benefits_text_i18n_0": "", "benefits_text_i18n_1": "", "benefits_text_i18n_2": "",
                "markups-TOTAL_FORMS": "2", "markups-INITIAL_FORMS": "2", "markups-MIN_NUM_FORMS": "0",
                "markups-MAX_NUM_FORMS": "1000"}
        for i, (markup, percent) in enumerate(zip(markups, ("20", "30"))):
            data.update({f"markups-{i}-id": markup.pk, f"markups-{i}-tariff": tariff.pk,
                         f"markups-{i}-currency": markup.currency, f"markups-{i}-markup_percent": percent})
        response = self.client.post(f"/admin/accounts/customertariff/{tariff.pk}/change/", data)
        self.assertEqual(response.status_code, 302, response.content[:2000])
        usd = CurrencySettings.objects.get(currency="USD")
        response = self.client.post(f"/admin/pricing/currencysettings/{usd.pk}/change/",
                                    {"currency": "USD", "vat_percent": "20", "is_active": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.usd_price(), D("180.96"))  # 116 x 1.3 x 1.2


class SiteSettingsAdminTests(AdminTestCase):
    URL = "/admin/pricing/sitesettings/"

    def test_single_row_that_cannot_be_deleted_or_added_again(self):
        self.assertEqual(self.client.get(f"{self.URL}add/").status_code, 403)
        self.assertEqual(self.client.get(f"{self.URL}1/delete/").status_code, 403)
        page = self.client.get(f"{self.URL}1/change/").content.decode()
        for i in range(3):
            self.assertIn(f'name="invoice_seller_name_i18n_{i}"', page)  # one input per language

    def test_delivery_rows_are_validated(self):
        site = SiteSettings.get()
        site.delivery_districts = [{"code": "x", "city": "nowhere", "name": {"ka": "x"}}]
        with self.assertRaises(Exception) as ctx:
            site.full_clean()
        self.assertIn("delivery_districts", ctx.exception.message_dict)
        site.delivery_districts = [{"code": "x", "city": "tbilisi", "name": {"en": "only en"}}]
        with self.assertRaises(Exception):
            site.full_clean()
        site.delivery_cities = [{"code": "tbilisi", "name": {"ka": "t"}}, {"code": "tbilisi", "name": {"ka": "t"}}]
        with self.assertRaises(Exception) as ctx:
            site.full_clean()
        self.assertIn("delivery_cities", ctx.exception.message_dict)
        SiteSettings.get().full_clean()  # the seed is valid


class ExchangeRateAdminTests(AdminTestCase):
    URL = "/admin/pricing/exchangerate/"

    def test_fetch_now_without_selecting_rows(self):
        stored = ExchangeRate(date=date(2026, 10, 24), rate=D("2.5971"))
        with mock.patch("pricing.admin.fetch_nbg_usd", return_value=stored) as fetch:
            response = self.client.post(self.URL, {"action": "fetch_now", "index": "0"}, follow=True)
        fetch.assert_called_once()
        self.assertIn("2.5971", " ".join(str(m) for m in response.context["messages"]))

    def test_fetch_failure_is_reported(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6"))
        with mock.patch("pricing.admin.fetch_nbg_usd", side_effect=RateUnavailable()):
            response = self.client.post(self.URL, {"action": "fetch_now", "index": "0"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(list(response.context["messages"])), 1)

    def test_rates_are_read_only(self):
        rate = ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6"))
        self.assertEqual(self.client.get(f"{self.URL}add/").status_code, 403)
        self.assertEqual(self.client.post(f"{self.URL}{rate.pk}/change/", {"rate": "9"}).status_code, 403)


class DiscountAdminTests(AdminTestCase):
    def test_percent_over_100_is_rejected(self):
        response = self.client.post("/admin/pricing/discount/add/", {"name": "x", "kind": "percent", "value": "150",
                                                                     "is_active": "on"})
        self.assertEqual(response.status_code, 200)  # form re-rendered with the error
        self.assertFalse(Discount.objects.exists())

    def test_list_filters_and_search(self):
        Discount.objects.create(name="Spring", kind="percent", value=5)
        self.assertEqual(self.client.get("/admin/pricing/discount/?q=spring&is_active__exact=1&kind=percent").status_code,
                         200)


class PageSmokeTests(AdminTestCase):
    def test_pages_render(self):
        for url in ("/admin/pricing/pickuppoint/", "/admin/pricing/pickuppoint/1/change/",
                    "/admin/pricing/currencysettings/", "/admin/parts/partimage/", "/admin/parts/partimage/add/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
