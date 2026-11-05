"""LPK-219 S1a: provider evidence is recorded and nothing reads it yet. Every decision about a new payment must stay
exactly as it was, so these tests also check that the two columns change no outcome."""

import inspect
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import make_customer
from api.errors import ApiError
from api.tests.test_contract import use_demo_routes
from orders.models import Order
from orders.serializers import payment_data
from orders.services import cancel_order, set_item_status
from orders.testing import make_order
from payments import test_lifecycle_baseline as baseline
from payments.models import Payment
from payments.providers import FakeProvider, PaymentProvider, get_provider
from payments.services import (PROVIDER_CLOSE_MARGIN, mark_payment, open_payment, recheck_payment, record_provider_result,
                               request_payment, start_payment)
from payments.testing import SimulatedProvider, use_simulated_provider

D = Decimal
LIFETIME = timedelta(hours=1)
CLOSING = ("failed", "expired")


class EvidenceTestCase(TestCase):
    def setUp(self):
        self.gateway = use_simulated_provider(self)
        self.gateway.checkout_lifetime = LIFETIME
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)

    def order(self, items=(("A", D("100"), 1),)):
        return make_order(self.customer, items=items, payment_method="card")

    def session(self, items=(("A", D("100"), 1),)):
        payment = request_payment(self.order(items).pk, "card")
        start_payment(payment)
        return self.now(payment)

    def now(self, payment):
        return Payment.objects.get(pk=payment.pk)

    def past_window(self, payment):
        Payment.objects.filter(pk=payment.pk).update(
            checkout_expires_at=timezone.now() - PROVIDER_CLOSE_MARGIN - timedelta(seconds=1))

    def query(self, payment, *answers):
        self.gateway.answer_fetch_with(payment, *answers)
        return recheck_payment(self.now(payment), get_provider())

    def assert_state(self, payment, status, closed):
        payment = self.now(payment)
        self.assertEqual(payment.status, status)
        self.assertEqual(payment.provider_closed_at is not None, closed)
        return payment


class CheckoutExpiryTests(EvidenceTestCase):
    def test_it_is_saved_with_the_link(self):
        before = timezone.now()
        payment = self.session()
        self.assertTrue(payment.checkout_url)
        self.assertGreaterEqual(payment.checkout_expires_at, before + LIFETIME)
        self.assertLessEqual(payment.checkout_expires_at, timezone.now() + LIFETIME)

    def test_it_stays_unknown_without_a_declared_lifetime(self):
        self.gateway.checkout_lifetime = None
        payment = self.session()
        self.assertTrue(payment.checkout_url)
        self.assertIsNone(payment.checkout_expires_at)

    def test_a_repeated_start_never_moves_it_earlier(self):
        payment = self.session()
        later = timezone.now() + timedelta(days=3)
        Payment.objects.filter(pk=payment.pk).update(checkout_expires_at=later)
        start_payment(self.now(payment))
        self.assertEqual(self.now(payment).checkout_expires_at, later)

    def test_a_failed_start_records_nothing(self):
        self.gateway.start_error = True
        payment = self.session()
        self.assert_state(payment, "failed", False)
        self.assertEqual(payment.checkout_url, "")
        self.assertIsNone(payment.checkout_expires_at)

    def test_a_link_arriving_after_the_payment_closed_is_dropped_with_its_expiry(self):
        payment = request_payment(self.order().pk, "card")

        def closes_during_the_call(_, started):
            mark_payment(started, "failed")
            return "https://provider.invalid/late"

        with mock.patch.object(SimulatedProvider, "start", closes_during_the_call):
            self.assertFalse(start_payment(payment))
        self.assertEqual(self.now(payment).checkout_url, "")
        self.assertIsNone(self.now(payment).checkout_expires_at)

    def test_an_unexpected_start_error_leaves_the_payment_as_it_was(self):
        payment = request_payment(self.order().pk, "card")
        with mock.patch.object(SimulatedProvider, "start", side_effect=RuntimeError), self.assertRaises(RuntimeError):
            start_payment(payment)
        self.assert_state(payment, "created", False)
        self.assertIsNone(self.now(payment).checkout_expires_at)

    def test_a_crash_before_commit_rolls_back_link_expiry_and_status_together(self):
        payment = request_payment(self.order().pk, "card")
        with mock.patch("payments.services.mark_payment", side_effect=RuntimeError), self.assertRaises(RuntimeError):
            start_payment(payment)
        current = self.assert_state(payment, "created", False)
        self.assertEqual(current.checkout_url, "")
        self.assertIsNone(current.checkout_expires_at)


