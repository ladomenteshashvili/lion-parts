"""LP-BE-705: admin payment actions go through mark_payment / request_payment (G§7.10)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase

from accounts.testing import make_customer
from orders.models import Order, OrderItem
from orders.services import cancel_order, recalculate_order
from orders.testing import make_order
from payments.models import Payment
from payments.services import create_payment

D = Decimal
PAYMENTS = "/admin/payments/payment/"
ORDERS = "/admin/orders/order/"


class AdminActionTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        self.client.force_login(self.admin)
        self.order = make_order(make_customer())
        self.invoice = create_payment(self.order, "invoice")

    def act(self, url, action, *objects):
        return self.client.post(url, {"action": action, "_selected_action": [o.pk for o in objects]}, follow=True)

    def test_payment_received_records_who_and_when_and_moves_the_order(self):
        self.act(PAYMENTS, "payment_received", self.invoice)
        self.invoice.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual((self.invoice.status, self.invoice.confirmed_by), ("paid", self.admin))
        self.assertIsNotNone(self.invoice.paid_at)
        # the same transition as a card payment (mark_payment)
        self.assertEqual((self.order.status, self.order.amount_paid, self.order.balance_due),
                         ("processing", self.order.total, D("0.00")))
        self.assertEqual(set(self.order.items.values_list("item_status", flat=True)),
                         {OrderItem.STATUS_PAYMENT_CONFIRMED})

    def test_payment_received_from_the_order_admin(self):
        self.act(ORDERS, "payment_received", self.order)
        self.invoice.refresh_from_db()
        self.assertEqual((self.invoice.status, self.invoice.confirmed_by), ("paid", self.admin))

    def test_card_payments_are_not_confirmed_by_hand(self):
        card = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=D("1"),
                                      currency="GEL", status="pending")
        self.act(PAYMENTS, "payment_received", card)
        card.refresh_from_db()
        self.assertEqual(card.status, "pending")

    def test_mark_refunded_only_after_paid(self):
        self.act(PAYMENTS, "mark_refunded", self.invoice)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "pending")
        self.act(PAYMENTS, "payment_received", self.invoice)
        self.act(PAYMENTS, "mark_refunded", self.invoice)  # D20: nothing is owed back on an open order
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "paid")
        cancel_order(self.order.pk, "Supplier failure", actor="operator", operator=True)
        self.act(PAYMENTS, "mark_refunded", self.invoice)
        self.invoice.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual((self.invoice.status, self.order.amount_paid), ("refunded", D("0.00")))

    def test_create_balance_invoice(self):
        self.act(PAYMENTS, "payment_received", self.invoice)
        self.order.refresh_from_db()
        self.act(ORDERS, "create_balance_invoice", self.order)  # fully paid
        self.assertEqual(self.order.payments.count(), 1)
        OrderItem.objects.create(**{**self.order.items.values().first(), "id": None, "part_number": "EXTRA"})
        recalculate_order(Order.objects.get(pk=self.order.pk))
        self.act(ORDERS, "create_balance_invoice", self.order)
        balance = self.order.payments.get(kind="balance")
        self.order.refresh_from_db()
        self.assertEqual((balance.provider, balance.status, balance.amount, balance.currency),
                         ("invoice", "pending", self.order.balance_due, "GEL"))
        self.act(ORDERS, "create_balance_invoice", self.order)  # payment_in_progress: no duplicate
        self.assertEqual(self.order.payments.filter(kind="balance").count(), 1)

    def test_view_only_staff_cannot_run_actions(self):
        staff = get_user_model().objects.create_user("viewer", "v@example.com", "x", is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(codename__in=["view_payment", "view_order"]))
        self.client.force_login(staff)
        response = self.client.get(PAYMENTS)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("payment_received", response.content.decode())
        self.act(PAYMENTS, "payment_received", self.invoice)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "pending")
        self.assertNotIn("create_balance_invoice", self.client.get(ORDERS).content.decode())

    def test_payments_cannot_be_edited_directly(self):
        self.assertEqual(self.client.post(f"{PAYMENTS}{self.invoice.pk}/change/", {"status": "paid"}).status_code, 403)
        self.assertEqual(self.client.get(f"{PAYMENTS}add/").status_code, 403)
