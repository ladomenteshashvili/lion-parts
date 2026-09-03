from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import PartQuoteRequest
from accounts.models import Customer

@override_settings(PARTS_PROVIDER="demo")
class PartsSearchApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_search_requires_part_number(self):
        response = self.client.post(
            "/api/parts/search/",
            {
                "part_number": "",
                "vin": "",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "part_number is required")

    def test_search_returns_demo_quote(self):
        response = self.client.post(
            "/api/parts/search/",
            {
                "part_number": "1565461",
                "vin": "",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.data["quote_id"], "Q-DEMO-0001")
        self.assertEqual(response.data["part_number"], "1565461")
        self.assertIsNone(response.data["vin"])

        self.assertIn("results", response.data)
        self.assertEqual(len(response.data["results"]), 3)

        result = response.data["results"][0]

        self.assertEqual(result["part_option_id"], "P-DEMO-001")
        self.assertEqual(result["name"], "Demo OEM Part")
        self.assertEqual(result["condition"], "New")
        self.assertEqual(result["brand"], "OEM")
        self.assertEqual(result["availability"], "Available")
        self.assertEqual(result["eta_days"], 14)
        self.assertEqual(result["final_price_gel"], 650.00)
        self.assertEqual(result["currency"], "GEL")
        self.assertEqual(
            result["note"],
            "Original new part. Demo offer. Supplier integration will be added later.",
        )

    def test_search_returns_multiple_demo_offers(self):
        response = self.client.post(
            "/api/parts/search/",
            {
                "part_number": "1565461",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        results = response.data["results"]

        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["part_option_id"], "P-DEMO-001")
        self.assertEqual(results[1]["part_option_id"], "P-DEMO-002")
        self.assertEqual(results[2]["part_option_id"], "P-DEMO-003")

    def test_search_returns_vin_when_provided(self):
        response = self.client.post(
            "/api/parts/search/",
            {
                "part_number": "1565461",
                "vin": "1C6SRFKP6TN159390",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["part_number"], "1565461")
        self.assertEqual(response.data["vin"], "1C6SRFKP6TN159390")

    def test_search_trims_part_number_and_vin(self):
        response = self.client.post(
            "/api/parts/search/",
            {
                "part_number": "  1565461  ",
                "vin": "  1C6SRFKP6TN159390  ",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["part_number"], "1565461")
        self.assertEqual(response.data["vin"], "1C6SRFKP6TN159390")

    def test_search_response_has_customer_price_fields(self):
        response = self.client.post(
            "/api/parts/search/",
            {
                "part_number": "1565461",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        for result in response.data["results"]:
            self.assertIn("final_price_gel", result)
            self.assertIn("currency", result)
            self.assertEqual(result["currency"], "GEL")
            self.assertNotIn("supplier_price", result)
            self.assertNotIn("shipping_price", result)
            self.assertNotIn("internal_cost", result)

class PartQuoteRequestApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "guest-test-session"

        Customer.objects.create(
            session_id=self.session_id,
            name="Test Customer",
            phone="+995555123456",
            can_request_quote=True,
        )

    def test_create_quote_request(self):
        response = self.client.post(
            "/api/parts/quote-requests/",
            {
                "session_id": self.session_id,
                "part_number": "51118070648",
                "vin": "WBA12345678901234",
                "customer_name": "Test Customer",
                "customer_phone": "+995555123456",
                "comment": "Need front bumper cover",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        self.assertEqual(PartQuoteRequest.objects.count(), 1)

        quote_request = PartQuoteRequest.objects.first()

        self.assertEqual(quote_request.session_id, self.session_id)
        self.assertEqual(quote_request.part_number, "51118070648")
        self.assertEqual(quote_request.vin, "WBA12345678901234")
        self.assertEqual(quote_request.customer_name, "Test Customer")
        self.assertEqual(quote_request.customer_phone, "+995555123456")
        self.assertEqual(quote_request.comment, "Need front bumper cover")
        self.assertEqual(quote_request.status, PartQuoteRequest.STATUS_NEW)

        self.assertEqual(response.data["part_number"], "51118070648")
        self.assertEqual(response.data["status"], PartQuoteRequest.STATUS_NEW)

    def test_create_quote_request_requires_enabled_customer(self):
        disabled_session_id = "disabled-test-session"

        Customer.objects.create(
            session_id=disabled_session_id,
            name="Disabled Customer",
            phone="+995599999999",
            can_request_quote=False,
        )

        response = self.client.post(
            "/api/parts/quote-requests/",
            {
                "session_id": disabled_session_id,
                "part_number": "51118070648",
                "customer_phone": "+995599999999",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.data["detail"],
            "quote request is not enabled for this customer",
        )

    def test_create_quote_request_requires_session_id(self):
        response = self.client.post(
            "/api/parts/quote-requests/",
            {
                "part_number": "51118070648",
                "customer_phone": "+995555123456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("session_id", response.data)

    def test_create_quote_request_requires_part_number(self):
        response = self.client.post(
            "/api/parts/quote-requests/",
            {
                "session_id": self.session_id,
                "part_number": "",
                "customer_phone": "+995555123456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("part_number", response.data)

    def test_create_quote_request_requires_customer_phone(self):
        response = self.client.post(
            "/api/parts/quote-requests/",
            {
                "session_id": self.session_id,
                "part_number": "51118070648",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("customer_phone", response.data)