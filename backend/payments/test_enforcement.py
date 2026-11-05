"""LPK-219 S2: a provider page that may still take money blocks every new payment until the provider gives evidence. A local
status never lifts the block. Everything else about paying must keep working."""

import unittest
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import make_customer
from api.errors import ApiError
from orders.models import Order
from orders.services import cancel_order, payment_status, set_item_status
from orders.testing import make_order
from payments import test_lifecycle_baseline as baseline
from payments import test_provider_evidence as evidence
from payments.models import Payment
from payments.providers import get_provider
from payments.services import (confirm_manual_payment, mark_payment, open_payment, recheck_payment, record_refund,
                               refund_whole_payment, request_payment, start_payment)
from payments.testing import SimulatedProvider, use_simulated_provider

D = Decimal
CLOSING = ("failed", "expired")
TWO_ITEMS = (("A", D("100"), 1), ("B", D("50"), 1))


class EnforcementTestCase(evidence.EvidenceTestCase):
    def assert_blocked_by(self, order, payment):
        for method in ("card", "invoice"):
            with self.subTest(method=method), self.assertRaises(ApiError) as caught:
                request_payment(order.pk, method)
            self.assertEqual(caught.exception.code, "payment_in_progress")
        self.assertEqual(open_payment(Order.objects.get(pk=order.pk)).pk, payment.pk)

    def order_of(self, payment):
        return Order.objects.get(pk=payment.order_id)

    def prove_closed(self, payment, answer="expired"):
        self.past_window(payment)
        self.gateway.answer_fetch_with(payment, answer)
        self.assertEqual(recheck_payment(self.now(payment), get_provider()), "closed")


class BlockTests(EnforcementTestCase):
    def test_a_local_status_alone_never_lifts_the_block(self):
        for status in CLOSING:
            with self.subTest(status=status):
                payment = self.session()
                mark_payment(payment, status)
                self.assert_blocked_by(self.order_of(payment), payment)

    def test_a_provider_closing_answer_without_proof_does_not_lift_it(self):
        for deliver in (lambda p: self.gateway.send_callback(p, "failed"), lambda p: self.query(p, "failed")):
            payment = self.session()
            deliver(payment)
            self.assert_blocked_by(self.order_of(payment), payment)

    def test_provider_evidence_lifts_it(self):
        payment = self.session()
        mark_payment(payment, "expired")
        self.prove_closed(payment)
        self.assertEqual(request_payment(payment.order_id, "card").status, "created")

    def test_a_declared_final_result_lifts_it_for_that_status_only(self):
        self.gateway.final_statuses = frozenset({"expired"})
        final, not_final = self.session(), self.session()
        self.gateway.send_callback(final, "expired")
        self.gateway.send_callback(not_final, "failed")
        self.assertIsNone(open_payment(self.order_of(final)))
        self.assert_blocked_by(self.order_of(not_final), not_final)

    def test_an_old_row_with_no_evidence_blocks_whatever_its_age_or_provider(self):
        for provider in ("simulated", "a-previous-provider"):
            with self.subTest(provider=provider):
                order = self.order()
                old = Payment.objects.create(order=order, kind="initial", provider=provider, amount=order.total,
                                             currency="GEL", status="expired", checkout_url="https://provider.invalid/x")
                Payment.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=400))
                self.assert_blocked_by(order, old)

    def test_a_refused_or_failed_start_never_reached_a_customer_so_it_does_not_block(self):
        self.gateway.start_error = True
        payment = self.session()
        self.gateway.start_error = False
        self.assert_state(payment, "failed", False)
        self.assertEqual(request_payment(payment.order_id, "card").status, "created")

    def test_paid_and_refunded_do_not_block(self):
        payment = self.session()
        self.gateway.send_callback(payment, "paid")
        self.assertIsNone(open_payment(self.order_of(payment)))
        admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        cancel_order(payment.order_id, "x", actor="operator", operator=True)
        self.assertIsNone(refund_whole_payment(payment, admin))
        self.assert_state(payment, "refunded", False)
        self.assertIsNone(open_payment(self.order_of(payment)))

    def test_the_fake_provider_and_cancelled_invoices_do_not_block(self):
        for provider in ("fake", "invoice", "manual"):
            with self.subTest(provider=provider):
                order = self.order()
                Payment.objects.create(order=order, kind="initial", provider=provider, amount=order.total, currency="GEL",
                                       status="expired", checkout_url="https://example.invalid/x")
                self.assertIsNone(open_payment(order))

    def test_the_answer_is_the_existing_409_with_the_blocking_payment(self):
        payment = self.session()
        mark_payment(payment, "expired")
        client = APIClient()
        client.force_login(self.customer.user)
        response = client.post(f"/api/orders/{payment.order.order_number}/payments/", {"method": "card"}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual((response.json()["code"], response.json()["payment"]["id"]), ("payment_in_progress", payment.pk))

    def test_the_operators_balance_invoice_action_is_refused_the_same_way(self):
        payment = self.session()
        mark_payment(payment, "expired")
        client = APIClient()
        client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))
        client.post("/admin/orders/order/", {"action": "create_balance_invoice", "_selected_action": [payment.order_id]})
        self.assertEqual(self.order_of(payment).payments.count(), 1)