class ClosureEvidenceTests(EvidenceTestCase):
    def test_a_query_after_the_window_closes_it_with_the_result(self):
        for seen in ("pending", "unknown"):
            for answer in CLOSING:
                with self.subTest(seen=seen, answer=answer):
                    payment = self.session()
                    if seen == "unknown":
                        mark_payment(payment, "unknown")
                    self.past_window(payment)
                    self.assertEqual(self.query(payment, answer), "updated")
                    current = self.assert_state(payment, answer, True)
                    self.assertEqual(current.provider_payload["closure"], "query_after_window")
                    self.assertEqual(current.provider_payload["reconciled"], answer)

    def test_a_query_inside_the_window_changes_the_status_but_proves_nothing(self):
        for answer in CLOSING:
            with self.subTest(answer=answer):
                payment = self.session()
                self.assertEqual(self.query(payment, answer), "updated")
                self.assert_state(payment, answer, False)

    def test_the_window_ends_exactly_at_expiry_plus_margin(self):
        for offset, closed in ((timedelta(microseconds=-1), False), (timedelta(0), True)):
            with self.subTest(offset=offset):
                payment = self.session()
                at = self.now(payment).checkout_expires_at + PROVIDER_CLOSE_MARGIN + offset
                with mock.patch("django.utils.timezone.now", return_value=at):
                    self.query(payment, "failed")
                self.assert_state(payment, "failed", closed)

    def test_a_query_started_before_the_window_ended_proves_nothing_however_long_it_took(self):
        payment = self.session()
        window_end = self.now(payment).checkout_expires_at + PROVIDER_CLOSE_MARGIN
        clock = [window_end - timedelta(seconds=1)]

        def slow_answer(_):
            clock[0] = window_end + timedelta(hours=1)
            return "failed"

        with mock.patch("django.utils.timezone.now", side_effect=lambda: clock[0]), \
                mock.patch.object(SimulatedProvider, "fetch_status", side_effect=slow_answer):
            recheck_payment(self.now(payment), get_provider())
        self.assert_state(payment, "failed", False)

    def test_an_unknown_expiry_never_satisfies_the_window(self):
        self.gateway.checkout_lifetime = None
        payment = self.session()
        self.query(payment, "failed")
        self.assert_state(payment, "failed", False)

    def test_a_callback_cannot_use_the_window_because_delivery_can_be_late(self):
        for status in CLOSING:
            with self.subTest(status=status):
                payment = self.session()
                self.past_window(payment)
                self.assertEqual(self.gateway.send_callback(payment, status).status_code, 200)
                self.assert_state(payment, status, False)

    def test_finality_is_per_status_and_only_what_the_provider_declared(self):
        self.gateway.final_statuses = frozenset({"expired"})
        send = {"callback": lambda p, status: self.gateway.send_callback(p, status),
                "query": lambda p, status: self.query(p, status)}
        for source, deliver in send.items():
            with self.subTest(source=source):
                failed, expired = self.session(), self.session()
                deliver(failed, "failed")
                deliver(expired, "expired")
                self.assert_state(failed, "failed", False)
                current = self.assert_state(expired, "expired", True)
                self.assertEqual(current.provider_payload["closure"], "final_result")

    def test_a_provider_declares_nothing_final_or_any_lifetime_by_default(self):
        self.assertEqual(PaymentProvider.final_statuses, frozenset())
        self.assertIsNone(PaymentProvider.checkout_lifetime)

    def test_a_result_for_a_payment_with_no_stored_link_proves_nothing(self):
        self.gateway.final_statuses = frozenset(CLOSING)
        payment = request_payment(self.order().pk, "card")
        self.gateway.send_callback(payment, "failed")
        self.assert_state(payment, "failed", False)
        self.assertFalse(start_payment(self.now(payment)))
        self.assertEqual(self.now(payment).checkout_url, "")
        self.assert_state(payment, "failed", False)

    def test_paid_needs_no_closure(self):
        self.gateway.final_statuses = frozenset(CLOSING)
        payment = self.session()
        self.past_window(payment)
        self.gateway.send_callback(payment, "paid")
        self.assert_state(payment, "paid", False)

    def test_a_closing_result_after_paid_changes_nothing_and_is_logged(self):
        self.gateway.final_statuses = frozenset(CLOSING)
        payment = self.session()
        self.gateway.send_callback(payment, "paid")
        with self.assertLogs("payments.services", "WARNING"):
            self.gateway.send_callback(payment, "failed")
        self.assert_state(payment, "paid", False)

    def test_answers_without_a_result_change_nothing(self):
        self.gateway.final_statuses = frozenset(CLOSING)
        for answer, raised in (("pending", None), ("not-a-status", ValueError), (RuntimeError("down"), RuntimeError)):
            with self.subTest(answer=answer):
                payment = self.session()
                self.past_window(payment)
                if raised:
                    with self.assertRaises(raised):
                        self.query(payment, answer)
                else:
                    self.assertEqual(self.query(payment, answer), "pending")
                self.assert_state(payment, "pending", False)

    def test_another_providers_payment_proves_nothing(self):
        self.gateway.final_statuses = frozenset(CLOSING)
        other = self.session()
        Payment.objects.filter(pk=other.pk).update(provider="fake")
        record_provider_result(other, "failed", get_provider())
        self.assert_state(other, "failed", False)
        invoice = request_payment(self.order().pk, "invoice")
        record_provider_result(invoice, "expired", get_provider())
        self.assert_state(invoice, "expired", False)

    def test_local_changes_never_record_closure(self):
        self.gateway.final_statuses = frozenset(CLOSING)
        cancelled = self.session()
        cancel_order(cancelled.order_id, "x", actor="operator", operator=True)
        self.assert_state(cancelled, "expired", False)

        reset = self.session(items=(("A", D("100"), 1), ("B", D("50"), 1)))
        set_item_status(reset.order.items.get(part_number="A").pk, "cancelled", reason="x")
        self.assert_state(reset, "expired", False)

        aged = self.session()
        baseline.age(aged, 31)
        self.gateway.answer_fetch_with(aged, "pending")
        baseline.run_reconcile()
        self.assert_state(aged, "unknown", False)

    def test_todays_move_from_expired_to_failed_and_its_notification_are_kept(self):
        for final, closed in ((frozenset(), False), (frozenset(CLOSING), True)):
            with self.subTest(final=final):
                self.gateway.final_statuses = final
                payment = self.session()
                mark_payment(payment, "expired")
                with mock.patch("orders.notifications.notify_after_commit") as notify:
                    self.gateway.send_callback(payment, "failed")
                self.assert_state(payment, "failed", closed)
                notify.assert_called_once()

    def test_a_repeated_result_keeps_the_first_timestamp(self):
        self.gateway.final_statuses = frozenset(CLOSING)
        payment = self.session()
        self.gateway.send_callback(payment, "failed")
        first = self.assert_state(payment, "failed", True)
        self.gateway.send_callback(payment, "failed")
        again = self.assert_state(payment, "failed", True)
        self.assertEqual(again.provider_closed_at, first.provider_closed_at)
        self.assertEqual(again.provider_payload, first.provider_payload)

    def test_a_result_arriving_during_a_query_wins_and_the_query_proves_nothing(self):
        payment = self.session()
        self.past_window(payment)

        def answer(_):
            self.gateway.send_callback(payment, "paid")
            return "failed"

        with mock.patch.object(SimulatedProvider, "fetch_status", side_effect=answer):
            self.assertEqual(recheck_payment(self.now(payment), get_provider()), "superseded")
        self.assert_state(payment, "paid", False)

    def test_a_query_does_not_replace_closure_recorded_during_it(self):
        self.gateway.final_statuses = frozenset({"failed"})
        payment = self.session()
        self.past_window(payment)

        def answer(_):
            self.gateway.send_callback(payment, "failed")
            return "expired"

        with mock.patch.object(SimulatedProvider, "fetch_status", side_effect=answer):
            self.assertEqual(recheck_payment(self.now(payment), get_provider()), "superseded")
        current = self.assert_state(payment, "failed", True)
        self.assertEqual(current.provider_payload["closure"], "final_result")

    def test_paid_after_closure_is_counted_keeps_the_timestamp_and_is_logged(self):
        payment = self.session()
        self.past_window(payment)
        self.query(payment, "expired")
        closed_at = self.assert_state(payment, "expired", True).provider_closed_at
        with self.assertLogs("payments.services", "ERROR"):
            self.gateway.send_callback(payment, "paid")
        current = self.assert_state(payment, "paid", True)
        self.assertEqual(current.provider_closed_at, closed_at)
        self.assertEqual(Order.objects.get(pk=payment.order_id).amount_paid, payment.amount)


