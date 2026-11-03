from decimal import Decimal
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import CarrierService, PartQuoteRequest, PartSearchLog
from .admin import PartQuoteRequestAdmin
from accounts.models import Customer, CustomerTariff
from accounts.testing import make_customer

from api.legacy import legacy_endpoint_test

@override_settings(PARTS_PROVIDER="demo")
class PartsSearchApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @legacy_endpoint_test("LP-BE-402/403")
    def test_feed_returns_recent_searches_for_verified_phone(self):
        session_id = "feed-session"

        Customer.objects.create(
            session_id=session_id,
            name="Feed Customer",
            phone="555111222",
            is_phone_verified=True,
        )

        self.client.post(
            "/api/parts/search/",
            {
                "session_id": session_id,
                "part_number": "FEED123",
                "vin": "TESTVIN123",
            },
            format="json",
        )

        response = self.client.get(
            f"/api/parts/feed/?session_id={session_id}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["requires_phone_verification"])
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["part_number"], "FEED123")
        self.assertEqual(response.data["results"][0]["vin"], "TESTVIN123")
        self.assertEqual(response.data["results"][0]["found_count"], 3)

        log = PartSearchLog.objects.get(part_number="FEED123")
        self.assertEqual(log.customer_phone, "555111222")
        self.assertEqual(log.customer_name, "Feed Customer")

    @legacy_endpoint_test("LP-BE-402/403")
    def test_feed_allows_same_verified_phone_from_new_session(self):
        first_session_id = "feed-first-session"
        second_session_id = "feed-second-session"

        first_customer = Customer.objects.create(
            session_id=first_session_id,
            name="Feed Customer",
            phone="555111222",
            is_phone_verified=True,
        )

        CustomerSession.objects.create(
            customer=first_customer,
            session_id=second_session_id,
        )

        self.client.post(
            "/api/parts/search/",
            {
                "session_id": first_session_id,
                "part_number": "SAMEPHONE123",
            },
            format="json",
        )

        response = self.client.get(
            f"/api/parts/feed/?session_id={second_session_id}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["part_number"],
            "SAMEPHONE123",
        )

    @legacy_endpoint_test("LP-BE-402/403")
    def test_feed_blocks_different_verified_phone(self):
        first_session_id = "feed-owner-session"
        second_session_id = "feed-other-session"

        Customer.objects.create(
            session_id=first_session_id,
            name="Owner",
            phone="555111222",
            is_phone_verified=True,
        )

        Customer.objects.create(
            session_id=second_session_id,
            name="Other",
            phone="555999888",
            is_phone_verified=True,
        )

        self.client.post(
            "/api/parts/search/",
            {
                "session_id": first_session_id,
                "part_number": "PRIVATE123",
            },
            format="json",
        )

        response = self.client.get(
            f"/api/parts/feed/?session_id={second_session_id}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"], [])

    @legacy_endpoint_test("LP-BE-402/403")
    def test_feed_requires_verified_phone(self):
        session_id = "feed-unverified-session"

        Customer.objects.create(
            session_id=session_id,
            name="Unverified",
            phone="555111222",
            is_phone_verified=False,
        )

        response = self.client.get(
            f"/api/parts/feed/?session_id={session_id}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["requires_phone_verification"])
        self.assertEqual(response.data["results"], [])



@override_settings(PARTS_PROVIDER="amt")
class PartPriceCalculationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "weight-enabled-session"

        self.tariff = CustomerTariff.objects.create(
            name="Weight enabled",
            markup_percent=Decimal("20.00"),
            can_enter_weight=True,
        )

        self.customer = Customer.objects.create(
            session_id=self.session_id,
            name="Weight Customer",
            phone="+995555123456",
            tariff=self.tariff,
        )

        CarrierService.objects.create(
            name="USA Standard",
            usd_per_kg=Decimal("8.00"),
            min_eta_days=10,
            max_eta_days=14,
            is_active=True,
            is_default=True,
        )

    @legacy_endpoint_test("LP-BE-405")
    @patch("parts.providers.get_usd_sell_rate")
    @patch("parts.providers.get_price_by_oem")
    def test_calculate_part_price_with_manual_weight(
        self,
        mock_get_price_by_oem,
        mock_get_usd_sell_rate,
    ):
        mock_get_price_by_oem.return_value = [
            {
                "brand": "Chrysler",
                "descr": "BRACKET-HEADLAMP",
                "list_price": 47.29,
                "oem": "68275354AC",
                "replace": "",
                "weight": 0.0,
            }
        ]
        mock_get_usd_sell_rate.return_value = Decimal("2.6235")

        response = self.client.post(
            "/api/parts/calculate-price/",
            {
                "session_id": self.session_id,
                "part_number": "68275354AC",
                "part_option_id": "AMT-1-68275354AC",
                "weight_kg": 2.5,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["part_option_id"], "AMT-1-68275354AC")
        self.assertEqual(response.data["name"], "BRACKET-HEADLAMP")
        self.assertEqual(response.data["brand"], "Chrysler")
        self.assertEqual(response.data["final_price_gel"], 249.97)
        self.assertEqual(response.data["currency"], "GEL")
        self.assertFalse(response.data["requires_weight_input"])
        self.assertEqual(response.data["weight_kg"], 2.5)

        self.assertNotIn("api_price_usd", response.data)
        self.assertNotIn("supplier_price", response.data)
        self.assertNotIn("internal_cost", response.data)

    @legacy_endpoint_test("LP-BE-405")
    def test_calculate_part_price_requires_weight_permission(self):
        disabled_session_id = "weight-disabled-session"

        disabled_tariff = CustomerTariff.objects.create(
            name="Weight disabled",
            markup_percent=Decimal("20.00"),
            can_enter_weight=False,
        )

        Customer.objects.create(
            session_id=disabled_session_id,
            name="Disabled Customer",
            phone="+995599999999",
            tariff=disabled_tariff,
        )

        response = self.client.post(
            "/api/parts/calculate-price/",
            {
                "session_id": disabled_session_id,
                "part_number": "68275354AC",
                "part_option_id": "AMT-1-68275354AC",
                "weight_kg": 2.5,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.data["detail"],
            "weight entry is not enabled for this customer",
        )

    @legacy_endpoint_test("LP-BE-405")
    def test_calculate_part_price_requires_positive_weight(self):
        response = self.client.post(
            "/api/parts/calculate-price/",
            {
                "session_id": self.session_id,
                "part_number": "68275354AC",
                "part_option_id": "AMT-1-68275354AC",
                "weight_kg": 0,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("weight_kg", response.data)


class PartQuoteRequestApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "guest-test-session"

        self.customer = make_customer(first_name="Test", last_name="Customer")

    @legacy_endpoint_test("LP-BE-406")
    def test_feed_shows_processing_and_then_ready_price(self):
        customer = self.customer
        customer.is_phone_verified = True
        customer.save(update_fields=["is_phone_verified", "updated_at"])

        quote_request = PartQuoteRequest.objects.create(
            session_id=self.session_id,
            part_number="NO-WEIGHT-1",
            customer_name=customer.full_name,
            customer_phone=customer.phone,
            name="Part without supplier weight",
        )

        response = self.client.get(
            f"/api/parts/feed/?session_id={self.session_id}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["feed_status"], "processing")
        self.assertIsNone(response.data["results"][0]["notification_token"])

        quote_request.prepared_weight_kg = Decimal("3.40")
        quote_request.final_price_gel = Decimal("725.50")
        quote_request.status = PartQuoteRequest.STATUS_RESOLVED
        quote_request.price_ready_at = timezone.now()
        quote_request.save()

        response = self.client.get(
            f"/api/parts/feed/?session_id={self.session_id}",
        )
        item = response.data["results"][0]
        self.assertEqual(item["feed_status"], "price_ready")
        self.assertEqual(item["top_result_price_gel"], Decimal("725.50"))
        self.assertEqual(item["weight_kg"], Decimal("3.40"))
        self.assertEqual(item["notification_token"], quote_request.notification_token)