class LegitimatePaymentTests(EnforcementTestCase):
    def split_order(self):
        order = make_order(self.customer, payment_option="split_70_30", items=TWO_ITEMS, payment_method="card")
        Order.objects.filter(pk=order.pk).update(amount_due_now=D("273.00"), split_upfront_percent=D("70"))
        return Order.objects.get(pk=order.pk)

    def paid_session(self, order):
        payment = request_payment(order.pk, "card")
        start_payment(payment)
        self.gateway.send_callback(payment, "paid")
        return self.now(payment)

    def test_70_30_initial_then_balance_collects_the_total(self):
        order = self.split_order()
        self.assertEqual(self.paid_session(order).amount, D("273.00"))
        balance = self.paid_session(order)
        self.assertEqual((balance.kind, balance.amount), ("balance", D("117.00")))
        order.refresh_from_db()
        self.assertEqual((order.balance_due, payment_status(order)), (D("0.00"), "paid"))

    def test_a_closed_balance_attempt_blocks_the_next_until_proven_closed(self):
        order = self.split_order()
        self.paid_session(order)
        balance = request_payment(order.pk, "card")
        start_payment(balance)
        self.gateway.send_callback(balance, "failed")
        self.assert_blocked_by(order, balance)
        self.prove_closed(balance, "failed")
        self.assertEqual(request_payment(order.pk, "invoice").amount, D("117.00"))

    def test_an_invoice_flow_is_untouched(self):
        admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        order = self.order()
        invoice = request_payment(order.pk, "invoice")
        self.assert_blocked_by(order, invoice)
        self.assertIsNone(confirm_manual_payment(invoice, admin))
        order.refresh_from_db()
        self.assertEqual(payment_status(order), "paid")

    def test_refund_pending_accounting_is_untouched_by_a_late_result(self):
        admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        payment = self.session()
        cancel_order(payment.order_id, "x", actor="operator", operator=True)
        self.gateway.send_callback(payment, "paid")
        order = self.order_of(payment)
        self.assertEqual(payment_status(order), "refund_pending")
        self.assertEqual(record_refund(order.pk, admin, "Late"), payment.amount)
        order.refresh_from_db()
        self.assertEqual((order.balance_due, order.amount_paid), (D("0.00"), D("0.00")))