class ConflictTests(EvidenceTestCase):
    def conflict(self, payment, second):
        """The provider says paid while a final callback closes the payment, then gives `second`."""
        answers = iter(("paid", second))

        def fetch(_):
            answer = next(answers)
            if answer == "paid":
                self.gateway.send_callback(payment, "failed")
            elif isinstance(answer, Exception):
                raise answer
            return answer

        with mock.patch.object(SimulatedProvider, "fetch_status", side_effect=fetch), \
                self.assertLogs("payments.services", "ERROR"):
            return recheck_payment(self.now(payment), get_provider())

    def test_moving_to_unknown_revokes_the_evidence(self):
        for second in ("pending", RuntimeError("down"), "not-a-status"):
            with self.subTest(second=second):
                self.gateway.final_statuses = frozenset({"failed"})
                payment = self.session()
                self.assertEqual(self.conflict(payment, second), "conflict")
                current = self.assert_state(payment, "unknown", False)
                self.assertTrue(current.provider_payload["closure_revoked"])
                self.assertEqual(current.provider_payload["conflict_with"], "failed")
                self.assertEqual(open_payment(current.order), current)

    def test_a_later_result_needs_new_proof(self):
        self.gateway.final_statuses = frozenset({"failed"})
        payment = self.session()
        self.conflict(payment, "pending")
        self.gateway.final_statuses = frozenset()
        self.query(payment, "failed")
        self.assert_state(payment, "failed", False)
        self.gateway.final_statuses = frozenset({"failed"})
        self.gateway.send_callback(payment, "failed")
        self.assert_state(payment, "failed", True)

    def test_conflicting_answers_never_record_closure(self):
        self.gateway.final_statuses = frozenset(CLOSING)
        payment = self.session()
        self.past_window(payment)
        answers = iter(("paid", "expired"))

        def fetch(_):
            answer = next(answers)
            if answer == "paid":
                mark_payment(payment, "expired")  # locally, so nothing is recorded before the answers
            return answer

        with mock.patch.object(SimulatedProvider, "fetch_status", side_effect=fetch):
            self.assertEqual(recheck_payment(self.now(payment), get_provider()), "superseded")
        self.assert_state(payment, "expired", False)


