from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from cart.models import Cart, CartItem
from orders.models import Order, OrderItem, OrderItemEvent, OrderSupportMessage, Payment
from accounts.models import Customer
from orders.admin import request_order_item_action_from_admin, set_order_item_status_from_admin


@override_settings(ENABLE_DEMO_ORDER_ENDPOINTS=True)
class OrderFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "test-session-automated"
        self.customer = Customer.objects.create(
            session_id=self.session_id,
            name="Lado",
            phone="599123456",
            is_phone_verified=True,
        )
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



    def test_checkout_requires_verified_phone(self):
        self.customer.is_phone_verified = False
        self.customer.save(update_fields=["is_phone_verified"])

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

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["detail"], "phone verification required")
        self.assertEqual(Order.objects.count(), 0)

    def test_checkout_requires_phone_to_match_verified_profile(self):
        response = self.client.post(
            "/api/orders/checkout/",
            {
                "session_id": self.session_id,
                "customer_name": "Lado",
                "customer_phone": "599000000",
                "vin": "",
                "note": "",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "checkout phone must match verified phone",
        )
        self.assertEqual(Order.objects.count(), 0)


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
            f"/api/orders/items/{item.id}/resolve-action/",
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
            f"/api/orders/items/{item.id}/resolve-action/",
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
        self.assertTrue(event.visible_to_customer)

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

    def test_order_detail_allows_same_verified_phone_from_new_session(self):
        order, _item = self._create_order_from_cart()
        other_session_id = "same-phone-new-session"

        Customer.objects.create(
            session_id=other_session_id,
            name="Lado",
            phone="599123456",
            is_phone_verified=True,
        )

        response = self.client.get(
            f"/api/orders/{order.order_number}/?session_id={other_session_id}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["order_number"], order.order_number)

    def test_order_detail_blocks_different_verified_phone(self):
        order, _item = self._create_order_from_cart()
        other_session_id = "different-phone-session"

        Customer.objects.create(
            session_id=other_session_id,
            name="Other",
            phone="599000000",
            is_phone_verified=True,
        )

        response = self.client.get(
            f"/api/orders/{order.order_number}/?session_id={other_session_id}",
        )

        self.assertEqual(response.status_code, 404)

    def test_order_detail_returns_only_customer_visible_events(self):
        order, item = self._create_order_from_cart()

        OrderItemEvent.objects.create(
            item=item,
            event_type=OrderItemEvent.EVENT_TYPE_NOTE_ADDED,
            title="Internal note",
            message="This must not be visible to customer.",
            actor_type=OrderItemEvent.ACTOR_TYPE_SYSTEM,
            actor_name="System",
            visible_to_customer=False,
        )

        OrderItemEvent.objects.create(
            item=item,
            event_type=OrderItemEvent.EVENT_TYPE_NOTE_ADDED,
            title="Customer visible note",
            message="This can be visible to customer.",
            actor_type=OrderItemEvent.ACTOR_TYPE_SYSTEM,
            actor_name="System",
            visible_to_customer=True,
        )

        response = self.client.get(
            f"/api/orders/{order.order_number}/?session_id={self.session_id}",
        )

        self.assertEqual(response.status_code, 200)

        event_titles = [
            event["title"]
            for event in response.data["items"][0]["events"]
        ]

        self.assertIn("Customer visible note", event_titles)
        self.assertNotIn("Internal note", event_titles)



    def test_customer_can_cancel_action_required_item(self):
        order, item = self._create_order_from_cart()

        payment = order.payment
        payment.status = Payment.STATUS_PAID
        payment.paid_at = timezone.now()
        payment.save(update_fields=["status", "paid_at", "updated_at"])

        order.status = Order.STATUS_ACTION_REQUIRED
        order.save(update_fields=["status", "updated_at"])

        item.item_status = OrderItem.ITEM_STATUS_ACTION_REQUIRED
        item.action_required = True
        item.action_type = OrderItem.ACTION_TYPE_PRICE_CHANGE
        item.action_message = "ფასი შეიცვალა."
        item.proposed_final_price_gel = Decimal("800.00")
        item.save()

        response = self.client.post(
            f"/api/orders/items/{item.id}/cancel-action/",
            {
                "session_id": self.session_id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        item.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(item.item_status, OrderItem.ITEM_STATUS_CANCELLED)
        self.assertFalse(item.action_required)
        self.assertEqual(item.action_type, OrderItem.ACTION_TYPE_NONE)
        self.assertEqual(item.action_message, "")
        self.assertIsNone(item.proposed_final_price_gel)
        self.assertEqual(order.status, Order.STATUS_CANCELLED)
        self.assertEqual(order.total_gel, Decimal("0.00"))

        event = item.events.last()

        self.assertEqual(event.event_type, OrderItemEvent.EVENT_TYPE_ACTION_RESOLVED)
        self.assertEqual(event.title, "ნაწილი გაუქმებულია")
        self.assertEqual(event.actor_type, OrderItemEvent.ACTOR_TYPE_CUSTOMER)
        self.assertTrue(event.visible_to_customer)

        self.assertEqual(response.data["status"], Order.STATUS_CANCELLED)
        self.assertEqual(response.data["total_gel"], "0.00")
        self.assertEqual(
            response.data["items"][0]["item_status"],
            OrderItem.ITEM_STATUS_CANCELLED,
        )

    def test_cancel_action_requires_pending_action(self):
        order, item = self._create_order_from_cart()

        payment = order.payment
        payment.status = Payment.STATUS_PAID
        payment.paid_at = timezone.now()
        payment.save(update_fields=["status", "paid_at", "updated_at"])

        order.status = Order.STATUS_PROCESSING
        order.save(update_fields=["status", "updated_at"])

        item.item_status = OrderItem.ITEM_STATUS_PAYMENT_CONFIRMED
        item.action_required = False
        item.save(update_fields=["item_status", "action_required", "updated_at"])

        response = self.client.post(
            f"/api/orders/items/{item.id}/cancel-action/",
            {
                "session_id": self.session_id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "item has no pending action")

        item.refresh_from_db()
        self.assertEqual(item.item_status, OrderItem.ITEM_STATUS_PAYMENT_CONFIRMED)

    def test_cancel_action_blocks_different_verified_phone(self):
        order, item = self._create_order_from_cart()

        Customer.objects.create(
            session_id="different-phone-cancel-session",
            name="Other",
            phone="599000000",
            is_phone_verified=True,
        )

        order.status = Order.STATUS_ACTION_REQUIRED
        order.save(update_fields=["status", "updated_at"])

        item.item_status = OrderItem.ITEM_STATUS_ACTION_REQUIRED
        item.action_required = True
        item.action_type = OrderItem.ACTION_TYPE_PRICE_CHANGE
        item.action_message = "ფასი შეიცვალა."
        item.proposed_final_price_gel = Decimal("800.00")
        item.save()

        response = self.client.post(
            f"/api/orders/items/{item.id}/cancel-action/",
            {
                "session_id": "different-phone-cancel-session",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 404)

        item.refresh_from_db()
        self.assertTrue(item.action_required)
        self.assertEqual(item.item_status, OrderItem.ITEM_STATUS_ACTION_REQUIRED)


    def test_customer_can_acknowledge_weight_change_notice(self):
        order, item = self._create_order_from_cart()

        payment = order.payment
        payment.status = Payment.STATUS_PAID
        payment.paid_at = timezone.now()
        payment.save(update_fields=["status", "paid_at", "updated_at"])

        order.status = Order.STATUS_ACTION_REQUIRED
        order.total_gel = Decimal("800.00")
        order.save(update_fields=["status", "total_gel", "updated_at"])

        item.item_status = OrderItem.ITEM_STATUS_PURCHASED
        item.action_required = True
        item.action_type = OrderItem.ACTION_TYPE_WEIGHT_CHANGE
        item.action_message = "წონის გამო ფასი დაკორექტირდა."
        item.final_price_gel = Decimal("800.00")
        item.weight_source = "manual"
        item.proposed_final_price_gel = None
        item.save()

        response = self.client.post(
            f"/api/orders/items/{item.id}/acknowledge-action/",
            {
                "session_id": self.session_id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        item.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(item.item_status, OrderItem.ITEM_STATUS_PURCHASED)
        self.assertFalse(item.action_required)
        self.assertEqual(item.action_type, OrderItem.ACTION_TYPE_NONE)
        self.assertEqual(item.final_price_gel, Decimal("800.00"))
        self.assertEqual(order.status, Order.STATUS_PROCESSING)
        self.assertEqual(order.total_gel, Decimal("800.00"))

        event = item.events.last()

        self.assertEqual(event.title, "შეტყობინება ნანახია")
        self.assertEqual(event.actor_type, OrderItemEvent.ACTOR_TYPE_CUSTOMER)
        self.assertTrue(event.visible_to_customer)

    def test_notice_only_weight_action_cannot_be_cancelled_by_customer(self):
        order, item = self._create_order_from_cart()

        payment = order.payment
        payment.status = Payment.STATUS_PAID
        payment.paid_at = timezone.now()
        payment.save(update_fields=["status", "paid_at", "updated_at"])

        order.status = Order.STATUS_ACTION_REQUIRED
        order.save(update_fields=["status", "updated_at"])

        item.item_status = OrderItem.ITEM_STATUS_PURCHASED
        item.action_required = True
        item.action_type = OrderItem.ACTION_TYPE_WEIGHT_CHANGE
        item.action_message = "წონის გამო ფასი დაკორექტირდა."
        item.weight_source = "manual"
        item.proposed_final_price_gel = None
        item.save()

        response = self.client.post(
            f"/api/orders/items/{item.id}/cancel-action/",
            {
                "session_id": self.session_id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "item action is notice only")

        item.refresh_from_db()
        self.assertEqual(item.item_status, OrderItem.ITEM_STATUS_PURCHASED)
        self.assertTrue(item.action_required)



    def test_customer_can_send_order_support_message(self):
        order, item = self._create_order_from_cart()

        response = self.client.post(
            f"/api/orders/{order.order_number}/support/messages/",
            {
                "session_id": self.session_id,
                "item_id": item.id,
                "message": "გთხოვთ დამიკონკრეტოთ ETA.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        message = OrderSupportMessage.objects.get(order=order)

        self.assertEqual(message.item, item)
        self.assertEqual(message.sender_type, OrderSupportMessage.SENDER_CUSTOMER)
        self.assertEqual(message.sender_name, "Lado")
        self.assertEqual(message.message, "გთხოვთ დამიკონკრეტოთ ETA.")
        self.assertTrue(message.visible_to_customer)
        self.assertTrue(message.is_read_by_customer)
        self.assertFalse(message.is_read_by_operator)

        self.assertEqual(len(response.data["support_messages"]), 1)
        self.assertEqual(response.data["support_messages"][0]["message"], message.message)
        self.assertEqual(response.data["support_unread_count"], 0)

    def test_support_message_blocks_different_verified_phone(self):
        order, item = self._create_order_from_cart()

        Customer.objects.create(
            session_id="support-other-phone",
            name="Other",
            phone="599000000",
            is_phone_verified=True,
        )

        response = self.client.post(
            f"/api/orders/{order.order_number}/support/messages/",
            {
                "session_id": "support-other-phone",
                "item_id": item.id,
                "message": " чужой заказ ",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(OrderSupportMessage.objects.count(), 0)

    def test_customer_can_acknowledge_operator_support_reply(self):
        order, item = self._create_order_from_cart()

        OrderSupportMessage.objects.create(
            order=order,
            item=item,
            sender_type=OrderSupportMessage.SENDER_OPERATOR,
            sender_name="Operator",
            message="ETA დაზუსტებულია.",
            visible_to_customer=True,
            is_read_by_customer=False,
            is_read_by_operator=True,
        )

        detail_response = self.client.get(
            f"/api/orders/{order.order_number}/?session_id={self.session_id}",
        )

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["support_unread_count"], 1)

        response = self.client.post(
            f"/api/orders/{order.order_number}/support/acknowledge/",
            {
                "session_id": self.session_id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        message = OrderSupportMessage.objects.get(order=order)
        message.refresh_from_db()

        self.assertTrue(message.is_read_by_customer)
        self.assertEqual(response.data["support_unread_count"], 0)


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


class AdminOrderItemStatusTests(TestCase):
    def setUp(self):
        self.order = Order.objects.create(
            order_number="LP-ADMIN-0001",
            session_id="admin-session",
            customer_name="Admin Customer",
            customer_phone="599123456",
            vin="",
            note="",
            payment_type=Order.PAYMENT_FULL,
            status=Order.STATUS_PROCESSING,
            total_gel=Decimal("650.00"),
        )

        self.payment = Payment.objects.create(
            order=self.order,
            payment_reference="PAY-ADMIN-0001",
            provider=Payment.PROVIDER_DEMO,
            status=Payment.STATUS_PAID,
            amount_gel=Decimal("650.00"),
            currency="GEL",
            paid_at=timezone.now(),
        )

        self.item = OrderItem.objects.create(
            order=self.order,
            cart_item_id="admin-cart-item",
            quote_id="admin-quote",
            part_option_id="admin-option",
            part_number="ADMIN123",
            name="Admin Test Part",
            condition="New",
            brand="OEM",
            availability="Available",
            eta_days=14,
            expected_arrival_date=timezone.localdate() + timedelta(days=14),
            weight_kg=Decimal("2.50"),
            final_price_gel=Decimal("650.00"),
            currency="GEL",
            note="",
            customer_notice="",
            weight_source="api",
            quantity=1,
            item_status=OrderItem.ITEM_STATUS_PAYMENT_CONFIRMED,
            action_required=False,
            action_type=OrderItem.ACTION_TYPE_NONE,
            action_message="",
        )

    def test_admin_status_update_creates_customer_visible_event(self):
        result = set_order_item_status_from_admin(
            self.item,
            OrderItem.ITEM_STATUS_PURCHASED,
            actor_name="operator@example.com",
        )

        self.assertEqual(result, "updated")

        self.item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(self.item.item_status, OrderItem.ITEM_STATUS_PURCHASED)
        self.assertEqual(self.order.status, Order.STATUS_PROCESSING)

        event = self.item.events.last()

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, OrderItemEvent.EVENT_TYPE_STATUS_CHANGED)
        self.assertEqual(event.title, "ნაწილი შეძენილია")
        self.assertEqual(event.actor_type, OrderItemEvent.ACTOR_TYPE_ADMIN)
        self.assertEqual(event.actor_name, "operator@example.com")
        self.assertTrue(event.visible_to_customer)
        self.assertEqual(
            event.old_value["item_status"],
            OrderItem.ITEM_STATUS_PAYMENT_CONFIRMED,
        )
        self.assertEqual(
            event.new_value["item_status"],
            OrderItem.ITEM_STATUS_PURCHASED,
        )

    def test_admin_completed_status_completes_order_when_all_items_completed(self):
        result = set_order_item_status_from_admin(
            self.item,
            OrderItem.ITEM_STATUS_COMPLETED,
            actor_name="Admin",
        )

        self.assertEqual(result, "updated")

        self.item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(self.item.item_status, OrderItem.ITEM_STATUS_COMPLETED)
        self.assertEqual(self.order.status, Order.STATUS_COMPLETED)

    def test_admin_status_update_skips_payment_pending_order(self):
        self.order.status = Order.STATUS_PAYMENT_PENDING
        self.order.save(update_fields=["status", "updated_at"])

        self.payment.status = Payment.STATUS_PENDING
        self.payment.paid_at = None
        self.payment.save(update_fields=["status", "paid_at", "updated_at"])

        self.item.item_status = OrderItem.ITEM_STATUS_CREATED
        self.item.save(update_fields=["item_status", "updated_at"])

        result = set_order_item_status_from_admin(
            self.item,
            OrderItem.ITEM_STATUS_PURCHASED,
            actor_name="Admin",
        )

        self.assertEqual(result, "skipped_payment_pending")

        self.item.refresh_from_db()
        self.assertEqual(self.item.item_status, OrderItem.ITEM_STATUS_CREATED)
        self.assertEqual(self.item.events.count(), 0)



    def test_admin_price_change_request_creates_customer_action(self):
        self.item.proposed_final_price_gel = Decimal("800.00")
        self.item.action_message = "მომწოდებელთან ფასი გაიზარდა."
        self.item.save(
            update_fields=[
                "proposed_final_price_gel",
                "action_message",
                "updated_at",
            ]
        )

        result = request_order_item_action_from_admin(
            self.item,
            OrderItem.ACTION_TYPE_PRICE_CHANGE,
            actor_name="operator@example.com",
        )

        self.assertEqual(result, "updated")

        self.item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(self.order.status, Order.STATUS_ACTION_REQUIRED)
        self.assertEqual(self.item.item_status, OrderItem.ITEM_STATUS_ACTION_REQUIRED)
        self.assertTrue(self.item.action_required)
        self.assertEqual(self.item.action_type, OrderItem.ACTION_TYPE_PRICE_CHANGE)
        self.assertEqual(self.item.proposed_final_price_gel, Decimal("800.00"))

        event = self.item.events.last()

        self.assertEqual(
            event.event_type,
            OrderItemEvent.EVENT_TYPE_PRICE_CHANGE_REQUESTED,
        )
        self.assertEqual(event.title, "ფასის ცვლილების დადასტურება საჭიროა")
        self.assertTrue(event.visible_to_customer)
        self.assertEqual(event.actor_type, OrderItemEvent.ACTOR_TYPE_ADMIN)
        self.assertEqual(event.actor_name, "operator@example.com")
        self.assertEqual(
            event.new_value["proposed_final_price_gel"],
            "800.00",
        )

    def test_admin_eta_change_request_creates_expected_arrival_date(self):
        self.item.proposed_eta_days = 21
        self.item.action_message = "მომწოდებელმა მიწოდების ვადა შეცვალა."
        self.item.save(
            update_fields=[
                "proposed_eta_days",
                "action_message",
                "updated_at",
            ]
        )

        result = request_order_item_action_from_admin(
            self.item,
            OrderItem.ACTION_TYPE_ETA_CHANGE,
            actor_name="Admin",
        )

        self.assertEqual(result, "updated")

        self.item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(self.order.status, Order.STATUS_ACTION_REQUIRED)
        self.assertEqual(self.item.item_status, OrderItem.ITEM_STATUS_ACTION_REQUIRED)
        self.assertTrue(self.item.action_required)
        self.assertEqual(self.item.action_type, OrderItem.ACTION_TYPE_ETA_CHANGE)
        self.assertEqual(self.item.proposed_eta_days, 21)
        self.assertEqual(
            self.item.proposed_expected_arrival_date,
            timezone.localdate() + timedelta(days=21),
        )

        event = self.item.events.last()

        self.assertEqual(
            event.event_type,
            OrderItemEvent.EVENT_TYPE_ETA_CHANGE_REQUESTED,
        )
        self.assertEqual(event.title, "მიწოდების ვადის დადასტურება საჭიროა")
        self.assertTrue(event.visible_to_customer)

    def test_admin_fitment_issue_request_can_use_message_only(self):
        self.item.action_message = "VIN-ით თავსებადობა დასაზუსტებელია."
        self.item.save(update_fields=["action_message", "updated_at"])

        result = request_order_item_action_from_admin(
            self.item,
            OrderItem.ACTION_TYPE_FITMENT_ISSUE,
            actor_name="Admin",
        )

        self.assertEqual(result, "updated")

        self.item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(self.order.status, Order.STATUS_ACTION_REQUIRED)
        self.assertTrue(self.item.action_required)
        self.assertEqual(self.item.action_type, OrderItem.ACTION_TYPE_FITMENT_ISSUE)
        self.assertEqual(self.item.action_message, "VIN-ით თავსებადობა დასაზუსტებელია.")

        event = self.item.events.last()

        self.assertEqual(event.event_type, OrderItemEvent.EVENT_TYPE_CHANGE_REQUESTED)
        self.assertEqual(event.title, "თავსებადობის შემოწმება საჭიროა")
        self.assertTrue(event.visible_to_customer)

    def test_admin_price_change_request_requires_proposed_price(self):
        result = request_order_item_action_from_admin(
            self.item,
            OrderItem.ACTION_TYPE_PRICE_CHANGE,
            actor_name="Admin",
        )

        self.assertEqual(result, "missing_proposed_price")

        self.item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertFalse(self.item.action_required)
        self.assertEqual(self.order.status, Order.STATUS_PROCESSING)
        self.assertEqual(self.item.events.count(), 0)

    def test_admin_eta_change_request_requires_actual_change(self):
        self.item.proposed_eta_days = 14
        self.item.save(update_fields=["proposed_eta_days", "updated_at"])

        result = request_order_item_action_from_admin(
            self.item,
            OrderItem.ACTION_TYPE_ETA_CHANGE,
            actor_name="Admin",
        )

        self.assertEqual(result, "no_actual_change")

        self.item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertFalse(self.item.action_required)
        self.assertEqual(self.order.status, Order.STATUS_PROCESSING)
        self.assertEqual(self.item.events.count(), 0)


    def test_admin_alternative_part_request_can_include_price_and_eta(self):
        self.item.proposed_part_number = "ALT123"
        self.item.proposed_name = "Alternative Test Part"
        self.item.proposed_final_price_gel = Decimal("800.00")
        self.item.proposed_eta_days = 21
        self.item.action_message = "ძველი ნაწილი აღარ არის ხელმისაწვდომი."
        self.item.save()

        result = request_order_item_action_from_admin(
            self.item,
            OrderItem.ACTION_TYPE_ALTERNATIVE_REQUIRED,
            actor_name="operator@example.com",
        )

        self.assertEqual(result, "updated")

        self.item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(self.order.status, Order.STATUS_ACTION_REQUIRED)
        self.assertEqual(self.item.item_status, OrderItem.ITEM_STATUS_ACTION_REQUIRED)
        self.assertTrue(self.item.action_required)
        self.assertEqual(
            self.item.action_type,
            OrderItem.ACTION_TYPE_ALTERNATIVE_REQUIRED,
        )
        self.assertEqual(self.item.proposed_part_number, "ALT123")
        self.assertEqual(self.item.proposed_name, "Alternative Test Part")
        self.assertEqual(self.item.proposed_final_price_gel, Decimal("800.00"))
        self.assertEqual(self.item.proposed_eta_days, 21)
        self.assertEqual(
            self.item.proposed_expected_arrival_date,
            timezone.localdate() + timedelta(days=21),
        )

        event = self.item.events.last()

        self.assertEqual(event.title, "ალტერნატიული ნაწილი შემოთავაზებულია")
        self.assertEqual(event.new_value["proposed_part_number"], "ALT123")
        self.assertTrue(event.visible_to_customer)

    def test_admin_alternative_part_request_requires_part_number(self):
        result = request_order_item_action_from_admin(
            self.item,
            OrderItem.ACTION_TYPE_ALTERNATIVE_REQUIRED,
            actor_name="Admin",
        )

        self.assertEqual(result, "missing_proposed_part_number")

        self.item.refresh_from_db()
        self.assertFalse(self.item.action_required)
        self.assertEqual(self.item.events.count(), 0)

    def test_admin_weight_change_after_purchase_is_notice_only(self):
        self.item.item_status = OrderItem.ITEM_STATUS_PURCHASED
        self.item.weight_source = "manual"
        self.item.proposed_final_price_gel = Decimal("800.00")
        self.item.action_message = "რეალური წონა მეტი აღმოჩნდა."
        self.item.save()

        result = request_order_item_action_from_admin(
            self.item,
            OrderItem.ACTION_TYPE_WEIGHT_CHANGE,
            actor_name="operator@example.com",
        )

        self.assertEqual(result, "updated")

        self.item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(self.item.item_status, OrderItem.ITEM_STATUS_PURCHASED)
        self.assertTrue(self.item.action_required)
        self.assertEqual(self.item.action_type, OrderItem.ACTION_TYPE_WEIGHT_CHANGE)
        self.assertEqual(self.item.final_price_gel, Decimal("800.00"))
        self.assertIsNone(self.item.proposed_final_price_gel)
        self.assertEqual(self.order.total_gel, Decimal("800.00"))
        self.assertEqual(self.order.status, Order.STATUS_ACTION_REQUIRED)

        event = self.item.events.last()

        self.assertEqual(event.title, "წონის/ზომის გამო ფასი შეიცვალა")
        self.assertTrue(event.visible_to_customer)
        self.assertEqual(event.new_value["final_price_gel"], "800.00")


class DisabledDemoEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_demo_payment_endpoint_is_disabled_by_default(self):
        response = self.client.post(
            "/api/orders/LP-TEST/demo-confirm-payment/",
            {"session_id": "test-session"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)

    def test_verify_payment_endpoint_is_disabled_by_default(self):
        response = self.client.post(
            "/api/orders/LP-TEST/verify-payment/",
            {
                "session_id": "test-session",
                "payment_reference": "PAY-TEST",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 404)

    def test_demo_operator_endpoints_are_disabled_by_default(self):
        request_change_response = self.client.post(
            "/api/orders/items/999/demo-request-change/",
            {
                "session_id": "test-session",
                "action_type": OrderItem.ACTION_TYPE_ETA_CHANGE,
                "action_message": "test",
                "proposed_eta_days": 21,
            },
            format="json",
        )

        update_status_response = self.client.post(
            "/api/orders/items/999/demo-update-status/",
            {
                "session_id": "test-session",
                "item_status": OrderItem.ITEM_STATUS_PURCHASED,
            },
            format="json",
        )

        self.assertEqual(request_change_response.status_code, 404)
        self.assertEqual(update_status_response.status_code, 404)

