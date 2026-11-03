"""LP-BE-403: search endpoint (G§8.4). Restores the legacy search_* scenarios (docs/legacy-test-map.md)."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from accounts.models import CustomerTariff, TariffMarkup
from accounts.testing import make_customer
from parts.amt_provider import AMTProviderError
from parts.models import PartSearchLog
from pricing.models import CurrencySettings, ExchangeRate
from pricing.rates import RateUnavailable

D = Decimal
SEARCH_KEYS = {"search_id", "part_number", "vin", "currency", "exchange_rate", "price_valid_until", "not_found",
               "offers"}


@override_settings(PARTS_PROVIDER="demo")
class SearchTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.5971"))
        self.client = APIClient()

    def search(self, client=None, **body):
        return (client or self.client).post("/api/parts/search/", {"part_number": "G9010-60020", **body}, format="json")

    def test_response_matches_the_contract(self):
        before = timezone.now()
        response = self.search(vin=" wba8e9g50gnu12345 ")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(set(data), SEARCH_KEYS)
        self.assertEqual((data["part_number"], data["vin"], data["currency"], data["exchange_rate"], data["not_found"]),
                         ("G9010-60020", "WBA8E9G50GNU12345", "GEL", "2.5971", False))
        valid_until = parse_datetime(data["price_valid_until"])
        self.assertTrue(before + timedelta(minutes=59) < valid_until <= timezone.now() + timedelta(minutes=60))
        self.assertEqual(len(data["offers"]), 4)
        ready = data["offers"][0]
        self.assertEqual((ready["status"], ready["currency"], ready["in_cart_quantity"], ready["image_urls"]),
                         ("ready", "GEL", 0, []))
        self.assertNotIn("supplier_price_usd", str(data))
        self.assertEqual(data["offers"][-1]["status"], "checking")
        self.assertEqual(PartSearchLog.objects.get().pk, data["search_id"])

    def test_part_number_is_trimmed(self):
        self.assertEqual(self.search(part_number="  G9010-60020 ").json()["part_number"], "G9010-60020")

    def test_not_found(self):
        data = self.search(part_number="NF-1").json()
        self.assertEqual((data["not_found"], data["offers"]), (True, []))

    def test_usd(self):
        data = self.search(currency="usd").json()
        self.assertEqual((data["currency"], data["offers"][0]["currency"]), ("USD", "USD"))

    def test_partner_gets_partner_prices_and_guest_default_prices(self):
        guest_price = self.search().json()["offers"][0]["unit_price"]
        partner = CustomerTariff.objects.create(name="Partner")
        TariffMarkup.objects.create(tariff=partner, currency="GEL", markup_percent=D("5"))
        customer = make_customer(tariff=partner)
        client = APIClient()
        client.force_login(customer.user)
        partner_price = self.search(client).json()["offers"][0]["unit_price"]
        self.assertLess(D(partner_price), D(guest_price))

    def test_every_search_is_logged_for_its_owner(self):
        self.search()
        guest_log = PartSearchLog.objects.get()
        self.assertEqual((guest_log.customer, guest_log.session_key), (None, self.client.session.session_key))
        self.assertEqual((guest_log.kind, guest_log.currency, guest_log.exchange_rate), ("search", "GEL", D("2.5971")))
        self.assertEqual(len(guest_log.normalized_response["offers"]), 4)
        customer = make_customer()
        client = APIClient()
        client.force_login(customer.user)
        self.search(client)
        self.assertEqual(PartSearchLog.objects.latest("id").customer, customer)

    def test_validation(self):
        CurrencySettings.objects.filter(currency="USD").update(is_active=False)
        response = self.client.post("/api/parts/search/", {"vin": "123", "currency": "USD"}, format="json")
        fields = {k: [e["code"] for e in v] for k, v in response.json()["fields"].items()}
        self.assertEqual(fields, {"part_number": ["required"], "vin": ["invalid_vin"], "currency": ["invalid"]})
        self.assertEqual(self.search(currency="EUR").json()["fields"]["currency"][0]["code"], "invalid")

    @override_settings(PARTS_PROVIDER="amt")
    def test_supplier_failure_is_502_and_logged(self):
        with mock.patch("parts.providers.get_price_by_oem", side_effect=AMTProviderError("down")):
            response = self.search()
        self.assertEqual((response.status_code, response.json()["code"]), (502, "supplier_unavailable"))
        self.assertEqual(PartSearchLog.objects.get().status, "failed")

    def test_rate_failure_is_502(self):
        ExchangeRate.objects.all().delete()
        with mock.patch("pricing.rates.fetch_nbg_usd", side_effect=RateUnavailable()):
            response = self.search()
        self.assertEqual((response.status_code, response.json()["code"]), (502, "rate_unavailable"))

    def test_csrf_is_required(self):
        response = self.search(APIClient(enforce_csrf_checks=True))
        self.assertEqual((response.status_code, response.json()["code"]), (403, "csrf_failed"))

    def test_search_throttle(self):
        statuses = [self.search().status_code for _ in range(31)]
        self.assertEqual((statuses[0], statuses[-1]), (200, 429))