class StartRefusalTests(EnforcementTestCase):
    def assert_refused(self, payment, reason):
        self.assertFalse(start_payment(payment))
        current = self.now(payment)
        self.assertEqual((current.status, current.checkout_url), ("expired", ""))
        self.assertEqual(current.provider_payload["start_refused"], reason)
        self.assertIsNone(current.checkout_expires_at)

    def test_an_answer_arriving_after_a_newer_payment_exists_is_refused(self):
        slow = request_payment(self.order().pk, "card")
        baseline.age(slow, 31)
        order = self.order_of(slow)
        newer = []

        def start(_, __):
            newer.append(request_payment(order.pk, "card"))
            return "https://provider.invalid/slow"

        with mock.patch.object(SimulatedProvider, "start", start):
            self.assert_refused(slow, "newer_payment")
        self.assertEqual(open_payment(order).pk, newer[0].pk)
        self.assertTrue(start_payment(newer[0]))

    def test_an_answer_arriving_after_the_order_was_cancelled_is_refused(self):
        payment = request_payment(self.order().pk, "card")

        def start(_, __):
            Order.objects.filter(pk=payment.order_id).update(status=Order.STATUS_CANCELLED)
            return "https://provider.invalid/late"

        with mock.patch.object(SimulatedProvider, "start", start):
            self.assert_refused(payment, "order_cancelled")

    def test_an_answer_arriving_after_the_amount_changed_is_refused(self):
        payment = request_payment(self.order().pk, "card")

        def start(_, __):
            Order.objects.filter(pk=payment.order_id).update(balance_due=payment.amount - 1)
            return "https://provider.invalid/late"

        with mock.patch.object(SimulatedProvider, "start", start):
            self.assert_refused(payment, "amount_changed")

    def test_the_provider_is_not_called_when_the_answer_could_not_be_used(self):
        payment = request_payment(self.order().pk, "card")
        Order.objects.filter(pk=payment.order_id).update(status=Order.STATUS_CANCELLED)
        with mock.patch.object(SimulatedProvider, "start") as start:
            self.assert_refused(payment, "order_cancelled")
        start.assert_not_called()

    def test_a_refused_start_does_not_block_because_no_customer_got_its_page(self):
        payment = request_payment(self.order().pk, "card")
        Order.objects.filter(pk=payment.order_id).update(balance_due=payment.amount - 1)
        self.assert_refused(payment, "amount_changed")
        self.assertIsNone(open_payment(self.order_of(payment)))


class ResetTests(EnforcementTestCase):
    def test_an_item_cancelled_closes_the_session_locally_and_the_block_stays(self):
        payment = self.session(items=TWO_ITEMS)
        set_item_status(payment.order.items.get(part_number="A").pk, "cancelled", reason="x")
        self.assert_state(payment, "expired", False)
        self.assert_blocked_by(self.order_of(payment), payment)

    def test_a_cancelled_order_answers_nothing_to_pay_whatever_blocks(self):
        payment = self.session()
        cancel_order(payment.order_id, "x", actor="operator", operator=True)
        with self.assertRaises(ApiError) as caught:
            request_payment(payment.order_id, "card")
        self.assertEqual(caught.exception.code, "nothing_to_pay")


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL (CI)")
class ParallelEnforcementTests(TransactionTestCase):
    """Real parallel transactions. A deadlock surfaces as an exception in a thread, which fails the test."""

    serialized_rollback = True
    ROUNDS = 5

    def setUp(self):
        self.gateway = use_simulated_provider(self)
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)

    def parallel(self, *work):
        return baseline.ParallelLifecycleBaselineTests.parallel(self, *work)

    def new_payment_or_nothing(self, order):
        def work():
            try:
                start_payment(request_payment(order.pk, "card"))
            except ApiError:
                pass
        return work

    def test_a_new_payment_against_a_cancellation_leaves_no_page_open(self):
        for _ in range(self.ROUNDS):
            order = make_order(self.customer, items=TWO_ITEMS, payment_method="card")
            self.parallel(self.new_payment_or_nothing(order),
                          lambda: cancel_order(order.pk, "x", actor="operator", operator=True))
            self.assertFalse(order.payments.filter(status__in=Payment.OPEN_STATUSES).exists())

    def test_a_new_payment_against_an_amount_reset_leaves_no_old_amount_open(self):
        for _ in range(self.ROUNDS):
            order = make_order(self.customer, items=TWO_ITEMS, payment_method="card")
            item = order.items.get(part_number="A")
            self.parallel(self.new_payment_or_nothing(order), lambda: set_item_status(item.pk, "cancelled", reason="x"))
            order.refresh_from_db()
            for payment in order.payments.filter(status__in=Payment.OPEN_STATUSES):
                self.assertLessEqual(payment.amount, order.amount_due_now)

    def test_two_new_payments_create_one_session(self):
        for _ in range(self.ROUNDS):
            order = make_order(self.customer, payment_method="card")
            self.parallel(self.new_payment_or_nothing(order), self.new_payment_or_nothing(order))
            self.assertEqual(order.payments.count(), 1)
