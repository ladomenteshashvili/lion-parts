"""LP-BE-702: mark_payment (G§7.10)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.testing import make_customer
from orders.models import Order, OrderItemEvent
from orders.testing import make_order
from payments.models import Payment
from payments.services import mark_payment

D = Decimal


class MarkPaymentTests(TestCase):
    def setUp(self):
        self.order = make_order(make_customer(), payment_option="split_70_30", items=(("A", D("100"), 2), ("B", D("50"), 1)))
        self.order.amount_due_now = D("455.00")  # 70 % of 650.00
        self.order.save()
        self.initial = Payment.objects.create(order=self.order, kind="initial", provider="invoice",
                                              amount=D("455.00"), currency="GEL", status="pending")

    def refresh(self):
        self.order.refresh_from_db()
        self.initial.refresh_from_db()

    def test_initial_split_payment_moves_the_order_to_processing(self):
        mark_payment(self.initial, "paid")
        self.refresh()
        self.assertEqual((self.order.status, self.order.amount_paid, self.order.balance_due),
                         (Order.STATUS_PROCESSING, D("455.00"), D("195.00")))  # 30 % left
        self.assertEqual(set(self.order.items.values_list("item_status", flat=True)), {"payment_confirmed"})
        self.assertIsNotNone(self.initial.paid_at)

    def test_paid_twice_counts_once(self):
        mark_payment(self.initial, "paid")
        mark_payment(self.initial, "paid")
        self.refresh()
        self.assertEqual(self.order.amount_paid, D("455.00"))
        self.assertEqual(OrderItemEvent.objects.filter(text_code="payment_confirmed").count(), 2)  # one per item

    def test_paid_is_never_downgraded_but_can_be_refunded(self):
        mark_payment(self.initial, "paid")
        for status in ("failed", "expired", "pending", "unknown"):
            mark_payment(self.initial, status)
            self.refresh()
            self.assertEqual(self.initial.status, "paid")
        mark_payment(self.initial, "refunded")
        self.refresh()
        self.assertEqual((self.initial.status, self.order.amount_paid), ("refunded", D("0.00")))
        mark_payment(self.initial, "paid")  # refunded is final
        self.refresh()
        self.assertEqual(self.initial.status, "refunded")

    def test_only_paid_payments_can_be_refunded(self):
        mark_payment(self.initial, "refunded")
        self.refresh()
        self.assertEqual(self.initial.status, "pending")

    def test_failed_payment_leaves_the_order_waiting(self):
        mark_payment(self.initial, "failed")
        self.refresh()
        self.assertEqual((self.order.status, self.order.amount_paid), (Order.STATUS_PAYMENT_PENDING, D("0.00")))

    def test_balance_payment_completes_the_money(self):
        mark_payment(self.initial, "paid")
        balance = Payment.objects.create(order=self.order, kind="balance", provider="invoice", amount=D("195.00"),
                                         currency="GEL", status="pending")
        mark_payment(balance, "paid", confirmed_by=get_user_model().objects.create_user("ops", is_staff=True))
        self.refresh()
        self.assertEqual((self.order.amount_paid, self.order.balance_due), (D("650.00"), D("0.00")))
        balance.refresh_from_db()
        self.assertEqual(balance.confirmed_by.username, "ops")

    def test_unknown_status_is_refused(self):
        with self.assertRaises(ValueError):
            mark_payment(self.initial, "bogus")
