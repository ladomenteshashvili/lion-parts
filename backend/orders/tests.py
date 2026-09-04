from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from cart.models import Cart, CartItem
from orders.models import Order, OrderItem, OrderItemEvent, Payment


class OrderFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "test-session-automated"

        self.cart = Cart.objects.create(session_id=self.session_id)

        self.cart_item = CartItem.objects.create(
            cart=self.cart,
            cart_item_id="Q-DEMO-0001:P-DEMO-001:1565461",
            quote_id="Q-DEMO-0001",
            part_option_id="P-DEMO-001",
            part_number="1565461",
            name="Demo OEM Part",
            condition="New",
            brand="OEM",
            availability="Available",
            eta_days=14,
            weight_kg=Decimal("2.50"),
            final_price_gel=Decimal("650.00"),
            currency="GEL",
            note="Demo offer.",
            customer_notice="",
            weight_source="api",
            quantity=1,
        )

    def test_checkout_creates_order_payment_and_order_item_event(self):
        response = self.client.post(
            "/api/orders/checkout/",
            {
                "session_id": self.session_id,
                "customer_name": "Lado",
                "customer_phone": "599123456",
                "vin": "",
                "note": "",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        order = Order.objects.get(session_id=self.session_id)
        item = order.items.first()
        payment = order.payment

        self.assertIsNotNone(item)
        self.assertEqual(order.status, Order.STATUS_PAYMENT_PENDING)
        self.assertEqual(order.total_gel, Decimal("650.00"))

        self.assertEqual(payment.status, Payment.STATUS_PENDING)
        self.assertEqual(payment.amount_gel, Decimal("650.00"))
        self.assertEqual(payment.currency, "GEL")
        self.assertTrue(payment.payment_reference.startswith("PAY-"))

        self.assertEqual(item.item_status, OrderItem.ITEM_STATUS_CREATED)
        self.assertEqual(item.eta_days, 14)
        self.assertEqual(item.weight_kg, Decimal("2.50"))
        self.assertEqual(item.weight_source, "api")
        self.assertEqual(
            item.expected_arrival_date,
            timezone.localdate() + timedelta(days=14),
        )

        self.assertEqual(item.events.count(), 1)
        self.assertEqual(
            item.events.first().event_type,
            OrderItemEvent.EVENT_TYPE_CREATED,
        )

        self.assertEqual(CartItem.objects.filter(cart=self.cart).count(), 0)

        self.assertEqual(response.data["payment"]["status"], Payment.STATUS_PENDING)
        self.assertEqual(response.data["payment"]["currency"], "GEL")
        self.assertTrue(response.data["payment"]["payment_reference"].startswith("PAY-"))

    def test_duplicate_eta_change_is_blocked(self):
        order, item = self._create_order_from_cart()

        response = self.client.post(
            f"/api/orders/items/{item.id}/demo-request-change/",
            {
                "session_id": self.session_id,
                "action_type": OrderItem.ACTION_TYPE_ETA_CHANGE,
                "action_message": "ETA იგივეა.",
                "proposed_eta_days": 14,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "no actual changes detected")
        self.assertEqual(item.events.count(), 1)

    def test_eta_change_request_creates_action_and_event(self):
        order, item = self._create_order_from_cart()

        response = self.client.post(
            f"/api/orders/items/{item.id}/demo-request-change/",
            {
                "session_id": self.session_id,
                "action_type": OrderItem.ACTION_TYPE_ETA_CHANGE,
                "action_message": "მიტანის ვადა შეიცვალა.",
                "proposed_eta_days": 21,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        item.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(order.status, Order.STATUS_ACTION_REQUIRED)
        self.assertEqual(item.item_status, OrderItem.ITEM_STATUS_ACTION_REQUIRED)
        self.assertTrue(item.action_required)
        self.assertEqual(item.proposed_eta_days, 21)
        self.assertEqual(
            item.proposed_expected_arrival_date,
            timezone.localdate() + timedelta(days=21),
        )

        self.assertEqual(item.events.count(), 2)
        self.assertEqual(
            item.events.last().event_type,
            OrderItemEvent.EVENT_TYPE_ETA_CHANGE_REQUESTED,
        )

    def test_resolve_action_applies_proposed_eta(self):
        order, item = self._create_order_from_cart()

        self.client.post(
            f"/api/orders/items/{item.id}/demo-request-change/",
            {
                "session_id": self.session_id,
                "action_type": OrderItem.ACTION_TYPE_ETA_CHANGE,
                "action_message": "მიტანის ვადა შეიცვალა.",
                "proposed_eta_days": 21,
            },
            format="json",
        )

        response = self.client.post(
            f"/api/orders/items/{item.id}/demo-resolve-action/",
            {
                "session_id": self.session_id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        item.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(order.status, Order.STATUS_PROCESSING)
        self.assertEqual(item.item_status, OrderItem.ITEM_STATUS_CHECKING)
        self.assertFalse(item.action_required)
        self.assertEqual(item.eta_days, 21)
        self.assertEqual(
            item.expected_arrival_date,
            timezone.localdate() + timedelta(days=21),
        )
        self.assertIsNone(item.proposed_eta_days)
        self.assertIsNone(item.proposed_expected_arrival_date)

        self.assertEqual(
            item.events.last().event_type,
            OrderItemEvent.EVENT_TYPE_ACTION_RESOLVED,
        )

    def test_price_change_resolve_updates_order_total(self):
        order, item = self._create_order_from_cart()

        self.client.post(
            f"/api/orders/items/{item.id}/demo-request-change/",
            {
                "session_id": self.session_id,
                "action_type": OrderItem.ACTION_TYPE_PRICE_CHANGE,
                "action_message": "ფასი შეიცვალა.",
                "proposed_final_price_gel": "800.00",
            },
            format="json",
        )

        response = self.client.post(
            f"/api/orders/items/{item.id}/demo-resolve-action/",
            {
                "session_id": self.session_id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        item.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(item.final_price_gel, Decimal("800.00"))
        self.assertIsNone(item.proposed_final_price_gel)
        self.assertEqual(order.total_gel, Decimal("800.00"))

    def test_status_update_creates_event(self):
        order, item = self._create_order_from_cart()

        response = self.client.post(
            f"/api/orders/items/{item.id}/demo-update-status/",
            {
                "session_id": self.session_id,
                "item_status": OrderItem.ITEM_STATUS_PURCHASED,
                "message": "ნაწილი შეძენილია მომწოდებელთან.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        item.refresh_from_db()

        self.assertEqual(item.item_status, OrderItem.ITEM_STATUS_PURCHASED)
        self.assertEqual(
            item.events.last().event_type,
            OrderItemEvent.EVENT_TYPE_STATUS_CHANGED,
        )
        self.assertEqual(
            item.events.last().old_value,
            {"item_status": OrderItem.ITEM_STATUS_CREATED},
        )
        self.assertEqual(
            item.events.last().new_value,
            {"item_status": OrderItem.ITEM_STATUS_PURCHASED},
        )

    def test_demo_confirm_payment_updates_payment_order_items_and_creates_events(self):
        order, item = self._create_order_from_cart()
        payment = order.payment

        response = self.client.post(
            f"/api/orders/{order.order_number}/demo-confirm-payment/",
            {
                "session_id": self.session_id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()
        item.refresh_from_db()
        payment.refresh_from_db()

        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertIsNotNone(payment.paid_at)
        self.assertEqual(order.status, Order.STATUS_PROCESSING)
        self.assertEqual(item.item_status, OrderItem.ITEM_STATUS_PAYMENT_CONFIRMED)
        self.assertFalse(item.action_required)
        self.assertEqual(item.action_type, OrderItem.ACTION_TYPE_NONE)
        self.assertEqual(item.action_message, "")

        event = item.events.order_by("id").last()

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, OrderItemEvent.EVENT_TYPE_STATUS_CHANGED)
        self.assertEqual(event.title, "გადახდა დადასტურებულია")
        self.assertEqual(event.message, "შეკვეთის გადახდა დადასტურდა.")
        self.assertEqual(event.old_value["item_status"], OrderItem.ITEM_STATUS_CREATED)
        self.assertEqual(event.old_value["payment_status"], Payment.STATUS_PENDING)
        self.assertEqual(
            event.new_value["item_status"],
            OrderItem.ITEM_STATUS_PAYMENT_CONFIRMED,
        )
        self.assertEqual(event.new_value["payment_status"], Payment.STATUS_PAID)
        self.assertEqual(event.new_value["payment_reference"], payment.payment_reference)
        self.assertFalse(event.visible_to_customer)

        self.assertEqual(response.data["status"], Order.STATUS_PROCESSING)
        self.assertEqual(response.data["payment"]["status"], Payment.STATUS_PAID)
        self.assertEqual(
            response.data["items"][0]["item_status"],
            OrderItem.ITEM_STATUS_PAYMENT_CONFIRMED,
        )

    def test_verify_payment_marks_payment_paid_and_updates_order(self):
        order, item = self._create_order_from_cart()
        payment = order.payment

        response = self.client.post(
            f"/api/orders/{order.order_number}/verify-payment/",
            {
                "session_id": self.session_id,
                "payment_reference": payment.payment_reference,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()
        item.refresh_from_db()
        payment.refresh_from_db()

        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertIsNotNone(payment.paid_at)
        self.assertEqual(order.status, Order.STATUS_PROCESSING)
        self.assertEqual(item.item_status, OrderItem.ITEM_STATUS_PAYMENT_CONFIRMED)

        event = item.events.order_by("id").last()

        self.assertEqual(event.event_type, OrderItemEvent.EVENT_TYPE_STATUS_CHANGED)
        self.assertEqual(event.new_value["payment_status"], Payment.STATUS_PAID)
        self.assertEqual(event.new_value["payment_reference"], payment.payment_reference)

        self.assertEqual(response.data["payment"]["status"], Payment.STATUS_PAID)

    def test_verify_payment_blocks_wrong_payment_reference(self):
        order, item = self._create_order_from_cart()

        response = self.client.post(
            f"/api/orders/{order.order_number}/verify-payment/",
            {
                "session_id": self.session_id,
                "payment_reference": "PAY-WRONG",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "payment_reference does not match this order",
        )

        order.refresh_from_db()
        item.refresh_from_db()

        self.assertEqual(order.status, Order.STATUS_PAYMENT_PENDING)
        self.assertEqual(item.item_status, OrderItem.ITEM_STATUS_CREATED)
        self.assertEqual(order.payment.status, Payment.STATUS_PENDING)

    def test_verify_payment_is_idempotent_after_paid(self):
        order, item = self._create_order_from_cart()
        payment = order.payment

        first_response = self.client.post(
            f"/api/orders/{order.order_number}/verify-payment/",
            {
                "session_id": self.session_id,
                "payment_reference": payment.payment_reference,
            },
            format="json",
        )

        second_response = self.client.post(
            f"/api/orders/{order.order_number}/verify-payment/",
            {
                "session_id": self.session_id,
                "payment_reference": payment.payment_reference,
            },
            format="json",
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)

        item.refresh_from_db()
        payment.refresh_from_db()

        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertEqual(item.events.count(), 2)

    def _create_order_from_cart(self):
        response = self.client.post(
            "/api/orders/checkout/",
            {
                "session_id": self.session_id,
                "customer_name": "Lado",
                "customer_phone": "599123456",
                "vin": "",
                "note": "",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        order = Order.objects.get(session_id=self.session_id)
        item = order.items.first()

        return order, item