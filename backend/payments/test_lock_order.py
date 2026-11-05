"""LPK-219 S0: the row locks each payment path takes, in order, read from the SQL it runs. The rule is payments, then the
order, then items, then courier proposals. A payment appearing after the order is mark_payment locking a row its caller
already holds, so it cannot wait. Plain writes also lock rows but do not show here, so this does not cover them."""

import re
import threading
import time
import unittest
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import OperationalError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.testing import make_customer
from orders import services
from orders.models import Order, OrderItem
from orders.testing import make_order
from payments.models import Payment
from payments.providers import FakeProvider, get_provider
from payments.services import (confirm_manual_payment, mark_payment, recheck_payment, record_provider_result, record_refund,
                               refund_whole_payment, request_payment, start_payment)
from payments.testing import use_simulated_provider

D = Decimal
NAMES = {"payments_payment": "payment", "orders_order": "order", "orders_orderitem": "item",
         "orders_courierfeeproposal": "proposal", "accounts_customer": "customer"}
LOCK = re.compile(r'FROM "(\w+)".* FOR (?:NO KEY )?UPDATE', re.DOTALL)


def locks_taken(queries):
    """Consecutive locks on one table count once: they are the same step."""
    steps = []
    for query in queries:
        match = LOCK.search(query["sql"])
        if match and (not steps or steps[-1] != NAMES.get(match[1], match[1])):
            steps.append(NAMES.get(match[1], match[1]))
    return steps


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL (CI)")
class LockOrderTests(TestCase):
    def setUp(self):
        self.gateway = use_simulated_provider(self)
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        self.order = make_order(self.customer, items=(("A", D("100"), 1), ("B", D("50"), 1)), payment_method="card")

    def locks(self, work):
        with CaptureQueriesContext(connection) as queries:
            work()
        return locks_taken(queries.captured_queries)

    def payment(self, status="pending", provider="simulated", amount=None, order=None):
        order = order or self.order
        payment = Payment.objects.create(order=order, kind="initial", provider=provider, currency="GEL", status=status,
                                         amount=amount if amount is not None else order.total)
        payment.provider_order_id = f"{order.order_number}-{payment.pk}"
        payment.save(update_fields=["provider_order_id"])
        return payment

    def started(self):
        payment = self.payment("created")
        start_payment(payment)
        return Payment.objects.get(pk=payment.pk)

    def test_mark_payment(self):
        for status in ("paid", "failed", "expired"):
            with self.subTest(status=status):
                payment = self.payment()
                self.assertEqual(self.locks(lambda s=status: mark_payment(payment, s)), ["payment", "order"])

    def test_request_payment_locks_only_the_order_and_inserts_a_new_row(self):
        self.assertEqual(self.locks(lambda: request_payment(self.order.pk, "card")), ["order"])

    def test_start_payment(self):
        payment = self.payment("created")
        self.assertEqual(self.locks(lambda: start_payment(payment)), ["payment", "order", "payment", "order"])

    def test_callback(self):
        payment = self.started()
        self.assertEqual(self.locks(lambda: self.gateway.send_callback(payment, "paid")), ["payment", "order"])

    def test_record_provider_result_with_closure(self):
        self.gateway.final_statuses = frozenset({"failed"})
        payment = self.started()
        self.assertEqual(self.locks(lambda: record_provider_result(payment, "failed", get_provider())),
                         ["payment", "order"])
        self.assertIsNotNone(Payment.objects.get(pk=payment.pk).provider_closed_at)

    def test_recheck_payment(self):
        payment = self.started()
        self.gateway.answer_fetch_with(payment, "paid")
        self.assertEqual(self.locks(lambda: recheck_payment(payment, get_provider())), ["payment", "order"])

    def test_reconcile_payments(self):
        payment = self.started()
        Payment.objects.filter(pk=payment.pk).update(created_at="2026-01-01T00:00:00Z")
        self.gateway.answer_fetch_with(payment, "failed")
        self.assertEqual(self.locks(lambda: call_command("reconcile_payments", stdout=StringIO())), ["payment", "order"])

    def test_cancel_order_relocks_only_payments_it_holds(self):
        self.started()
        self.assertEqual(self.locks(lambda: services.cancel_order(self.order.pk, "x", actor="customer")),
                         ["payment", "order", "item", "payment", "order"])

    def test_set_item_status_with_an_amount_reset_relocks_only_payments_it_holds(self):
        self.started()
        item = self.order.items.get(part_number="A")
        self.assertEqual(self.locks(lambda: services.set_item_status(item.pk, "cancelled", reason="x")),
                         ["payment", "order", "item", "payment", "order"])

    def test_decide(self):
        order = make_order(self.customer)
        Order.objects.filter(pk=order.pk).update(status="processing")
        item = order.items.get()
        OrderItem.objects.filter(pk=item.pk).update(item_status="checking")
        services.request_item_action(item.pk, "other", "Please check")
        self.payment(order=order)
        self.assertEqual(self.locks(lambda: services.decide(self.customer, item.pk, "cancel")),
                         ["payment", "order", "item", "payment", "order"])

    def test_request_item_action_takes_no_payment_lock(self):
        Order.objects.filter(pk=self.order.pk).update(status="processing")
        item = self.order.items.get(part_number="A")
        self.assertEqual(self.locks(lambda: services.request_item_action(item.pk, "other", "Please check")),
                         ["order", "item"])

    def test_confirm_manual_payment(self):
        invoice = self.payment(provider="invoice")
        self.assertEqual(self.locks(lambda: confirm_manual_payment(invoice, self.admin)),
                         ["payment", "order", "payment", "order"])

    def test_record_refund(self):
        mark_payment(self.payment(), "paid")
        mark_payment(self.payment(), "paid")
        self.assertEqual(self.locks(lambda: record_refund(self.order.pk, self.admin)), ["payment", "order"])

    def test_refund_whole_payment(self):
        payment = self.payment()
        mark_payment(payment, "paid")
        services.cancel_order(self.order.pk, "x", actor="operator", operator=True)
        self.assertEqual(self.locks(lambda: refund_whole_payment(payment, self.admin)),
                         ["payment", "order", "payment", "order"])

    def test_courier_fee_paths_take_no_payment_lock(self):
        Order.objects.filter(pk=self.order.pk).update(status="processing", delivery_method="delivery")
        self.assertEqual(self.locks(lambda: services.propose_courier_fee(self.order.pk, D("15.00"), "Vake")), ["order"])
        self.assertEqual(self.locks(lambda: services.respond_courier_fee(self.order.pk, True)), ["order", "proposal"])


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL (CI)")
class WaitingOnAPaymentTests(TransactionTestCase):
    """Real locks, no timing luck. This connection holds a payment's row lock while the path runs in another connection
    until PostgreSQL shows it waiting. That path must not hold the order yet, or the two could deadlock: anything holding
    a payment goes on to lock the order. Checked with NOWAIT, so a wrong order fails here rather than hanging."""

    serialized_rollback = True

    def setUp(self):
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        self.order = make_order(self.customer, items=(("A", D("100"), 1), ("B", D("50"), 1)))

    def payment(self, status="pending", provider="fake"):
        return Payment.objects.create(order=self.order, kind="initial", provider=provider, amount=self.order.total,
                                      currency="GEL", status=status)

    def wait_until_another_connection_is_blocked(self):
        with connection.cursor() as cursor:
            for _ in range(200):  # at most 10 s; it normally takes one or two polls
                cursor.execute("SELECT pg_stat_clear_snapshot()")  # inside a transaction the view is otherwise cached
                cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                               "AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()")
                if cursor.fetchone()[0]:
                    return True
                time.sleep(0.05)
        return False

    def assert_waits_for_the_payment_before_the_order(self, payment, path):
        errors = []

        def run():
            try:
                path()
            except Exception as error:
                errors.append(repr(error))
            finally:
                connection.close()

        thread = threading.Thread(target=run)
        try:
            with transaction.atomic():
                Payment.objects.select_for_update(no_key=True).get(pk=payment.pk)
                thread.start()
                self.assertTrue(self.wait_until_another_connection_is_blocked(), "the path did not wait for a lock")
                try:
                    Order.objects.select_for_update(no_key=True, nowait=True).get(pk=self.order.pk)
                except OperationalError:
                    self.fail("the path already held the order while waiting for a payment")
        finally:  # released by now; the test's cleanup must not run alongside the path
            if thread.ident:
                thread.join(30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_mark_payment(self):
        payment = self.payment()
        self.assert_waits_for_the_payment_before_the_order(payment, lambda: mark_payment(payment, "paid"))

    def test_record_provider_result(self):
        payment = self.payment()
        Payment.objects.filter(pk=payment.pk).update(checkout_url="https://provider.invalid/x")
        self.assert_waits_for_the_payment_before_the_order(
            payment, lambda: record_provider_result(payment, "failed", FakeProvider()))
        self.assertIsNotNone(Payment.objects.get(pk=payment.pk).provider_closed_at)

    def test_start_payment(self):
        payment = self.payment("created")
        self.assert_waits_for_the_payment_before_the_order(payment, lambda: start_payment(payment))

    def test_cancel_order(self):
        self.assert_waits_for_the_payment_before_the_order(
            self.payment(), lambda: services.cancel_order(self.order.pk, "x", actor="operator", operator=True))

    def test_set_item_status(self):
        item = self.order.items.get(part_number="A")
        self.assert_waits_for_the_payment_before_the_order(
            self.payment(), lambda: services.set_item_status(item.pk, "cancelled", reason="x"))

    def test_decide(self):
        Order.objects.filter(pk=self.order.pk).update(status="processing")
        item = self.order.items.get(part_number="A")
        OrderItem.objects.filter(pk=item.pk).update(item_status="checking")
        services.request_item_action(item.pk, "other", "Please check")
        self.assert_waits_for_the_payment_before_the_order(
            self.payment(), lambda: services.decide(self.customer, item.pk, "cancel"))

    def test_confirm_manual_payment(self):
        invoice = self.payment(provider="invoice")
        self.assert_waits_for_the_payment_before_the_order(invoice, lambda: confirm_manual_payment(invoice, self.admin))

    def test_record_refund(self):
        payment = self.payment()
        mark_payment(payment, "paid")
        self.assert_waits_for_the_payment_before_the_order(payment, lambda: record_refund(self.order.pk, self.admin))

    def test_refund_whole_payment(self):
        payment = self.payment()
        mark_payment(payment, "paid")
        services.cancel_order(self.order.pk, "x", actor="operator", operator=True)
        self.assert_waits_for_the_payment_before_the_order(payment, lambda: refund_whole_payment(payment, self.admin))


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL (CI)")
class PaymentCommittedWhileWaitingTests(TransactionTestCase):
    """A path that closes open payments reads them, then waits for the order. A payment committed in between must not be
    missed: the path starts again and takes it first, so the lock order holds. Real locks, no timing luck."""

    serialized_rollback = True
    wait_until_another_connection_is_blocked = WaitingOnAPaymentTests.wait_until_another_connection_is_blocked

    def setUp(self):
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.order = make_order(self.customer, items=(("A", D("100"), 1), ("B", D("50"), 1)), payment_method="card")

    def payment_committed_while_it_waits(self, path):
        errors = []

        def run():
            try:
                path()
            except Exception as error:
                errors.append(repr(error))
            finally:
                connection.close()

        thread = threading.Thread(target=run)
        try:
            with transaction.atomic():
                Order.objects.select_for_update(no_key=True).get(pk=self.order.pk)
                thread.start()
                self.assertTrue(self.wait_until_another_connection_is_blocked(), "the path did not wait for the order")
                payment = request_payment(self.order.pk, "card")
        finally:
            if thread.ident:
                thread.join(30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        return Payment.objects.get(pk=payment.pk)

    def test_cancel_order_closes_it_and_it_gets_no_page(self):
        payment = self.payment_committed_while_it_waits(
            lambda: services.cancel_order(self.order.pk, "x", actor="operator", operator=True))
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, Order.STATUS_CANCELLED)
        self.assertEqual(payment.status, "expired")
        self.assertFalse(start_payment(payment))
        self.assertEqual(Payment.objects.get(pk=payment.pk).checkout_url, "")

    def test_an_amount_reset_closes_it_and_it_gets_no_page(self):
        item = self.order.items.get(part_number="A")
        payment = self.payment_committed_while_it_waits(
            lambda: services.set_item_status(item.pk, "cancelled", reason="x"))
        self.assertEqual(payment.status, "expired")
        self.assertFalse(start_payment(payment))
        self.assertEqual(Payment.objects.get(pk=payment.pk).checkout_url, "")

    def test_a_customers_last_item_decision_closes_it(self):
        order = make_order(self.customer, payment_method="card")
        Order.objects.filter(pk=order.pk).update(status="processing")
        item = order.items.get()
        OrderItem.objects.filter(pk=item.pk).update(item_status="checking")
        services.request_item_action(item.pk, "other", "Please check")
        self.order = order
        payment = self.payment_committed_while_it_waits(lambda: services.decide(self.customer, item.pk, "cancel"))
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.STATUS_CANCELLED)
        self.assertEqual(payment.status, "expired")
