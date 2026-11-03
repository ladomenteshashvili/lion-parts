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
from orders.services import OperatorError
from parts.services import mark_quote_ready
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
                                follow=True, headers={"accept-language": "en"})

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
        self.assertIn(f"#{no_weight.pk}: Enter a weight greater than zero.", warnings)  # one message per row
        self.assertIn(f"#{no_price.pk}: Enter the supplier price.", warnings)

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


class MarkQuoteReadyServiceTests(TestCase):
    """LP-BE-1104: mark_quote_ready re-queries the supplier for requests made from a search offer."""

    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.customer = make_customer()

    def offer_request(self, offer_id="AMT-1"):
        return PartQuoteRequest.objects.create(customer=self.customer, request_type="weight_price", part_number="P-1",
                                               brand="OEM", offer_id=offer_id, supplier_price_usd=D("90.00"))

    def rows(self, price=D("100.00"), offer_id="AMT-1"):
        return ("amt", {}, [{"offer_id": offer_id, "supplier_price_usd": price}])

    def test_supplier_is_asked_again_for_a_search_offer(self):
        qr = self.offer_request()
        with mock.patch("parts.providers.fetch_rows", return_value=self.rows()):
            mark_quote_ready(qr.pk, D("2.00"), "Checked")
        qr.refresh_from_db()
        self.assertEqual((qr.supplier_price_usd, qr.prepared_weight_kg, qr.operator_message, qr.unit_price_usd,
                          qr.status), (D("100.00"), D("2.00"), "Checked", D("164.26"), "resolved"))
        public = self.client.get(f"/api/public/q/{qr.notification_token}/").json()
        self.assertEqual((public["unit_price"], public["weight_kg"]), ("427.08", "2.00"))  # 164.26 x 2.6

    def test_offer_gone_or_supplier_down_changes_nothing(self):
        qr = self.offer_request()
        from parts.providers import PartsProviderError
        for patch in ({"return_value": self.rows(offer_id="OTHER")}, {"side_effect": PartsProviderError()}):
            with self.subTest(patch=patch), mock.patch("parts.providers.fetch_rows", **patch), \
                    self.assertRaises(OperatorError):
                mark_quote_ready(qr.pk, D("2.00"))
        qr.refresh_from_db()
        self.assertIsNone(qr.price_ready_at)

    def test_manual_supplier_price_wins_and_cancelled_requests_are_refused(self):
        qr = self.offer_request()
        with mock.patch("parts.providers.fetch_rows") as fetch:
            mark_quote_ready(qr.pk, D("2.00"), supplier_price_usd=D("100.00"))
        fetch.assert_not_called()
        PartQuoteRequest.objects.filter(pk=qr.pk).update(status="cancelled")
        with self.assertRaises(OperatorError):
            mark_quote_ready(qr.pk, D("2.00"), supplier_price_usd=D("100.00"))
