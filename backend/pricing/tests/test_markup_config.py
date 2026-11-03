"""Owner decision (M2 review, superseded by the canonical-USD rules): the default tariff always has a
markup; pricing never falls back to zero markup."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomerTariff
from accounts.testing import make_customer
from pricing.engine import PriceInput, PricingConfigurationError, price_offer
from pricing.models import ExchangeRate

D = Decimal


class DefaultMarkupValidationTests(TestCase):
    def test_the_default_tariff_needs_a_markup(self):
        default = CustomerTariff.objects.get(is_default=True)
        self.assertEqual(default.markup_percent, D("20.00"))  # migrated from the former USD markup row
        default.markup_percent = None
        with self.assertRaises(ValidationError) as ctx:
            default.full_clean()
        self.assertIn("markup_percent", ctx.exception.message_dict)

    def test_other_tariffs_may_leave_it_empty_and_negative_is_refused(self):
        CustomerTariff(name="Partner").full_clean()
        with self.assertRaises(ValidationError):
            CustomerTariff(name="Bad", markup_percent=D("-1")).full_clean()

    def test_admin_refuses_clearing_the_default_markup(self):
        client = APIClient()
        client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))
        default = CustomerTariff.objects.get(is_default=True)
        response = client.post(f"/admin/accounts/customertariff/{default.pk}/change/", {
            "name": default.name, "is_default": "on", "markup_percent": "", "payment_terms": "full",
            "split_upfront_percent": "70", "benefits_text_i18n_0": "", "benefits_text_i18n_1": "",
            "benefits_text_i18n_2": ""})
        self.assertEqual(response.status_code, 200)
        default.refresh_from_db()
        self.assertEqual(default.markup_percent, D("20.00"))


@override_settings(PARTS_PROVIDER="demo")
class MissingMarkupIsNeverZeroTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        CustomerTariff.objects.update(markup_percent=None)  # bypassing admin, e.g. a bad data fix

    def test_engine_refuses_and_logs(self):
        for customer in (None, make_customer()):
            with self.subTest(customer=customer), self.assertRaises(PricingConfigurationError), \
                    self.assertLogs("pricing.engine", "ERROR"):
                price_offer(PriceInput(D("100"), D("2"), D("8")), customer=customer, part_number="P", brand="")

    def test_search_fails_instead_of_selling_without_markup(self):
        client = APIClient(raise_request_exception=False)
        with self.assertLogs("pricing.engine", "ERROR"), self.assertLogs("django.request", "ERROR"):
            response = client.post("/api/parts/search/", {"part_number": "P-1"}, format="json")
        self.assertEqual((response.status_code, response.json()["code"]), (500, "server_error"))
