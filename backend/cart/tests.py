from decimal import Decimal
from urllib.parse import quote

from django.test import TestCase
from rest_framework.test import APIClient


from api.legacy import legacy_endpoint_test


class CartApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "test-cart-session"

        self.cart_item_id = "Q-DEMO-0001:P-DEMO-001:1565461"

        self.item_payload = {
            "session_id": self.session_id,
            "cart_item_id": self.cart_item_id,
            "quote_id": "Q-DEMO-0001",
            "part_option_id": "P-DEMO-001",
            "part_number": "1565461",
            "name": "Demo OEM Part",
            "condition": "New",
            "brand": "OEM",
            "availability": "Available",
            "eta_days": 14,
            "final_price_gel": "650.00",
            "currency": "GEL",
            "note": "Demo offer.",
            "quantity": 1,
        }

    @legacy_endpoint_test("LP-BE-502/503")
    def test_get_empty_cart(self):
        response = self.client.get(f"/api/cart/?session_id={self.session_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["session_id"], self.session_id)
        self.assertEqual(response.data["items"], [])
        self.assertEqual(Decimal(str(response.data["total_gel"])), Decimal("0"))

    @legacy_endpoint_test("LP-BE-502/503")
    def test_cart_total_uses_quantity(self):
        payload = {
            **self.item_payload,
            "quantity": 2,
        }

        response = self.client.post(
            "/api/cart/items/",
            payload,
            format="json",
        )

        self.assertIn(response.status_code, [200, 201])
        self.assertEqual(Decimal(str(response.data["total_gel"])), Decimal("1300.0"))

    @legacy_endpoint_test("LP-BE-502/503")
    def test_remove_cart_item(self):
        add_response = self.client.post(
            "/api/cart/items/",
            self.item_payload,
            format="json",
        )
        self.assertIn(add_response.status_code, [200, 201])

        cart_item_id = add_response.data["items"][0]["cart_item_id"]
        encoded_cart_item_id = quote(cart_item_id, safe="")

        response = self.client.delete(
            f"/api/cart/items/{encoded_cart_item_id}/?session_id={self.session_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["items"], [])
        self.assertEqual(Decimal(str(response.data["total_gel"])), Decimal("0"))

        cart = Cart.objects.get(session_id=self.session_id)
        self.assertEqual(cart.items.count(), 0)

    @legacy_endpoint_test("LP-BE-502/503")
    def test_get_cart_requires_session_id(self):
        response = self.client.get("/api/cart/")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "session_id is required")

