"""LP-BE-903: every G§7.11 trigger creates exactly one notification with its targets, after commit."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.testing import make_customer
from orders.models import Notification, Order
from orders.services import (OperatorError, cancel_order, decide, propose_courier_fee, reply_to_order, request_item_action,
                             set_item_status)
from orders.testing import make_order
from parts.models import PartQuoteRequest
from parts.services import mark_quote_ready
from payments.models import Payment
from payments.services import create_payment, mark_payment
from pricing.models import ExchangeRate

D = Decimal


class TriggerTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.customer = make_customer()
        self.order = make_order(self.customer)
        with self.captureOnCommitCallbacks(execute=True):
            mark_payment(create_payment(self.order, "invoice"), "paid")
        self.item = self.order.items.get()

    def run_trigger(self, call):
        before = set(Notification.objects.values_list("pk", flat=True))
        with self.captureOnCommitCallbacks(execute=True):
            call()
        return list(Notification.objects.exclude(pk__in=before))

    def one(self, call, event_type, **targets):
        created = self.run_trigger(call)
        self.assertEqual(len(created), 1, created)
        n = created[0]
        self.assertEqual((n.event_type, n.customer), (event_type, self.customer))
        for name, value in targets.items():
            self.assertEqual(getattr(n, name), value, name)
        return n

    def test_payment_paid_was_notified_once_and_failed_too(self):
        self.assertEqual(Notification.objects.get().event_type, "payment_update")
        card = Payment.objects.create(order=self.order, kind="balance", provider="fake", amount=D("1"),
                                      currency="GEL", status="pending")
        self.one(lambda: mark_payment(card, "failed"), "payment_update", order=self.order)
        self.assertEqual(self.run_trigger(lambda: mark_payment(card, "failed")), [])  # repeated callback

    def test_price_ready(self):
        qr = PartQuoteRequest.objects.create(customer=self.customer, request_type="manual_search", part_number="P",
                                             supplier_price_usd=D("100"))
        self.one(lambda: mark_quote_ready(qr.pk, D("2")), "price_ready", quote_request=qr)

    def test_action_request(self):
        self.one(lambda: request_item_action(self.item.pk, "other", "Call us"), "action_required",
                 order=self.order, item=self.item)

    def test_courier_fee_proposal(self):
        Order.objects.filter(pk=self.order.pk).update(delivery_method="delivery")
        self.one(lambda: propose_courier_fee(self.order.pk, D("15"), ""), "courier_fee_proposed", order=self.order)

    def test_operator_order_message(self):
        n = self.one(lambda: reply_to_order(self.order, "Tomorrow", "op"), "support_reply", order=self.order)
        self.assertEqual((n.support_message.message, n.message), ("Tomorrow", "Tomorrow"))

    def test_visible_status_change_and_no_message_for_a_no_op(self):
        self.one(lambda: set_item_status(self.item.pk, "purchased"), "status_update", order=self.order,
                 item=self.item)
        self.assertEqual(self.run_trigger(lambda: set_item_status(self.item.pk, "purchased")), [])

    def test_operator_cancel_notifies_the_customer_own_actions_do_not(self):
        self.one(lambda: cancel_order(self.order.pk, "Supplier failure", actor="operator", operator=True),
                 "status_update", order=self.order)
        own = make_order(self.customer)
        self.assertEqual(self.run_trigger(lambda: cancel_order(own.pk, "", actor="customer")), [])
        paid = make_order(self.customer)
        with self.captureOnCommitCallbacks(execute=True):
            mark_payment(create_payment(paid, "invoice"), "paid")
        item = paid.items.get()
        with self.captureOnCommitCallbacks(execute=True):
            request_item_action(item.pk, "other", "?")
        self.assertEqual(self.run_trigger(lambda: decide(self.customer, item.pk, "cancel")), [])

    def test_a_failed_action_sends_nothing(self):
        unpaid = make_order(self.customer, status=Order.STATUS_PROCESSING)
        before = Notification.objects.count()
        with self.assertRaises(OperatorError), self.captureOnCommitCallbacks(execute=True):
            set_item_status(unpaid.items.get().pk, "completed")  # balance due: refused
        self.assertEqual(Notification.objects.count(), before)

    def test_admin_reply_goes_through_the_service(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post("/admin/orders/order/", {"action": "reply_to_customer", "_selected_action": [self.order.pk],
                                                      "apply": "1", "message": "Hi"}, follow=True)
        self.assertEqual(Notification.objects.filter(event_type="support_reply").count(), 1)
