from django.test import TestCase
from rest_framework.test import APIClient


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