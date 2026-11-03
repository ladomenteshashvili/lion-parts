"""LP-BE-404: search history, seen, clear (G§7.6, G§8.4 history).
Restores the legacy feed_* scenarios (docs/legacy-test-map.md)."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import TariffMarkup
from accounts.testing import make_customer
from orders.models import Order, OrderItem
from parts.models import PartQuoteRequest, PartSearchLog
from parts.services import mark_quote_price_ready
from pricing.models import ExchangeRate

D = Decimal
ITEM_KEYS = {"id", "kind", "search_id", "quote_request_id", "part_number", "vin", "name", "brand", "status",
             "unit_price", "previous_unit_price", "currency", "offers_count", "eta_min_days", "eta_max_days",
             "weight_kg", "weight_source", "price_valid_until", "is_new", "purchased_order_number",
             "operator_message", "created_at"}


@override_settings(PARTS_PROVIDER="demo")
class HistoryTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.customer = make_customer(is_phone_verified=True)
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def search(self, part="17940-25020", client=None, **body):
        return (client or self.client).post("/api/parts/search/", {"part_number": part, **body}, format="json").json()

    def history(self, **params):
        response = self.client.get("/api/parts/history/", params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def quote(self, **fields):
        return PartQuoteRequest.objects.create(customer=self.customer, request_type="weight_price", **fields)

    def test_contract_and_dedupe_by_normalized_part_and_vin(self):
        self.search("17940-25020")
        latest = self.search("1794025020")
        self.search("1794025020", vin="WBA8E9G50GNU12345")  # another VIN: its own item
        data = self.history()
        self.assertEqual(list(data), ["count", "new_count", "next", "previous", "results"])
        self.assertEqual(data["count"], 2)
        item = next(i for i in data["results"] if i["vin"] == "")
        self.assertEqual(set(item), ITEM_KEYS)
        self.assertEqual((item["id"], item["search_id"], item["status"], item["offers_count"]),
                         (f"log-{latest['search_id']}", latest["search_id"], "ready", 4))
        cheapest = min(D(o["unit_price"]) for o in latest["offers"] if o["unit_price"])
        self.assertEqual(item["unit_price"], f"{cheapest:.2f}")

    def test_price_changed_against_the_previous_search(self):
        first = self.search()
        TariffMarkup.objects.filter(tariff__is_default=True, currency="GEL").update(markup_percent=D("25"))
        self.search()
        item = self.history()["results"][0]
        previous = min(D(o["unit_price"]) for o in first["offers"] if o["unit_price"])
        self.assertEqual((item["status"], item["previous_unit_price"]), ("price_changed", f"{previous:.2f}"))

    def test_a_part_later_ordered_shows_purchased(self):
        self.search("17940-25020")
        order = Order.objects.create(order_number="LP-2026-00042", session_id="s", customer=self.customer,
                                     customer_name="Nino", customer_phone="555123456", payment_type=Order.PAYMENT_FULL,
                                     status=Order.STATUS_PROCESSING, total_gel=D("10"))
        OrderItem.objects.create(order=order, cart_item_id="c", quote_id="q", part_option_id="o",
                                 part_number="17940 25020", name="Hose", final_price_gel=D("10"), quantity=1)
        item = self.history()["results"][0]
        self.assertEqual((item["status"], item["purchased_order_number"]), ("purchased", "LP-2026-00042"))

    def test_statuses_and_filters(self):
        self.search("READY-1")
        self.search("NF-1")
        expired = self.search("OLD-1")
        PartSearchLog.objects.filter(pk=expired["search_id"]).update(price_valid_until=timezone.now() - timedelta(seconds=1))
        self.quote(part_number="QR-CHECK")
        self.quote(part_number="QR-CANCEL", status="cancelled")
        ready_qr = self.quote(part_number="QR-READY", supplier_price_usd=D("50"), prepared_weight_kg=D("1"))
        mark_quote_price_ready(ready_qr)

        statuses = {i["part_number"]: i["status"] for i in self.history()["results"]}
        self.assertEqual(statuses, {"READY-1": "ready", "NF-1": "not_found", "OLD-1": "expired",
                                    "QR-CHECK": "checking", "QR-CANCEL": "cancelled", "QR-READY": "price_ready"})
        part_numbers = lambda f: {i["part_number"] for i in self.history(filter=f)["results"]}
        self.assertEqual(part_numbers("ready"), {"READY-1", "QR-READY"})
        self.assertEqual(part_numbers("checking"), {"QR-CHECK"})
        self.assertEqual(part_numbers("other"), {"NF-1", "OLD-1", "QR-CANCEL"})
        response = self.client.get("/api/parts/history/", {"filter": "bogus"})
        self.assertEqual(response.json()["fields"]["filter"][0]["code"], "invalid")

    def test_quote_request_goes_from_checking_to_price_ready_and_new(self):
        qr = self.quote(part_number="17940-25020", supplier_price_usd=D("50"), prepared_weight_kg=D("3"),
                        operator_message="3.0 kg")
        self.assertEqual(self.history()["results"][0]["status"], "checking")
        mark_quote_price_ready(qr)
        data = self.history()
        item = data["results"][0]
        self.assertEqual((item["id"], item["status"], item["is_new"], item["weight_kg"], item["weight_source"],
                          item["operator_message"]), (f"qr-{qr.pk}", "price_ready", True, "3.00", "operator", "3.0 kg"))
        self.assertEqual(data["new_count"], 1)
        self.assertEqual(self.client.post("/api/parts/history/seen/").status_code, 204)
        data = self.history()
        self.assertEqual((data["new_count"], data["results"][0]["is_new"]), (0, False))

    def test_delete_hides_searches_and_keeps_quote_requests(self):
        self.search()
        self.quote(part_number="OPEN-1")
        self.assertEqual(self.client.delete("/api/parts/history/").status_code, 204)
        self.assertEqual([i["part_number"] for i in self.history()["results"]], ["OPEN-1"])
        self.assertTrue(PartSearchLog.objects.filter(hidden_at__isnull=False).exists())

    def test_only_own_history_and_login_required(self):
        other = APIClient()
        other.force_login(make_customer(email="o@example.com", phone="599000000").user)
        self.search("OTHERS-PART", client=other)
        self.search("GUEST-PART", client=APIClient())
        self.assertEqual(self.history()["count"], 0)
        self.assertEqual(APIClient().get("/api/parts/history/").status_code, 401)

    def test_pagination(self):
        for i in range(21):
            self.search(f"P-{i}")
        data = self.history()
        self.assertEqual((data["count"], len(data["results"])), (21, 20))
        self.assertIsNotNone(data["next"])
