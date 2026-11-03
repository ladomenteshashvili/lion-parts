"""LP-BE-1101: configuration admin (G§10)."""

import json

from datetime import date
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomerTariff
from pricing.engine import PriceInput, price_offer
from pricing.models import Discount, ExchangeRate, SiteSettings
from pricing.rates import RateUnavailable

D = Decimal


class AdminTestCase(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))


def site_form(**changes):
    """The SiteSettings admin form data for the current row, with `changes` applied."""
    site = SiteSettings.get()
    data = {}
    for field in ("multi_currency_enabled", "rate_max_age_days", "vat_percent", "price_valid_business_days",
                  "default_shipping_usd_per_kg", "invoice_seller_code", "invoice_seller_phone", "invoice_seller_email"):
        value = getattr(site, field)
        if isinstance(value, bool):
            if value:
                data[field] = "on"
        else:
            data[field] = str(value)
    for field in ("public_holidays", "delivery_cities", "delivery_districts"):
        data[field] = json.dumps(getattr(site, field))
    for field in ("invoice_seller_name_i18n", "invoice_seller_address_i18n", "invoice_bank_details_i18n",
                  "invoice_payment_due_text_i18n", "invoice_footer_text_i18n"):
        for i, (code, _name) in enumerate(settings.LANGUAGES):
            data[f"{field}_{i}"] = getattr(site, field).get(code, "")
    for key, value in changes.items():
        if value is False:
            data.pop(key, None)
        else:
            data[key] = "on" if value is True else value
    return data


class OperatorChangesPricesTests(AdminTestCase):
    def usd_price(self):
        return price_offer(PriceInput(D("100"), D("2"), D("8")), customer=None, part_number="P",
                           brand="").list_price_usd

    def test_markup_and_vat_changes_reach_the_next_price(self):
        self.assertEqual(self.usd_price(), D("164.26"))
        tariff = CustomerTariff.objects.get(is_default=True)
        data = {"name": tariff.name, "is_default": "on", "markup_percent": "30", "payment_terms": "full",
                "split_upfront_percent": "70", "benefits_text_i18n_0": "", "benefits_text_i18n_1": "",
                "benefits_text_i18n_2": ""}
        response = self.client.post(f"/admin/accounts/customertariff/{tariff.pk}/change/", data)
        self.assertEqual(response.status_code, 302, response.content[:2000])
        response = self.client.post("/admin/pricing/sitesettings/1/change/", site_form(vat_percent="20"))
        self.assertEqual(response.status_code, 302, response.content[:3000])
        self.assertEqual(self.usd_price(), D("180.96"))  # 116 x 1.3 x 1.2


class CurrencyAndValidityAdminTests(AdminTestCase):
    URL = "/admin/pricing/sitesettings/1/change/"

    def test_multi_currency_toggle_and_validity_are_editable(self):
        response = self.client.post(self.URL, site_form(multi_currency_enabled=True, price_valid_business_days="5",
                                                        public_holidays='["2026-01-01", "2026-01-07"]'))
        self.assertEqual(response.status_code, 302, response.content[:3000])
        site = SiteSettings.get()
        self.assertEqual((site.multi_currency_enabled, site.price_valid_business_days, site.public_holidays),
                         (True, 5, ["2026-01-01", "2026-01-07"]))

    def test_invalid_values_are_refused(self):
        for changes in ({"price_valid_business_days": "0"}, {"vat_percent": "101"}, {"vat_percent": "-1"},
                        {"public_holidays": '["2026-13-01"]'}, {"public_holidays": '"2026-01-01"'},
                        {"public_holidays": '["01.01.2026"]'}):
            with self.subTest(**changes):
                response = self.client.post(self.URL, site_form(**changes))
                self.assertEqual(response.status_code, 200)  # re-rendered with the error
        site = SiteSettings.get()
        self.assertEqual((site.price_valid_business_days, site.vat_percent, site.public_holidays), (10, D("18.00"), []))


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
                    "/admin/pricing/sitesettings/1/change/", "/admin/parts/partimage/", "/admin/parts/partimage/add/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