class AtomicityTests(EvidenceTestCase):
    def assert_rolled_back(self, payment, work):
        with mock.patch("payments.services._close_if_proven", side_effect=RuntimeError), \
                self.captureOnCommitCallbacks() as callbacks, self.assertRaises(RuntimeError):
            work()
        self.assert_state(payment, "pending", False)
        self.assertEqual(callbacks, [])

    def test_a_pushed_result_and_its_evidence_commit_together(self):
        payment = self.session()
        self.assert_rolled_back(payment, lambda: record_provider_result(payment, "failed", get_provider()))

    def test_a_queried_result_and_its_evidence_commit_together(self):
        payment = self.session()
        self.past_window(payment)
        self.assert_rolled_back(payment, lambda: self.query(payment, "failed"))


class NothingReadsTheEvidenceTests(EvidenceTestCase):
    def outcome(self, order):
        order = Order.objects.get(pk=order.pk)
        blocking = open_payment(order)
        with transaction.atomic():
            try:
                result = request_payment(order.pk, "card").status
            except ApiError as error:
                result = error.code
            transaction.set_rollback(True)
        return blocking.pk if blocking else None, result

    def test_no_outcome_for_a_new_payment_depends_on_it(self):
        past, future = timezone.now() - timedelta(days=1), timezone.now() + timedelta(days=1)
        evidence = ({"provider_closed_at": past}, {"checkout_expires_at": past}, {"checkout_expires_at": future},
                    {"provider_closed_at": past, "checkout_expires_at": past})
        for status, _ in Payment.STATUSES:
            for created_minutes_ago in (0, 31):
                order = self.order()
                payment = Payment.objects.create(
                    order=order, kind="initial", provider="simulated", amount=order.total, currency="GEL",
                    status=status, checkout_url="https://provider.invalid/x")
                baseline.age(payment, created_minutes_ago)
                without = self.outcome(order)
                for values in evidence:
                    with self.subTest(status=status, age=created_minutes_ago, values=values):
                        Payment.objects.filter(pk=payment.pk).update(**values)
                        self.assertEqual(self.outcome(order), without)
                        Payment.objects.filter(pk=payment.pk).update(provider_closed_at=None, checkout_expires_at=None)

    def test_the_api_does_not_expose_it(self):
        payment = self.session()
        self.assertEqual(set(payment_data(payment)), {"id", "kind", "provider", "status", "amount", "currency",
                                                      "checkout_url", "paid_at", "created_at"})
        client = APIClient()
        client.force_login(self.customer.user)
        response = client.get(f"/api/orders/{payment.order.order_number}/payments/{payment.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("provider_closed_at", response.content.decode())
        self.assertNotIn("checkout_expires_at", response.content.decode())

    def test_reconciliation_does_not_poll_closed_payments(self):
        self.gateway.final_statuses = frozenset(CLOSING)
        closed, local = self.session(), self.session()
        self.gateway.send_callback(closed, "failed")
        mark_payment(local, "expired")
        self.assert_state(closed, "failed", True)
        for payment in (closed, local):
            baseline.age(payment, 60)
        baseline.run_reconcile()
        self.assertEqual(self.gateway.fetch_calls, [])

    def test_todays_stale_created_payment_is_unchanged(self):
        """S2 decides what should happen here; this only shows S1a did not change it."""
        payment = request_payment(self.order().pk, "card")
        baseline.age(payment, 31)
        self.assertIsNone(open_payment(self.now(payment).order))
        baseline.run_reconcile()
        self.assertEqual(self.gateway.fetch_calls, [])
        self.assertTrue(start_payment(self.now(payment)))
        self.assert_state(payment, "pending", False)
        self.assertIsNotNone(self.now(payment).checkout_expires_at)

    def test_only_the_expected_modules_mention_the_columns(self):
        allowed = {Path("payments/models.py"), Path("payments/services.py"),
                   Path("payments/management/commands/reconcile_closed_payments.py")}
        for path in Path(settings.BASE_DIR).rglob("*.py"):
            relative = path.relative_to(settings.BASE_DIR)
            if ("venv" in relative.parts or "migrations" in relative.parts or "tests" in relative.parts
                    or relative.name.startswith("test")):
                continue
            text = path.read_text(encoding="utf-8")
            if "provider_closed_at" in text or "checkout_expires_at" in text:
                self.assertIn(relative, allowed)

    def test_the_decisions_do_not_mention_the_columns(self):
        for function in (open_payment, request_payment):
            source = inspect.getsource(function)
            self.assertNotIn("provider_closed_at", source)
            self.assertNotIn("checkout_expires_at", source)


class TestOnlyProviderTests(TestCase):
    def test_no_runtime_module_imports_it(self):
        for path in Path(settings.BASE_DIR).rglob("*.py"):
            relative = path.relative_to(settings.BASE_DIR)
            if "venv" in relative.parts or "tests" in relative.parts or relative.name.startswith("test"):
                continue
            self.assertNotIn("payments.testing", path.read_text(encoding="utf-8"), relative)

    def test_its_callback_address_does_not_exist_outside_a_test(self):
        self.assertEqual(APIClient().post("/api/payments/simulated/callback/", {}, format="json").status_code, 404)


class FakeProviderTests(TestCase):
    def setUp(self):
        use_demo_routes(self)
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.client = APIClient()
        self.client.force_login(self.customer.user)
        self.order = make_order(self.customer, payment_method="card")

    def started(self):
        response = self.client.post(f"/api/orders/{self.order.order_number}/payments/", {"method": "card"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return Payment.objects.get(pk=response.json()["id"])

    def complete(self, payment, status):
        response = self.client.post(f"/api/payments/fake/{payment.pk}/complete/", {"status": status}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(set(response.json()), set(payment_data(payment)))
        self.assertEqual(response.json()["status"], status)
        return Payment.objects.get(pk=payment.pk)

    def test_starting_is_unchanged(self):
        payment = self.started()
        self.assertEqual((payment.provider, payment.status), ("fake", "pending"))
        self.assertTrue(payment.checkout_url.endswith(f"payment={payment.pk}&fake=1"))
        self.assertIsNone(payment.checkout_expires_at)
        self.assertIsNone(FakeProvider.checkout_lifetime)

    def test_its_simulated_closing_results_are_final(self):
        payment = self.complete(self.started(), "failed")
        self.assertIsNotNone(payment.provider_closed_at)
        self.assertEqual(payment.provider_payload["closure"], "final_result")
        self.assertTrue(payment.provider_payload["fake"])
        retry = self.complete(self.started(), "expired")
        self.assertIsNotNone(retry.provider_closed_at)

    def test_paid_needs_no_closure(self):
        self.assertIsNone(self.complete(self.started(), "paid").provider_closed_at)

    def test_a_new_payment_after_a_failed_one_is_still_allowed(self):
        self.complete(self.started(), "failed")
        self.assertEqual(self.started().status, "pending")


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL (CI)")
class ParallelEvidenceTests(TransactionTestCase):
    """Real parallel transactions, repeated. Either order is acceptable; the end state must be consistent in both."""

    serialized_rollback = True
    ROUNDS = 5
    parallel = baseline.ParallelLifecycleBaselineTests.parallel

    def setUp(self):
        self.gateway = use_simulated_provider(self)
        self.gateway.checkout_lifetime = LIFETIME
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)

    def requested(self):
        return request_payment(make_order(self.customer, payment_method="card").pk, "card")

    def started(self):
        payment = self.requested()
        start_payment(payment)
        return Payment.objects.get(pk=payment.pk)

    def past_window(self, payment):
        Payment.objects.filter(pk=payment.pk).update(
            checkout_expires_at=timezone.now() - PROVIDER_CLOSE_MARGIN - timedelta(seconds=1))
        return Payment.objects.get(pk=payment.pk)

    def test_duplicate_callbacks(self):
        self.gateway.final_statuses = frozenset(CLOSING)
        for _ in range(self.ROUNDS):
            payment = self.started()
            with mock.patch("orders.notifications.notify_after_commit") as notify:
                answers = self.parallel(*[lambda: self.gateway.send_callback(payment, "failed").status_code] * 4)
            self.assertEqual(answers, [200] * 4)
            current = Payment.objects.get(pk=payment.pk)
            self.assertEqual(current.status, "failed")
            self.assertIsNotNone(current.provider_closed_at)
            notify.assert_called_once()

    def test_a_paid_callback_against_a_closing_query(self):
        for _ in range(self.ROUNDS):
            payment = self.past_window(self.started())
            self.gateway.answer_fetch_with(payment, "expired")
            self.parallel(lambda: self.gateway.send_callback(payment, "paid").status_code,
                          lambda: recheck_payment(payment, get_provider()))
            current = Payment.objects.get(pk=payment.pk)
            self.assertEqual(current.status, "paid")
            self.assertEqual(Order.objects.get(pk=payment.order_id).amount_paid, payment.amount)

    def test_a_final_callback_against_a_closing_query(self):
        self.gateway.final_statuses = frozenset({"failed"})
        for _ in range(self.ROUNDS):
            payment = self.past_window(self.started())
            self.gateway.answer_fetch_with(payment, "expired")
            self.parallel(lambda: self.gateway.send_callback(payment, "failed").status_code,
                          lambda: recheck_payment(payment, get_provider()))
            current = Payment.objects.get(pk=payment.pk)
            self.assertIn(current.status, CLOSING)
            self.assertIsNotNone(current.provider_closed_at)

    def test_a_final_callback_against_the_start_of_the_session(self):
        self.gateway.final_statuses = frozenset({"failed"})
        for _ in range(self.ROUNDS):
            payment = self.requested()
            self.parallel(lambda: self.gateway.send_callback(payment, "failed").status_code,
                          lambda: start_payment(payment))
            current = Payment.objects.get(pk=payment.pk)
            self.assertEqual(current.status, "failed")
            self.assertEqual(bool(current.checkout_url), current.provider_closed_at is not None)
            self.assertEqual(bool(current.checkout_url), current.checkout_expires_at is not None)
