"""LP-BE-601: order schema and core services (G§6.5, G§7.8, G§7.13, D16)."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone, translation

from accounts.testing import make_customer
from orders.models import Order, OrderItem
from orders.services import (attention, can_cancel, payment_status, progress, recalculate_order,
                             sync_order_status)
from orders.testing import make_order

D = Decimal


class OrderTestCase(TestCase):
    def setUp(self):
        self.customer = make_customer()

    def order(self, **kwargs):
        return make_order(self.customer, **kwargs)

    def set_items(self, order, *statuses, action=False):
        for item, status in zip(order.items.order_by("id"), statuses):
            item.item_status = status
            item.action_required = action and status == OrderItem.STATUS_ACTION_REQUIRED
            item.save()


class OrderNumberTests(OrderTestCase):
    def test_numbers_follow_the_pk_and_are_unique_within_one_second(self):
        first, second = self.order(), self.order()
        self.assertNotEqual(first.order_number, second.order_number)
        self.assertEqual(first.order_number, f"LP-{first.created_at.year}-{first.pk:05d}")


class MoneyTests(OrderTestCase):
    def test_totals_over_non_cancelled_items(self):
        order = self.order(items=(("A", D("100"), 2), ("B", D("50"), 1)))  # GEL at 2.6: 260 x 2 + 130
        self.assertEqual((order.items_total, order.discount_total, order.total, order.balance_due),
                         (D("650.00"), D("0.00"), D("650.00"), D("650.00")))
        b = order.items.get(part_number="B")
        b.item_status = OrderItem.STATUS_CANCELLED
        b.save()
        recalculate_order(order)
        self.assertEqual(order.total, D("520.00"))

    def test_discount_and_courier_fee(self):
        order = self.order(items=(("A", D("100"), 2),))
        OrderItem.objects.filter(order=order).update(discount_unit_amount=D("10.00"))
        order.courier_fee = D("15.00")
        recalculate_order(order)
        self.assertEqual((order.items_total, order.discount_total, order.total), (D("520.00"), D("20.00"), D("515.00")))

    def test_negative_balance_is_refund_pending(self):
        order = self.order()
        order.amount_paid, order.balance_due = D("300.00"), D("-40.00")
        self.assertEqual(payment_status(order), "refund_pending")


class PaymentStatusTests(OrderTestCase):
    def test_states(self):
        order = self.order()
        self.assertEqual(payment_status(order), "unpaid")
        order.amount_paid, order.balance_due = D("100.00"), order.total - D("100.00")
        self.assertEqual(payment_status(order), "partially_paid")
        order.amount_paid, order.balance_due = order.total, D("0.00")
        self.assertEqual(payment_status(order), "paid")
        order.status = Order.STATUS_CANCELLED
        self.assertEqual(payment_status(order), "refund_pending")


class StatusTests(OrderTestCase):
    def test_payment_pending_completed_and_cancelled_never_change_here(self):
        for status in (Order.STATUS_PAYMENT_PENDING, Order.STATUS_COMPLETED, Order.STATUS_CANCELLED):
            order = self.order(status=status)
            self.set_items(order, OrderItem.STATUS_ACTION_REQUIRED, action=True)
            self.assertEqual(sync_order_status(order).status, status)

    def test_processing_action_required_completed(self):
        order = self.order(status=Order.STATUS_PROCESSING, items=(("A", D("1"), 1), ("B", D("1"), 1)))
        self.set_items(order, OrderItem.STATUS_ACTION_REQUIRED, OrderItem.STATUS_CHECKING, action=True)
        self.assertEqual(sync_order_status(order).status, Order.STATUS_ACTION_REQUIRED)
        self.set_items(order, OrderItem.STATUS_CHECKING, OrderItem.STATUS_PURCHASED)
        self.assertEqual(sync_order_status(order).status, Order.STATUS_PROCESSING)
        order.courier_fee_action_required = True
        self.assertEqual(sync_order_status(order).status, Order.STATUS_ACTION_REQUIRED)
        order.courier_fee_action_required = False
        self.set_items(order, OrderItem.STATUS_COMPLETED, OrderItem.STATUS_CANCELLED)  # cancelled items don't count
        self.assertEqual(sync_order_status(order).status, Order.STATUS_COMPLETED)


class ProgressAndCancelTests(OrderTestCase):
    def test_progress_is_the_least_advanced_item(self):
        order = self.order(items=(("A", D("1"), 1), ("B", D("1"), 1)))
        self.assertEqual(progress(order), {"step": 0, "of": 6})
        self.set_items(order, OrderItem.STATUS_SHIPPED_TO_GEORGIA, OrderItem.STATUS_PURCHASED)
        self.assertEqual(progress(order)["step"], 2)
        self.set_items(order, OrderItem.STATUS_COMPLETED, OrderItem.STATUS_CANCELLED)
        self.assertEqual(progress(order)["step"], 6)

    def test_can_cancel_until_something_is_purchased(self):
        order = self.order(status=Order.STATUS_PROCESSING, items=(("A", D("1"), 1), ("B", D("1"), 1)))
        self.set_items(order, OrderItem.STATUS_CHECKING, OrderItem.STATUS_ACTION_REQUIRED)
        self.assertTrue(can_cancel(order))
        self.set_items(order, OrderItem.STATUS_CHECKING, OrderItem.STATUS_PURCHASED)
        self.assertFalse(can_cancel(order))
        self.assertFalse(can_cancel(self.order(status=Order.STATUS_COMPLETED)))


class AttentionTests(OrderTestCase):
    def test_priority_and_language(self):
        order = self.order(status=Order.STATUS_PROCESSING, items=(("A", D("1"), 1), ("B", D("1"), 1)))
        with translation.override("en"):
            self.assertEqual(attention(order), {"kind": "balance_due", "message": "Remaining amount: ₾5.20."})
            order.courier_fee_action_required = True
            self.assertEqual(attention(order)["kind"], "courier_fee")
            self.set_items(order, OrderItem.STATUS_ACTION_REQUIRED, OrderItem.STATUS_ACTION_REQUIRED, action=True)
            self.assertEqual(attention(order)["message"], "2 items need your confirmation.")
            self.set_items(order, OrderItem.STATUS_ACTION_REQUIRED, OrderItem.STATUS_CHECKING, action=True)
            self.assertEqual(attention(order)["message"], "1 item needs your confirmation.")
        order.courier_fee_action_required = False
        order.balance_due = D("0")
        self.set_items(order, OrderItem.STATUS_READY_FOR_PICKUP, OrderItem.STATUS_COMPLETED)
        self.assertEqual(attention(order)["kind"], "ready_for_pickup")

    def test_russian_plural_forms(self):
        order = self.order(status=Order.STATUS_PROCESSING, items=tuple((f"P{i}", D("1"), 1) for i in range(5)))
        expected = {1: "1 деталь", 2: "2 детали", 5: "5 деталей"}
        for count, start in expected.items():
            OrderItem.objects.filter(order=order).update(action_required=False, item_status=OrderItem.STATUS_CHECKING)
            OrderItem.objects.filter(pk__in=order.items.values_list("pk", flat=True)[:count]).update(action_required=True)
            with translation.override("ru"):
                self.assertTrue(attention(order)["message"].startswith(start), attention(order))


class FrozenOrderTests(OrderTestCase):
    def test_currency_and_rate_cannot_change_after_checkout(self):
        order = self.order()
        for field, value in (("exchange_rate", D("2.9000")), ("currency", "USD")):
            fresh = Order.objects.get(pk=order.pk)
            setattr(fresh, field, value)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                fresh.save()
        fresh = Order.objects.get(pk=order.pk)
        fresh.cancel_reason = "x"
        fresh.save()  # other fields still save
        self.assertEqual(Order.objects.get(pk=order.pk).exchange_rate, D("2.6000"))
