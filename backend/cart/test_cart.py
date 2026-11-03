"""LP-BE-503: cart read & line operations (G§7.7, G§8.5, G§8.0 Cart).
Restores the legacy get_cart / remove_cart_item / cart_total scenarios."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomerTariff
from accounts.testing import make_customer
from cart.models import Cart, CartItem
from pricing.models import Discount, ExchangeRate

D = Decimal
CART_KEYS = {"currency", "items", "summary", "payment_terms", "checkout_blocked", "checkout_blocked_reasons", "merge"}
ITEM_KEYS = {"id", "source", "part_number", "name", "brand", "condition", "quantity", "vin", "list_unit_price",
             "discount_unit_amount", "unit_price", "previous_unit_price", "line_total", "status", "eta_min_days",
             "eta_max_days", "weight_kg", "weight_source", "customer_notice", "price_valid_until", "image_urls"}


@override_settings(PARTS_PROVIDER="demo")
class CartTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.client = APIClient()

    def add(self, part="G9010-60020", index=0, quantity=1, client=None):
        client = client or self.client
        data = client.post("/api/parts/search/", {"part_number": part}, format="json").json()
        return client.post("/api/cart/items/", {"source": "search", "search_id": data["search_id"],
                                                "offer_id": data["offers"][index]["offer_id"], "quantity": quantity},
                           format="json").json()

    def get(self, client=None):
        response = (client or self.client).get("/api/cart/")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_empty_cart_without_creating_one(self):
        self.assertEqual(self.get(), {
            "currency": "GEL", "items": [], "payment_terms": "full", "checkout_blocked": False,
            "checkout_blocked_reasons": [], "merge": None,
            "summary": {"items_count": 0, "items_total": "0.00", "discount_total": "0.00", "delivery_fee": None,
                        "total": "0.00"}})
        self.assertFalse(Cart.objects.exists())

    def test_contract_keys_and_totals_with_a_discount(self):
        Discount.objects.create(name="OEM -10 USD", kind="fixed", value=10, brand="OEM")
        self.add(index=0, quantity=2)
        self.add(index=1, quantity=1)
        cart = self.get()
        self.assertEqual(set(cart), CART_KEYS)
        for item in cart["items"]:
            self.assertEqual(set(item), ITEM_KEYS)
        first, second = cart["items"]
        self.assertEqual((first["discount_unit_amount"], second["discount_unit_amount"]), ("26.00", "0.00"))  # 10 USD x 2.6
        self.assertEqual(first["line_total"], f"{D(first['unit_price']) * 2:.2f}")
        items_total = D(first["list_unit_price"]) * 2 + D(second["list_unit_price"])
        self.assertEqual(cart["summary"], {
            "items_count": 3, "items_total": f"{items_total:.2f}", "discount_total": "52.00", "delivery_fee": None,
            "total": f"{items_total - 52:.2f}"})

    def test_expired_and_unavailable_block_checkout_price_changed_does_not(self):
        self.add(index=0)
        self.add(index=1)
        self.add(index=2)
        first, second, third = CartItem.objects.order_by("id")
        first.price_valid_until = timezone.now() - timedelta(seconds=1)
        first.save()
        CartItem.objects.filter(pk=second.pk).update(availability_status="price_changed", previous_unit_price=D("1.00"))
        CartItem.objects.filter(pk=third.pk).update(availability_status="unavailable")
        cart = self.get()
        self.assertEqual([i["status"] for i in cart["items"]], ["expired", "price_changed", "unavailable"])
        self.assertEqual(cart["items"][1]["previous_unit_price"], "1.00")
        self.assertTrue(cart["checkout_blocked"])
        self.assertEqual(cart["checkout_blocked_reasons"],
                         [{"item_id": first.pk, "code": "expired"}, {"item_id": third.pk, "code": "unavailable"}])

    def test_patch_quantity_and_vin(self):
        item_id = self.add()["items"][0]["id"]
        url = f"/api/cart/items/{item_id}/"
        cart = self.client.patch(url, {"quantity": 5, "vin": "wba8e9g50gnu12345"}, format="json").json()
        self.assertEqual((cart["items"][0]["quantity"], cart["items"][0]["vin"]), (5, "WBA8E9G50GNU12345"))
        self.assertEqual(self.client.patch(url, {"quantity": 2}, format="json").json()["items"][0]["vin"],
                         "WBA8E9G50GNU12345")  # absent VIN = unchanged
        self.assertEqual(self.client.patch(url, {"vin": ""}, format="json").json()["items"][0]["vin"], "")
        response = self.client.patch(url, {"vin": "SHORT"}, format="json")
        self.assertEqual(response.json()["fields"]["vin"][0]["code"], "invalid_vin")
        response = self.client.patch(url, {"quantity": 100}, format="json")
        self.assertEqual(response.json()["fields"]["quantity"][0]["code"], "invalid_quantity")
        self.assertEqual(CartItem.objects.get().quantity, 2)

    def test_delete_line(self):
        item_id = self.add()["items"][0]["id"]
        cart = self.client.delete(f"/api/cart/items/{item_id}/").json()
        self.assertEqual(cart["items"], [])

    def test_lines_of_another_cart_are_404(self):
        item_id = self.add(client=APIClient())["items"][0]["id"]
        self.add()
        self.assertEqual(self.client.patch(f"/api/cart/items/{item_id}/", {"quantity": 2}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/cart/items/{item_id}/").status_code, 404)
        self.assertEqual(APIClient().delete(f"/api/cart/items/{item_id}/").status_code, 404)

    def test_apply_vin_to_every_line(self):
        self.add(index=0)
        self.add(index=1)
        cart = self.client.post("/api/cart/apply-vin/", {"vin": "WBA8E9G50GNU12345"}, format="json").json()
        self.assertEqual({i["vin"] for i in cart["items"]}, {"WBA8E9G50GNU12345"})
        response = self.client.post("/api/cart/apply-vin/", {"vin": "bad"}, format="json")
        self.assertEqual(response.json()["fields"]["vin"][0]["code"], "invalid_vin")

    def test_me_cart_count_is_the_sum_of_quantities(self):
        self.assertEqual(self.client.get("/api/auth/me/").json()["cart_count"], 0)
        self.add(index=0, quantity=2)
        self.add(index=1, quantity=3)
        self.assertEqual(self.client.get("/api/auth/me/").json()["cart_count"], 5)

    def test_payment_terms_follow_the_tariff(self):
        partner = CustomerTariff.objects.create(name="Partner", payment_terms="split_70_30")
        client = APIClient()
        client.force_login(make_customer(tariff=partner).user)
        self.assertEqual(self.get(client)["payment_terms"], "split_70_30")
        self.assertEqual(self.get()["payment_terms"], "full")

    def test_merge_summary_is_returned_once(self):
        self.add()
        summary = {"merged": True, "added_items": 1, "summed": [], "problems": []}
        Cart.objects.update(merge_summary=summary)
        self.assertEqual(self.get()["merge"], summary)
        self.assertIsNone(self.get()["merge"])
