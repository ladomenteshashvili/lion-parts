from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from rest_framework.test import APIClient

from accounts.testing import make_customer
from orders.admin import OrderAdmin
from orders.models import Order, OrderCustomerNotification, OrderItem, OrderSupportMessage
from orders.serializers import OrderSerializer

from api.legacy import legacy_endpoint_test


def create_customer(session_id):
    return make_customer(first_name="Courier Fee", last_name="Customer", is_phone_verified=True)


def create_order(session_id):
    # Built with the ORM: the legacy checkout endpoint is disabled (LP-BE-103).
    customer = create_customer(session_id)
    order = Order.objects.create(
        order_number="LP-COURIER-0001",
        session_id=session_id,
        customer=customer,
        customer_name=customer.full_name,
        customer_phone=customer.phone,
        payment_type=Order.PAYMENT_FULL,
        status=Order.STATUS_PAYMENT_PENDING,
        courier_delivery_requested=True,
        total_gel=Decimal("150.00"),
    )
    OrderItem.objects.create(
        order=order,
        cart_item_id="courier-fee-cart-item",
        quote_id="courier-fee-quote",
        part_option_id="courier-fee-option",
        part_number="COURIER-FEE-123",
        name="Courier Fee Test Part",
        condition="New",
        brand="OEM",
        availability="Available",
        eta_days=10,
        weight_kg=Decimal("2.50"),
        final_price_gel=Decimal("150.00"),
        currency="GEL",
        quantity=1,
    )
    return order


class CourierFeeConfirmationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "courier-fee-session"

    def test_serializer_exposes_courier_fee_fields(self):
        order = create_order(self.session_id)
        order.proposed_courier_delivery_fee_gel = Decimal("12.00")
        order.courier_delivery_action_required = True
        order.courier_delivery_action_message = "დაადასტურეთ კურიერის ფასი."
        order.save()

        data = OrderSerializer(order).data

        self.assertEqual(data["proposed_courier_delivery_fee_gel"], "12.00")
        self.assertTrue(data["courier_delivery_action_required"])
        self.assertEqual(
            data["courier_delivery_action_message"],
            "დაადასტურეთ კურიერის ფასი.",
        )

    @legacy_endpoint_test("LP-BE-805")
    def test_customer_confirms_courier_fee_and_total_updates(self):
        order = create_order(self.session_id)
        order.proposed_courier_delivery_fee_gel = Decimal("12.00")
        order.courier_delivery_action_required = True
        order.courier_delivery_action_message = "დაადასტურეთ კურიერის ფასი."
        order.status = Order.STATUS_ACTION_REQUIRED
        order.save()

        response = self.client.post(
            f"/api/orders/{order.order_number}/resolve-courier-fee/",
            {"session_id": self.session_id},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()
        order.payment.refresh_from_db()

        self.assertEqual(order.courier_delivery_fee_gel, Decimal("12.00"))
        self.assertIsNone(order.proposed_courier_delivery_fee_gel)
        self.assertFalse(order.courier_delivery_action_required)
        self.assertEqual(order.total_gel, Decimal("162.00"))
        self.assertEqual(order.payment.amount_gel, Decimal("162.00"))

    @legacy_endpoint_test("LP-BE-805")
    def test_customer_declines_courier_fee_without_changing_total(self):
        order = create_order(self.session_id)
        order.proposed_courier_delivery_fee_gel = Decimal("12.00")
        order.courier_delivery_action_required = True
        order.courier_delivery_action_message = "დაადასტურეთ კურიერის ფასი."
        order.status = Order.STATUS_ACTION_REQUIRED
        order.save()

        response = self.client.post(
            f"/api/orders/{order.order_number}/decline-courier-fee/",
            {"session_id": self.session_id},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()

        self.assertEqual(order.courier_delivery_fee_gel, Decimal("0.00"))
        self.assertIsNone(order.proposed_courier_delivery_fee_gel)
        self.assertFalse(order.courier_delivery_action_required)
        self.assertEqual(order.total_gel, Decimal("150.00"))

    def test_admin_action_marks_courier_fee_action_required(self):
        order = create_order(self.session_id)
        order.proposed_courier_delivery_fee_gel = Decimal("12.00")
        order.save()

        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )

        request = RequestFactory().post("/")
        request.user = user
        setattr(request, "session", {})
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        admin_model = OrderAdmin(Order, AdminSite())
        admin_model.request_courier_fee_confirmation(
            request,
            Order.objects.filter(id=order.id),
        )

        order.refresh_from_db()

        self.assertTrue(order.courier_delivery_action_required)
        self.assertEqual(order.status, Order.STATUS_ACTION_REQUIRED)
        self.assertEqual(
            order.support_messages.filter(
                sender_type=OrderSupportMessage.SENDER_SYSTEM,
                visible_to_customer=True,
            ).count(),
            1,
        )

        notification = OrderCustomerNotification.objects.get(order=order)

        self.assertEqual(
            notification.notification_type,
            OrderCustomerNotification.TYPE_ACTION_REQUIRED,
        )
        self.assertEqual(notification.title, "კურიერის ფასის დადასტურება")
        self.assertTrue(notification.visible_to_customer)
        self.assertFalse(notification.is_read_by_customer)
        self.assertIsNotNone(notification.token)
        self.assertIn("12.00", notification.message)
