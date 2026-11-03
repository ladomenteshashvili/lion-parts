"""Parts admin: the quote request "price ready" action prices through the engine (G§7.2, G§7.6).
Replaces the legacy admin tests test_admin_action_* (old final_price_gel flow)."""

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomerTariff
from accounts.testing import make_customer
from parts.models import PartQuoteRequest
from pricing.models import ExchangeRate
from pricing.rates import RateUnavailable

D = Decimal
URL = "/admin/parts/partquoterequest/"


class PriceReadyActionTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.customer = make_customer()
        self.client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))

    def request_(self, **fields):
        defaults = {"customer": self.customer, "request_type": "weight_price", "part_number": "P-1", "brand": "OEM",
                    "supplier_price_usd": D("100.00"), "prepared_weight_kg": D("2.00")}
        return PartQuoteRequest.objects.create(**{**defaults, **fields})

    def run_action(self, *requests):
        return self.client.post(URL, {"action": "mark_price_ready", "_selected_action": [r.pk for r in requests]},
                                follow=True)

    def test_weight_and_manual_requests_are_priced_for_their_customer(self):
        weight, manual = self.request_(), self.request_(request_type="manual_search", currency="USD")
        self.run_action(weight, manual)
        weight.refresh_from_db()
        manual.refresh_from_db()
        for qr in (weight, manual):  # the approved base is canonical USD, whatever the display currency
            self.assertEqual((qr.unit_price_usd, qr.list_price_usd, qr.discount_usd), (D("164.26"), D("164.26"), D("0.00")))
            self.assertEqual(qr.status, "resolved")
            self.assertTrue(qr.is_price_ready)
            self.assertEqual((qr.markup_percent, qr.vat_percent, qr.shipping_usd_per_kg), (D("20.00"), D("18.00"), D("8.00")))
            self.assertGreater(qr.price_valid_until, qr.price_ready_at)
        self.assertEqual((weight.currency, manual.currency), ("GEL", "USD"))

    def test_partner_customer_gets_partner_prices(self):
        partner = CustomerTariff.objects.create(name="Partner", markup_percent=D("10"))
        self.customer.tariff = partner
        self.customer.save()
        qr = self.request_()
        self.run_action(qr)
        qr.refresh_from_db()
        self.assertEqual(qr.unit_price_usd, D("150.57"))

    def test_missing_weight_or_price_is_skipped(self):
        no_weight, no_price = self.request_(prepared_weight_kg=None), self.request_(supplier_price_usd=None)
        response = self.run_action(no_weight, no_price)
        for qr in (no_weight, no_price):
            qr.refresh_from_db()
            self.assertIsNone(qr.price_ready_at)
        warnings = [str(m) for m in response.context["messages"]]
        self.assertTrue(any(str(no_weight.pk) in m and str(no_price.pk) in m for m in warnings), warnings)

    def test_rate_failure_leaves_the_request_unpriced(self):
        qr = self.request_()
        with mock.patch("parts.services.price_offer", side_effect=RateUnavailable()):
            self.run_action(qr)
        qr.refresh_from_db()
        self.assertIsNone(qr.price_ready_at)

    def test_change_page_and_list_render(self):
        qr = self.request_()
        self.assertEqual(self.client.get(f"{URL}{qr.pk}/change/").status_code, 200)
        self.assertEqual(self.client.get(f"{URL}?q=P-1").status_code, 200)
        self.assertEqual(self.client.get("/admin/parts/partsearchlog/?q=x").status_code, 200)
