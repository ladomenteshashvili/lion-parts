"""LP-BE-406: quote requests & the public prepared-quote link (G§7.6, G§8.4).
Restores the legacy create_quote_request* / weight-price / magic-link scenarios."""

import uuid
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomerTariff
from accounts.testing import make_customer
from parts.models import PartQuoteRequest
from parts.services import mark_quote_price_ready
from pricing.models import ExchangeRate

D = Decimal
QR_KEYS = {"id", "request_type", "part_number", "vin", "name", "brand", "status", "is_price_ready", "unit_price",
           "price_valid_until",
           "currency", "weight_kg", "operator_message", "created_at"}
PUBLIC_KEYS = {"quote_request_id", "part_number", "name", "brand", "unit_price", "currency", "price_valid_until",
               "eta_min_days",
               "eta_max_days", "weight_kg", "operator_message", "requires_login"}


@override_settings(PARTS_PROVIDER="demo")
class QuoteRequestTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.customer = make_customer(is_phone_verified=True)
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def search(self, client=None):
        data = (client or self.client).post("/api/parts/search/", {"part_number": "17940-25020", "vin": "WBA8E9G50GNU12345"},
                                            format="json").json()
        return data["search_id"], data["offers"][-1]["offer_id"]  # the demo row without weight

    def create(self, client=None, **body):
        return (client or self.client).post("/api/parts/quote-requests/", body, format="json")

    def weight_request(self, client=None):
        search_id, offer_id = self.search(client)
        return self.create(client, request_type="weight_price", search_id=search_id, offer_id=offer_id)

    def test_weight_price_copies_the_offer_and_is_not_duplicated(self):
        response = self.weight_request()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(set(body), QR_KEYS)
        self.assertEqual((body["status"], body["is_price_ready"], body["unit_price"], body["name"], body["vin"]),
                         ("new", False, None, "Demo Part Without Weight", "WBA8E9G50GNU12345"))
        qr = PartQuoteRequest.objects.get()
        self.assertEqual((qr.supplier_price_usd, qr.brand, qr.customer), (D("95.00"), "Denso", self.customer))
        again = self.weight_request()  # a new search, same part and offer
        self.assertEqual((again.status_code, again.json()["id"]), (200, body["id"]))
        self.assertEqual(PartQuoteRequest.objects.count(), 1)

    def test_closed_request_allows_a_new_one(self):
        self.weight_request()
        PartQuoteRequest.objects.update(status="cancelled")
        self.assertEqual(self.weight_request().status_code, 201)

    def test_foreign_or_unknown_search_and_offer_are_404(self):
        other = APIClient()
        other.force_login(make_customer(email="o@example.com", phone="599000000", is_phone_verified=True).user)
        foreign_id, offer_id = self.search(other)
        for body in ({"search_id": foreign_id, "offer_id": offer_id}, {"search_id": 999999, "offer_id": offer_id}):
            with self.subTest(body=body):
                response = self.create(request_type="weight_price", **body)
                self.assertEqual((response.status_code, response.json()["code"]), (404, "not_found"))
        own_id, _ = self.search()
        self.assertEqual(self.create(request_type="weight_price", search_id=own_id, offer_id="NOPE").status_code, 404)

    def test_permissions(self):
        self.assertEqual(APIClient().post("/api/parts/quote-requests/", {}, format="json").status_code, 401)
        unverified = APIClient()
        unverified.force_login(make_customer(email="u@example.com", phone="599000001").user)
        response = self.create(unverified, request_type="manual_search", part_number="X")
        self.assertEqual((response.status_code, response.json()["code"]), (403, "phone_verification_required"))

    def test_manual_search_needs_the_tariff_permission(self):
        response = self.create(request_type="manual_search", part_number="17940-25099")
        self.assertEqual((response.status_code, response.json()["code"]), (403, "quote_request_not_allowed"))
        CustomerTariff.objects.filter(is_default=True).update(can_request_quote=True)
        response = self.create(request_type="manual_search", part_number="17940-25099", vin="WBA8E9G50GNU12345",
                               comment="left side")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(PartQuoteRequest.objects.get().comment, "left side")

    def test_required_fields_per_type(self):
        fields = lambda r: {k: [e["code"] for e in v] for k, v in r.json()["fields"].items()}
        self.assertEqual(fields(self.create(request_type="weight_price")),
                         {"search_id": ["required"], "offer_id": ["required"]})
        self.assertEqual(fields(self.create(request_type="manual_search")), {"part_number": ["required"]})
        self.assertEqual(fields(self.create(request_type="other")), {"request_type": ["invalid"]})

    def test_list_shows_only_own_requests(self):
        self.weight_request()
        PartQuoteRequest.objects.create(customer=make_customer(email="o@example.com", phone="599000000"),
                                        request_type="manual_search", part_number="OTHER")
        data = self.client.get("/api/parts/quote-requests/").json()
        self.assertEqual((data["count"], data["results"][0]["part_number"]), (1, "17940-25020"))
        self.assertEqual(set(data), {"count", "next", "previous", "results"})


class PublicQuoteTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.qr = PartQuoteRequest.objects.create(
            customer=make_customer(), request_type="weight_price", part_number="17940-25020", name="Air Intake Hose",
            brand="TOYOTA OEM", supplier_price_usd=D("100"), prepared_weight_kg=D("3.00"), operator_message="3.0 kg")

    def get(self, token):
        return APIClient().get(f"/api/public/q/{token}/")

    def test_public_only_after_the_price_is_ready(self):
        self.assertEqual(self.get(self.qr.notification_token).status_code, 404)
        mark_quote_price_ready(self.qr)
        response = self.get(self.qr.notification_token)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), PUBLIC_KEYS)
        self.assertEqual((data["unit_price"], data["weight_kg"], data["requires_login"]),
                         (f"{(self.qr.unit_price_usd * D('2.6')).quantize(D('0.01'))}", "3.00", True))
        text = response.content.decode()
        for private in ("555123456", "nino@example.com", "Nino", "Beridze"):
            self.assertNotIn(private, text)

    def test_unknown_or_malformed_token(self):
        self.assertEqual(self.get(uuid.uuid4()).status_code, 404)
        self.assertEqual(self.get("not-a-uuid").json()["code"], "not_found")
