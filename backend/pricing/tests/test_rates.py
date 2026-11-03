"""LP-BE-302: NBG exchange rate client & job (G§7.4). No network: requests.get is mocked."""

from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

import requests
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from api.errors import ApiError
from pricing.models import ExchangeRate, SiteSettings
from pricing.rates import RateUnavailable, fetch_nbg_usd, get_usd_rate

NBG_RESPONSE = [{"date": "2026-10-24T00:00:00.000Z", "currencies": [
    {"code": "EUR", "quantity": 1, "rate": 3.0123, "validFromDate": "2026-10-24T00:00:00.000Z"},
    {"code": "USD", "quantity": 1, "rate": 2.5971, "validFromDate": "2026-10-24T00:00:00.000Z"},
]}]


def nbg(payload=NBG_RESPONSE, status=200):
    response = mock.Mock(status_code=status)
    response.json.return_value = payload
    response.raise_for_status.side_effect = None if status == 200 else requests.HTTPError(str(status))
    return mock.patch("pricing.rates.requests.get", return_value=response)


class FetchTests(TestCase):
    def test_parses_and_stores_the_usd_rate(self):
        with nbg() as get:
            rate = fetch_nbg_usd()
        self.assertEqual((rate.date, rate.currency, rate.rate), (date(2026, 10, 24), "USD", Decimal("2.5971")))
        self.assertEqual(get.call_args.kwargs["timeout"], 10)

    def test_quantity_division_and_upsert(self):
        payload = [{"currencies": [{"code": "USD", "quantity": 10, "rate": 25.971,
                                    "validFromDate": "2026-10-24T00:00:00.000Z"}]}]
        with nbg():
            fetch_nbg_usd()
        with nbg(payload):
            fetch_nbg_usd()
        self.assertEqual(list(ExchangeRate.objects.values_list("rate", flat=True)), [Decimal("2.5971")])

    def test_failures_raise_rate_unavailable(self):
        logs = self.enterContext(self.assertLogs("pricing.rates", "WARNING"))
        bad_payloads = [[], [{"currencies": []}], [{"currencies": [{"code": "USD"}]}], {"x": 1},
                        [{"currencies": [{"code": "USD", "rate": 0, "validFromDate": "2026-10-24"}]}]]
        for payload in bad_payloads:
            with self.subTest(payload=payload), nbg(payload), self.assertRaises(RateUnavailable):
                fetch_nbg_usd()
        with nbg(status=500), self.assertRaises(RateUnavailable):
            fetch_nbg_usd()
        with mock.patch("pricing.rates.requests.get", side_effect=requests.Timeout), self.assertRaises(RateUnavailable):
            fetch_nbg_usd()
        self.assertFalse(ExchangeRate.objects.exists())
        self.assertTrue(all("NBG rate fetch failed" in line for line in logs.output))

    def test_rate_unavailable_is_the_contract_error(self):
        err = RateUnavailable()
        self.assertIsInstance(err, ApiError)
        self.assertEqual((err.code, err.status_code), ("rate_unavailable", 502))


class GetUsdRateTests(TestCase):
    def store(self, days_old, rate="2.6000"):
        return ExchangeRate.objects.create(date=timezone.localdate() - timedelta(days=days_old), rate=Decimal(rate))

    def test_fresh_rate_needs_no_http_call(self):
        self.store(3)  # rate_max_age_days = 3
        with mock.patch("pricing.rates.requests.get") as get:
            self.assertEqual(get_usd_rate(), Decimal("2.6000"))
        get.assert_not_called()

    def test_stale_rate_fetches(self):
        self.store(4)
        with nbg():
            self.assertEqual(get_usd_rate(), Decimal("2.5971"))

    def test_stale_rate_and_nbg_down(self):
        self.store(4)
        with nbg(status=503), self.assertRaises(RateUnavailable), self.assertLogs("pricing.rates", "WARNING"):
            get_usd_rate()

    def test_missing_rate_fetches_and_max_age_is_configurable(self):
        with nbg():
            self.assertEqual(get_usd_rate(), Decimal("2.5971"))
        site = SiteSettings.get()
        site.rate_max_age_days = 10_000
        site.save()
        with mock.patch("pricing.rates.requests.get") as get:
            get_usd_rate()
        get.assert_not_called()


class FetchCommandTests(TestCase):
    def test_command_is_idempotent(self):
        out = StringIO()
        with nbg():
            call_command("fetch_nbg_rates", stdout=out)
            call_command("fetch_nbg_rates", stdout=out)
        self.assertEqual(ExchangeRate.objects.count(), 1)
        self.assertIn("2.5971", out.getvalue())

    def test_command_fails_loudly(self):
        with nbg(status=500), self.assertRaises(CommandError), self.assertLogs("pricing.rates", "WARNING"):
            call_command("fetch_nbg_rates", stdout=StringIO())
