from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Customer, CustomerSession
from cart.models import Cart, CartItem
from orders.models import Order, OrderItem, OrderItemEvent, Payment


def create_customer(session_id, verified=True, phone="555123456"):
    customer = Customer.objects.create(
        session_id=session_id,
        name="Checkout Customer",
        phone=phone,
        is_phone_verified=verified,
    )

    CustomerSession.objects.create(
        customer=customer,
        session_id=session_id,
    )

    return customer


def create_cart_with_item(session_id):
    cart = Cart.objects.create(session_id=session_id)

    CartItem.objects.create(
        cart=cart,
        cart_item_id="checkout-quote:checkout-option:CHK123",
        quote_id="checkout-quote",
        part_option_id="checkout-option",
        part_number="CHK123",
        name="Checkout Test Part",
        condition="New",
        brand="OEM",
        availability="Available",
        eta_days=10,
        weight_kg=Decimal("2.50"),
        final_price_gel=Decimal("150.00"),
        currency="GEL",
        note="Cart item note",
        customer_notice="Cart customer notice",
        weight_source="api",
        quantity=2,
    )

    return cart


class CheckoutRegressionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "checkout-session"

    def test_checkout_creates_order_payment_items_and_clears_cart(self):
        customer = create_customer(self.session_id, verified=True)
        cart = create_cart_with_item(self.session_id)

        response = self.client.post(
            "/api/orders/checkout/",
            {
                "session_id": self.session_id,
                "customer_name": "Submitted Name",
                "customer_phone": "+995555123456",
                "vin": "CHECKOUTVIN123456",
                "note": "Please verify fitment before order.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(OrderItem.objects.count(), 1)

        order = Order.objects.select_related("customer", "payment").get()
        item = order.items.get()

        self.assertEqual(order.customer_id, customer.id)
        self.assertEqual(order.session_id, self.session_id)
        self.assertEqual(order.customer_name, "Checkout Customer")
        self.assertEqual(order.customer_phone, "555123456")
        self.assertEqual(order.current_customer_name, "Checkout Customer")
        self.assertEqual(order.current_customer_phone, "555123456")
        self.assertEqual(order.vin, "CHECKOUTVIN123456")
        self.assertEqual(order.note, "Please verify fitment before order.")
        self.assertEqual(order.status, Order.STATUS_PAYMENT_PENDING)
        self.assertEqual(order.total_gel, Decimal("300.00"))

        self.assertEqual(order.payment.status, Payment.STATUS_PENDING)
        self.assertEqual(order.payment.amount_gel, Decimal("300.00"))
        self.assertEqual(order.payment.currency, "GEL")

        self.assertEqual(item.part_number, "CHK123")
        self.assertEqual(item.name, "Checkout Test Part")
        self.assertEqual(item.final_price_gel, Decimal("150.00"))
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.item_status, OrderItem.ITEM_STATUS_CREATED)
        self.assertFalse(item.action_required)

        self.assertTrue(
            OrderItemEvent.objects.filter(
                item=item,
                event_type=OrderItemEvent.EVENT_TYPE_CREATED,
                visible_to_customer=True,
            ).exists()
        )

        cart.refresh_from_db()
        self.assertEqual(cart.items.count(), 0)

        self.assertEqual(response.data["customer_name"], "Checkout Customer")
        self.assertEqual(response.data["customer_phone"], "555123456")
        self.assertEqual(response.data["current_customer_phone"], "555123456")
        self.assertEqual(response.data["status"], Order.STATUS_PAYMENT_PENDING)
        self.assertEqual(response.data["total_gel"], "300.00")
        self.assertEqual(len(response.data["items"]), 1)

    def test_checkout_rejects_unverified_customer_and_keeps_cart(self):
        create_customer(self.session_id, verified=False)
        cart = create_cart_with_item(self.session_id)

        response = self.client.post(
            "/api/orders/checkout/",
            {
                "session_id": self.session_id,
                "customer_name": "Checkout Customer",
                "customer_phone": "+995555123456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["detail"], "phone verification required")
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)

        cart.refresh_from_db()
        self.assertEqual(cart.items.count(), 1)

    def test_checkout_rejects_phone_that_does_not_match_verified_customer(self):
        create_customer(self.session_id, verified=True, phone="555123456")
        cart = create_cart_with_item(self.session_id)

        response = self.client.post(
            "/api/orders/checkout/",
            {
                "session_id": self.session_id,
                "customer_name": "Checkout Customer",
                "customer_phone": "+995599777777",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "checkout phone must match verified phone",
        )
        self.assertEqual(Order.objects.count(), 0)

        cart.refresh_from_db()
        self.assertEqual(cart.items.count(), 1)

    def test_checkout_rejects_empty_cart(self):
        create_customer(self.session_id, verified=True)
        Cart.objects.create(session_id=self.session_id)

        response = self.client.post(
            "/api/orders/checkout/",
            {
                "session_id": self.session_id,
                "customer_name": "Checkout Customer",
                "customer_phone": "+995555123456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "cart is empty")
        self.assertEqual(Order.objects.count(), 0)


class PaymentVerificationRegressionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "payment-session"
        self.customer = create_customer(self.session_id, verified=True)

        self.order = Order.objects.create(
            order_number="LP-PAY-0001",
            session_id=self.session_id,
            customer=self.customer,
            customer_name=self.customer.name,
            customer_phone=self.customer.phone,
            payment_type=Order.PAYMENT_FULL,
            status=Order.STATUS_PAYMENT_PENDING,
            total_gel=Decimal("120.00"),
        )

        self.payment = Payment.objects.create(
            order=self.order,
            payment_reference="PAY-PAY-0001",
            provider=Payment.PROVIDER_DEMO,
            status=Payment.STATUS_PENDING,
            amount_gel=Decimal("120.00"),
            currency="GEL",
        )

        self.item = OrderItem.objects.create(
            order=self.order,
            cart_item_id="payment-cart-item",
            quote_id="payment-quote",
            part_option_id="payment-option",
            part_number="PAY123",
            name="Payment Test Part",
            condition="New",
            brand="OEM",
            availability="Available",
            eta_days=14,
            weight_kg=Decimal("1.20"),
            final_price_gel=Decimal("120.00"),
            currency="GEL",
            quantity=1,
            item_status=OrderItem.ITEM_STATUS_CREATED,
        )

    @override_settings(ENABLE_DEMO_ORDER_ENDPOINTS=True)
    def test_verify_payment_marks_payment_order_and_items_as_paid(self):
        response = self.client.post(
            f"/api/orders/{self.order.order_number}/verify-payment/",
            {
                "session_id": self.session_id,
                "payment_reference": "PAY-PAY-0001",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        self.item.refresh_from_db()

        self.assertEqual(self.payment.status, Payment.STATUS_PAID)
        self.assertIsNotNone(self.payment.paid_at)
        self.assertEqual(self.order.status, Order.STATUS_PROCESSING)
        self.assertEqual(
            self.item.item_status,
            OrderItem.ITEM_STATUS_PAYMENT_CONFIRMED,
        )
        self.assertFalse(self.item.action_required)

        self.assertTrue(
            OrderItemEvent.objects.filter(
                item=self.item,
                title="გადახდა დადასტურებულია",
                visible_to_customer=True,
            ).exists()
        )

        self.assertEqual(response.data["payment"]["status"], Payment.STATUS_PAID)
        self.assertEqual(response.data["status"], Order.STATUS_PROCESSING)
        self.assertEqual(
            response.data["items"][0]["item_status"],
            OrderItem.ITEM_STATUS_PAYMENT_CONFIRMED,
        )

    @override_settings(ENABLE_DEMO_ORDER_ENDPOINTS=True)
    def test_verify_payment_rejects_wrong_payment_reference(self):
        response = self.client.post(
            f"/api/orders/{self.order.order_number}/verify-payment/",
            {
                "session_id": self.session_id,
                "payment_reference": "WRONG-REFERENCE",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "payment_reference does not match this order",
        )

        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.item.refresh_from_db()

        self.assertEqual(self.payment.status, Payment.STATUS_PENDING)
        self.assertEqual(self.order.status, Order.STATUS_PAYMENT_PENDING)
        self.assertEqual(self.item.item_status, OrderItem.ITEM_STATUS_CREATED)

    @override_settings(ENABLE_DEMO_ORDER_ENDPOINTS=False)
    def test_verify_payment_is_hidden_when_demo_endpoints_are_disabled(self):
        response = self.client.post(
            f"/api/orders/{self.order.order_number}/verify-payment/",
            {
                "session_id": self.session_id,
                "payment_reference": "PAY-PAY-0001",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["detail"], "not found")
