from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import (
    Customer,
    CustomerSession,
    CustomerTariff,
    PhoneVerificationCode,
)
from orders.models import (
    Order,
    OrderCustomerNotification,
    OrderItem,
    OrderItemEvent,
    OrderSupportMessage,
    Payment,
)


def create_order_for_customer(customer, session_id="main-session"):
    order = Order.objects.create(
        order_number="LP-REG-0001",
        session_id=session_id,
        customer=customer,
        customer_name=customer.name,
        customer_phone=customer.phone,
        vin="REGVIN123456789",
        note="Regression test order",
        payment_type=Order.PAYMENT_FULL,
        status=Order.STATUS_PAYMENT_PENDING,
        total_gel=Decimal("120.00"),
    )

    Payment.objects.create(
        order=order,
        payment_reference="PAY-REG-0001",
        provider=Payment.PROVIDER_DEMO,
        status=Payment.STATUS_PENDING,
        amount_gel=Decimal("120.00"),
        currency="GEL",
    )

    OrderItem.objects.create(
        order=order,
        cart_item_id="reg-cart-item-1",
        quote_id="reg-quote-1",
        part_option_id="reg-option-1",
        part_number="REG123",
        name="Regression Test Part",
        condition="New",
        brand="OEM",
        availability="Available",
        eta_days=14,
        weight_kg=Decimal("1.50"),
        final_price_gel=Decimal("120.00"),
        currency="GEL",
        quantity=1,
    )

    return order


class CustomerAccountOrderRegressionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "main-session"
        self.second_session_id = "second-browser-session"

        self.customer = Customer.objects.create(
            session_id=self.session_id,
            name="Regression Customer",
            phone="555123456",
            is_phone_verified=True,
        )

        CustomerSession.objects.create(
            customer=self.customer,
            session_id=self.session_id,
        )

        CustomerSession.objects.create(
            customer=self.customer,
            session_id=self.second_session_id,
        )

        self.order = create_order_for_customer(
            customer=self.customer,
            session_id=self.session_id,
        )

    def test_phone_change_keeps_order_access_and_preserves_order_snapshot(self):
        self.customer.phone = "599777777"
        self.customer.save(update_fields=["phone", "updated_at"])

        for session_id in [self.session_id, self.second_session_id]:
            detail_response = self.client.get(
                f"/api/orders/{self.order.order_number}/?session_id={session_id}",
            )

            self.assertEqual(detail_response.status_code, 200)
            self.assertEqual(
                detail_response.data["current_customer_phone"],
                "599777777",
            )
            self.assertEqual(
                detail_response.data["customer_phone"],
                "555123456",
            )

        list_response = self.client.get(
            f"/api/orders/?session_id={self.second_session_id}",
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(
            list_response.data[0]["order_number"],
            self.order.order_number,
        )
        self.assertEqual(
            list_response.data[0]["current_customer_phone"],
            "599777777",
        )
        self.assertEqual(
            list_response.data[0]["customer_phone"],
            "555123456",
        )

    def test_password_login_reuses_customer_and_keeps_order_access(self):
        self.customer.set_password("Strong1!")

        login_session_id = "password-login-session"

        login_response = self.client.post(
            "/api/accounts/login-password/",
            {
                "session_id": login_session_id,
                "customer_phone": "+995555123456",
                "password": "Strong1!",
            },
            format="json",
        )

        self.assertEqual(login_response.status_code, 200)
        self.assertEqual(login_response.data["id"], self.customer.id)
        self.assertEqual(Customer.objects.count(), 1)

        self.assertTrue(
            CustomerSession.objects.filter(
                customer=self.customer,
                session_id=login_session_id,
            ).exists()
        )

        orders_response = self.client.get(
            f"/api/orders/?session_id={login_session_id}",
        )

        self.assertEqual(orders_response.status_code, 200)
        self.assertEqual(len(orders_response.data), 1)
        self.assertEqual(
            orders_response.data[0]["order_number"],
            self.order.order_number,
        )

    def test_unverified_session_cannot_open_verified_customer_order(self):
        unverified_customer = Customer.objects.create(
            session_id="unverified-session",
            name="Unverified Customer",
            phone="555999888",
            is_phone_verified=False,
        )

        CustomerSession.objects.create(
            customer=unverified_customer,
            session_id="unverified-session",
        )

        response = self.client.get(
            f"/api/orders/{self.order.order_number}/?session_id=unverified-session",
        )

        self.assertEqual(response.status_code, 404)


class OperatorAdminRegressionTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        User = get_user_model()
        self.operator = User.objects.create_user(
            username="operator1",
            password="OperatorStrong1!",
            is_active=True,
            is_staff=True,
            is_superuser=False,
        )

        self.group = Group.objects.create(name="Operator")
        self.operator.groups.add(self.group)

        permission_map = {
            Customer: ["view", "change"],
            CustomerSession: ["view"],
            CustomerTariff: ["view"],
            PhoneVerificationCode: ["view"],
            Order: ["view", "change"],
            OrderItem: ["view", "change"],
            Payment: ["view", "change"],
            OrderSupportMessage: ["view", "add", "change"],
            OrderCustomerNotification: ["view"],
            OrderItemEvent: ["view"],
        }

        for model, actions in permission_map.items():
            content_type = ContentType.objects.get_for_model(model)

            for action in actions:
                permission = Permission.objects.get(
                    content_type=content_type,
                    codename=f"{action}_{model._meta.model_name}",
                )
                self.group.permissions.add(permission)

    def test_operator_can_authenticate_and_open_admin_index(self):
        logged_in = self.client.login(
            username="operator1",
            password="OperatorStrong1!",
        )

        self.assertTrue(logged_in)

        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Site administration")

    def test_operator_has_required_permissions_but_is_not_superuser(self):
        self.assertTrue(self.operator.is_active)
        self.assertTrue(self.operator.is_staff)
        self.assertFalse(self.operator.is_superuser)

        self.assertTrue(self.operator.has_perm("orders.view_order"))
        self.assertTrue(self.operator.has_perm("orders.change_order"))
        self.assertTrue(self.operator.has_perm("orders.change_orderitem"))
        self.assertTrue(self.operator.has_perm("orders.change_payment"))
        self.assertTrue(self.operator.has_perm("accounts.view_customer"))
        self.assertTrue(self.operator.has_perm("accounts.change_customer"))

        self.assertFalse(self.operator.has_perm("auth.change_user"))
        self.assertFalse(self.operator.has_perm("auth.view_group"))
        self.assertFalse(self.operator.has_perm("accounts.change_customertariff"))
