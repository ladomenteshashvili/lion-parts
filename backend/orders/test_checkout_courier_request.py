from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Customer, CustomerSession
from cart.models import Cart, CartItem
from orders.models import Order


def create_customer(session_id):
    customer = Customer.objects.create(
        session_id=session_id,
        name="Courier Customer",
        phone="555123456",
        is_phone_verified=True,
    )
    CustomerSession.objects.create(customer=customer, session_id=session_id)
    return customer


def create_cart_with_item(session_id):
    cart = Cart.objects.create(session_id=session_id)

    CartItem.objects.create(
        cart=cart,
        cart_item_id="courier-cart-item",
        quote_id="courier-quote",
        part_option_id="courier-option",
        part_number="COURIER123",
        name="Courier Test Part",
        condition="New",
        brand="OEM",
        availability="Available",
        eta_days=10,
        weight_kg=Decimal("2.50"),
        final_price_gel=Decimal("150.00"),
        currency="GEL",
        quantity=1,
    )

    return cart


class CheckoutCourierRequestTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "checkout-courier-session"

    def test_checkout_stores_courier_delivery_request(self):
        create_customer(self.session_id)
        create_cart_with_item(self.session_id)

        response = self.client.post(
            "/api/orders/checkout/",
            {
                "session_id": self.session_id,
                "customer_name": "Courier Customer",
                "customer_phone": "+995555123456",
                "courier_delivery_requested": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        order = Order.objects.get()

        self.assertTrue(order.courier_delivery_requested)
        self.assertTrue(response.data["courier_delivery_requested"])

    def test_checkout_defaults_courier_delivery_request_to_false(self):
        create_customer(self.session_id)
        create_cart_with_item(self.session_id)

        response = self.client.post(
            "/api/orders/checkout/",
            {
                "session_id": self.session_id,
                "customer_name": "Courier Customer",
                "customer_phone": "+995555123456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        order = Order.objects.get()

        self.assertFalse(order.courier_delivery_requested)
        self.assertFalse(response.data["courier_delivery_requested"])
