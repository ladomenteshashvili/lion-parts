"""Operator (staff, not superuser) access to the admin. The legacy order-access regressions of this
module were removed with the legacy order schema (LP-BE-601); their scenarios are tracked for LP-BE-801
in docs/legacy-test-map.md."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Customer, CustomerTariff, PhoneVerificationCode
from orders.models import Order, OrderItem, OrderItemEvent, OrderSupportMessage


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
            CustomerTariff: ["view"],
            PhoneVerificationCode: ["view"],
            Order: ["view", "change"],
            OrderItem: ["view", "change"],
            OrderSupportMessage: ["view", "add", "change"],
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

        # The admin follows the operator's browser language (default: ka); assert in English.
        response = self.client.get("/admin/", headers={"accept-language": "en"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Site administration")

    def test_operator_has_required_permissions_but_is_not_superuser(self):
        self.assertTrue(self.operator.is_active)
        self.assertTrue(self.operator.is_staff)
        self.assertFalse(self.operator.is_superuser)

        self.assertTrue(self.operator.has_perm("orders.view_order"))
        self.assertTrue(self.operator.has_perm("orders.change_order"))
        self.assertTrue(self.operator.has_perm("orders.change_orderitem"))
        self.assertTrue(self.operator.has_perm("accounts.view_customer"))
        self.assertTrue(self.operator.has_perm("accounts.change_customer"))

        self.assertFalse(self.operator.has_perm("auth.change_user"))
        self.assertFalse(self.operator.has_perm("auth.view_group"))
        self.assertFalse(self.operator.has_perm("accounts.change_customertariff"))
