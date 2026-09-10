from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Customer, CustomerSession
from orders.models import (
    Order,
    OrderCustomerNotification,
    OrderItem,
    OrderItemEvent,
    OrderSupportMessage,
    Payment,
)


def create_verified_customer(session_id="order-action-session"):
    customer = Customer.objects.create(
        session_id=session_id,
        name="Order Regression Customer",
        phone="555123456",
        is_phone_verified=True,
    )

    CustomerSession.objects.create(
        customer=customer,
        session_id=session_id,
    )

    return customer


def create_order_with_item(customer, session_id="order-action-session"):
    order = Order.objects.create(
        order_number="LP-ACTION-0001",
        session_id=session_id,
        customer=customer,
        customer_name=customer.name,
        customer_phone=customer.phone,
        vin="ACTIONVIN12345678",
        note="Order action regression test",
        payment_type=Order.PAYMENT_FULL,
        status=Order.STATUS_PROCESSING,
        total_gel=Decimal("120.00"),
    )

    Payment.objects.create(
        order=order,
        payment_reference="PAY-ACTION-0001",
        provider=Payment.PROVIDER_DEMO,
        status=Payment.STATUS_PAID,
        amount_gel=Decimal("120.00"),
        currency="GEL",
    )

    item = OrderItem.objects.create(
        order=order,
        cart_item_id="action-cart-item-1",
        quote_id="action-quote-1",
        part_option_id="action-option-1",
        part_number="ACTION123",
        name="Action Regression Part",
        condition="New",
        brand="OEM",
        availability="Available",
        eta_days=14,
        weight_kg=Decimal("1.50"),
        final_price_gel=Decimal("120.00"),
        currency="GEL",
        quantity=1,
        item_status=OrderItem.ITEM_STATUS_PAYMENT_CONFIRMED,
    )

    return order, item


class OrderActionRegressionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "order-action-session"
        self.customer = create_verified_customer(self.session_id)
        self.order, self.item = create_order_with_item(
            self.customer,
            self.session_id,
        )

    def make_price_change_action(self):
        self.item.action_required = True
        self.item.action_type = OrderItem.ACTION_TYPE_PRICE_CHANGE
        self.item.action_message = "Price increased after supplier confirmation."
        self.item.item_status = OrderItem.ITEM_STATUS_ACTION_REQUIRED
        self.item.proposed_final_price_gel = Decimal("150.00")
        self.item.save()

        self.order.status = Order.STATUS_ACTION_REQUIRED
        self.order.save(update_fields=["status", "updated_at"])

    def test_customer_can_confirm_price_change_action(self):
        self.make_price_change_action()

        response = self.client.post(
            f"/api/orders/items/{self.item.id}/resolve-action/",
            {
                "session_id": self.session_id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        self.item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(self.item.final_price_gel, Decimal("150.00"))
        self.assertIsNone(self.item.proposed_final_price_gel)
        self.assertFalse(self.item.action_required)
        self.assertEqual(self.item.action_type, OrderItem.ACTION_TYPE_NONE)
        self.assertEqual(self.item.item_status, OrderItem.ITEM_STATUS_CHECKING)
        self.assertEqual(self.order.total_gel, Decimal("150.00"))
        self.assertEqual(self.order.status, Order.STATUS_PROCESSING)

        self.assertTrue(
            OrderItemEvent.objects.filter(
                item=self.item,
                event_type=OrderItemEvent.EVENT_TYPE_ACTION_RESOLVED,
                actor_type=OrderItemEvent.ACTOR_TYPE_CUSTOMER,
                visible_to_customer=True,
            ).exists()
        )

        self.assertEqual(response.data["total_gel"], "150.00")
        self.assertEqual(response.data["status"], Order.STATUS_PROCESSING)

    def test_customer_can_cancel_price_change_action(self):
        self.make_price_change_action()

        response = self.client.post(
            f"/api/orders/items/{self.item.id}/cancel-action/",
            {
                "session_id": self.session_id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        self.item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(self.item.item_status, OrderItem.ITEM_STATUS_CANCELLED)
        self.assertFalse(self.item.action_required)
        self.assertEqual(self.item.action_type, OrderItem.ACTION_TYPE_NONE)
        self.assertIsNone(self.item.proposed_final_price_gel)
        self.assertEqual(self.order.total_gel, Decimal("0.00"))
        self.assertEqual(self.order.status, Order.STATUS_CANCELLED)

        self.assertTrue(
            OrderItemEvent.objects.filter(
                item=self.item,
                title="ნაწილი გაუქმებულია",
                actor_type=OrderItemEvent.ACTOR_TYPE_CUSTOMER,
                visible_to_customer=True,
            ).exists()
        )

        self.assertEqual(response.data["total_gel"], "0.00")
        self.assertEqual(response.data["status"], Order.STATUS_CANCELLED)

    def test_notice_only_weight_action_cannot_be_cancelled_but_can_be_acknowledged(self):
        self.item.action_required = True
        self.item.action_type = OrderItem.ACTION_TYPE_WEIGHT_CHANGE
        self.item.action_message = "Weight changed after purchase."
        self.item.item_status = OrderItem.ITEM_STATUS_PURCHASED
        self.item.proposed_final_price_gel = None
        self.item.proposed_eta_days = None
        self.item.proposed_expected_arrival_date = None
        self.item.save()

        self.order.status = Order.STATUS_ACTION_REQUIRED
        self.order.save(update_fields=["status", "updated_at"])

        cancel_response = self.client.post(
            f"/api/orders/items/{self.item.id}/cancel-action/",
            {
                "session_id": self.session_id,
            },
            format="json",
        )

        self.assertEqual(cancel_response.status_code, 400)
        self.assertEqual(cancel_response.data["detail"], "item action is notice only")

        acknowledge_response = self.client.post(
            f"/api/orders/items/{self.item.id}/acknowledge-action/",
            {
                "session_id": self.session_id,
            },
            format="json",
        )

        self.assertEqual(acknowledge_response.status_code, 200)

        self.item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(self.item.item_status, OrderItem.ITEM_STATUS_PURCHASED)
        self.assertFalse(self.item.action_required)
        self.assertEqual(self.item.action_type, OrderItem.ACTION_TYPE_NONE)
        self.assertEqual(self.order.status, Order.STATUS_PROCESSING)

        self.assertTrue(
            OrderItemEvent.objects.filter(
                item=self.item,
                title="შეტყობინება ნანახია",
                actor_type=OrderItemEvent.ACTOR_TYPE_CUSTOMER,
                visible_to_customer=True,
            ).exists()
        )


class OrderSupportAndNotificationRegressionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "support-session"
        self.customer = create_verified_customer(self.session_id)
        self.order, self.item = create_order_with_item(
            self.customer,
            self.session_id,
        )

    def test_customer_support_message_is_created_and_visible_on_order(self):
        response = self.client.post(
            f"/api/orders/{self.order.order_number}/support/messages/",
            {
                "session_id": self.session_id,
                "item_id": self.item.id,
                "message": "Please check this part before ordering.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        message = OrderSupportMessage.objects.get(order=self.order)

        self.assertEqual(message.sender_type, OrderSupportMessage.SENDER_CUSTOMER)
        self.assertEqual(message.sender_name, self.order.customer_name)
        self.assertEqual(message.item_id, self.item.id)
        self.assertEqual(message.message, "Please check this part before ordering.")
        self.assertTrue(message.visible_to_customer)
        self.assertTrue(message.is_read_by_customer)
        self.assertFalse(message.is_read_by_operator)

        self.assertEqual(len(response.data["support_messages"]), 1)
        self.assertEqual(
            response.data["support_messages"][0]["message"],
            "Please check this part before ordering.",
        )

    def test_customer_can_acknowledge_operator_support_messages(self):
        operator_message = OrderSupportMessage.objects.create(
            order=self.order,
            item=self.item,
            sender_type=OrderSupportMessage.SENDER_OPERATOR,
            sender_name="Operator",
            message="Operator reply for customer.",
            visible_to_customer=True,
            is_read_by_customer=False,
            is_read_by_operator=True,
        )

        detail_response = self.client.get(
            f"/api/orders/{self.order.order_number}/?session_id={self.session_id}",
        )

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["support_unread_count"], 1)

        acknowledge_response = self.client.post(
            f"/api/orders/{self.order.order_number}/support/acknowledge/",
            {
                "session_id": self.session_id,
            },
            format="json",
        )

        self.assertEqual(acknowledge_response.status_code, 200)
        self.assertEqual(acknowledge_response.data["support_unread_count"], 0)

        operator_message.refresh_from_db()

        self.assertTrue(operator_message.is_read_by_customer)

    def test_public_notification_acknowledge_marks_notification_and_support_message_read(self):
        operator_message = OrderSupportMessage.objects.create(
            order=self.order,
            item=self.item,
            sender_type=OrderSupportMessage.SENDER_OPERATOR,
            sender_name="Operator",
            message="Public notification support reply.",
            visible_to_customer=True,
            is_read_by_customer=False,
            is_read_by_operator=True,
        )

        notification = OrderCustomerNotification.objects.create(
            order=self.order,
            item=self.item,
            support_message=operator_message,
            notification_type=OrderCustomerNotification.TYPE_SUPPORT_REPLY,
            title="Support reply",
            message="Operator replied to your order.",
            visible_to_customer=True,
            is_read_by_customer=False,
        )

        detail_response = self.client.get(
            f"/api/orders/public/notifications/{notification.token}/",
        )

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["title"], "Support reply")
        self.assertEqual(detail_response.data["is_read_by_customer"], False)
        self.assertEqual(
            detail_response.data["order"]["order_number"],
            self.order.order_number,
        )

        acknowledge_response = self.client.post(
            f"/api/orders/public/notifications/{notification.token}/acknowledge/",
            {},
            format="json",
        )

        self.assertEqual(acknowledge_response.status_code, 200)
        self.assertEqual(acknowledge_response.data["is_read_by_customer"], True)
        self.assertIsNotNone(acknowledge_response.data["acknowledged_at"])

        notification.refresh_from_db()
        operator_message.refresh_from_db()

        self.assertTrue(notification.is_read_by_customer)
        self.assertIsNotNone(notification.acknowledged_at)
        self.assertTrue(operator_message.is_read_by_customer)
