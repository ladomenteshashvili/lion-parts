"""LP-BE-804: customer order cancellation (G§7.13)."""

import threading
import unittest
from decimal import Decimal

from django.db import connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from accounts.testing import make_customer
from orders.models import Notification, Order, OrderItem, OrderItemEvent
from orders.testing import make_order
from payments.models import Payment
from payments.services import create_payment, mark_payment

D = Decimal


class CancelTests(TestCase):
    def setUp(self):
        self.customer = make_customer()
        self.client = APIClient()
        self.client.force_login(self.customer.user)
        self.order = make_order(self.customer, items=(("A", D("100"), 1), ("B", D("50"), 2)))

    def cancel(self, order=None, client=None, reason="Changed my mind"):
        return (client or self.client).post(f"/api/orders/{(order or self.order).order_number}/cancel/",
                                            {"reason": reason}, format="json")

    def test_unpaid_order_cancels_everything_and_expires_open_payments(self):
        invoice = create_payment(self.order, "invoice")
        card = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=D("1"),
                                      currency="GEL", status="created")
        body = self.cancel().json()
        self.assertEqual((body["status"], body["payment_status"], body["can_cancel"], body["total"]),
                         ("cancelled", "unpaid", False, "0.00"))
        self.assertEqual(set(OrderItem.objects.filter(order=self.order).values_list("item_status", flat=True)),
                         {"cancelled"})
        self.assertEqual([Payment.objects.get(pk=p.pk).status for p in (invoice, card)], ["expired", "expired"])
        order = Order.objects.get(pk=self.order.pk)
        self.assertEqual((order.cancel_reason, order.cancelled_at is not None), ("Changed my mind", True))
        self.assertEqual(body["timeline"][-1]["type"], "cancelled")
        events = OrderItemEvent.objects.filter(item__order=self.order, event_type="status_changed")
        self.assertEqual((events.count(), set(events.values_list("actor_type", flat=True))), (2, {"customer"}))

    def test_paid_order_becomes_refund_pending_for_what_was_collected(self):
        mark_payment(create_payment(self.order, "invoice"), "paid")
        body = self.cancel().json()
        self.assertEqual((body["payment_status"], body["amount_paid"], body["balance_due"]),
                         ("refund_pending", "520.00", "-520.00"))
        self.assertEqual(Payment.objects.get(order=self.order).status, "paid")  # never refunded automatically

    def test_any_purchased_item_is_409(self):
        Order.objects.filter(pk=self.order.pk).update(status="processing")
        OrderItem.objects.filter(order=self.order, part_number="A").update(item_status="purchased")
        response = self.cancel()
        self.assertEqual((response.status_code, response.json()["code"]), (409, "not_cancellable"))
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, "processing")

    def test_cancelled_or_completed_is_409_and_a_repeat_is_harmless(self):
        self.assertEqual(self.cancel().status_code, 200)
        self.assertEqual(self.cancel().json()["code"], "not_cancellable")
        self.assertEqual(OrderItemEvent.objects.filter(item__order=self.order).count(), 3)  # 2 item events + the order audit event
        done = make_order(self.customer, status=Order.STATUS_COMPLETED)
        self.assertEqual(self.cancel(done).status_code, 409)

    def test_foreign_anonymous_and_reason_limits(self):
        other = APIClient()
        other.force_login(make_customer(email="o@example.com", phone="599000000").user)
        self.assertEqual(self.cancel(client=other).status_code, 404)
        self.assertEqual(self.cancel(client=APIClient()).status_code, 401)
        self.assertEqual(self.cancel(reason="x" * 1001).status_code, 400)
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, "payment_pending")
        self.assertEqual(self.client.post(f"/api/orders/{self.order.order_number}/cancel/", {},
                                          format="json").status_code, 200)  # the reason is optional


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL (CI)")
class CancelVersusPaymentTests(TransactionTestCase):
    serialized_rollback = True

    def test_cancel_and_payment_callback_in_parallel_never_lose_the_payment(self):
        customer = make_customer()
        order = make_order(customer)
        payment = Payment.objects.create(order=order, kind="initial", provider="fake", amount=order.total,
                                         currency="GEL", status="pending")
        barrier = threading.Barrier(2)

        def cancel():
            client = APIClient()
            client.force_login(customer.user)
            barrier.wait()
            try:
                client.post(f"/api/orders/{order.order_number}/cancel/", {}, format="json")
            finally:
                connection.close()

        def pay():
            barrier.wait()
            try:
                mark_payment(payment, "paid")
            finally:
                connection.close()

        threads = [threading.Thread(target=cancel), threading.Thread(target=pay)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        order.refresh_from_db()
        self.assertEqual((order.status, Payment.objects.get(pk=payment.pk).status), ("cancelled", "paid"))
        self.assertEqual(order.amount_paid, order.items.first().unit_price)  # recorded -> refund_pending
        # the payment notification is not lost to a lock conflict with the cancellation (M5 review)
        self.assertEqual(Notification.objects.filter(event_type="payment_update", order=order).count(), 1)
