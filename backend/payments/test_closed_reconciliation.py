"""LPK-219 S1b: payments closed here are checked with the provider until there is evidence, in bounded steps."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from orders.models import Order
from orders.services import cancel_order
from payments import test_provider_evidence as evidence
from payments.management.commands import reconcile_closed_payments as job
from payments.models import Payment
from payments.providers import get_provider
from payments.services import PROVIDER_CLOSE_MARGIN, claim_closure_check, mark_payment, recheck_payment
from payments.testing import SimulatedProvider

D = Decimal
CLOSING = ("failed", "expired")


def run():
    out = StringIO()
    call_command("reconcile_closed_payments", stdout=out)
    return out.getvalue().strip()


def counts(**kwargs):
    values = {"checked": 0, "closed": 0, "paid": 0, "open": 0, "errors": 0, "unresolved": 0, "other provider": 0,
              "needs review": 0, **kwargs}
    return ", ".join(f"{name}: {value}" for name, value in values.items())


class ClosedTestCase(evidence.EvidenceTestCase):
    def closed(self, status="expired", **fields):
        payment = self.session()
        mark_payment(payment, status)
        Payment.objects.filter(pk=payment.pk).update(**fields)
        return self.now(payment)

    def due_again(self, payment):
        Payment.objects.filter(pk=payment.pk).update(closure_next_check_at=timezone.now() - timedelta(seconds=1))


class SelectionTests(ClosedTestCase):
    def test_only_closed_sessions_without_evidence_are_asked_about(self):
        wanted = [self.closed("failed"), self.closed("expired")]
        self.session()  # pending: reconcile_payments owns it
        mark_payment(self.session(), "unknown")
        mark_payment(self.session(), "paid")
        self.closed(provider_closed_at=timezone.now())
        self.gateway.start_error = True
        self.session()  # failed without a page
        self.gateway.start_error = False
        for payment in wanted:
            self.gateway.answer_fetch_with(payment, "pending")
        run()
        self.assertCountEqual(self.gateway.fetch_calls, [p.provider_order_id for p in wanted])

    def test_invoices_and_the_fake_provider_are_never_asked_about(self):
        invoice = self.closed(provider="invoice")
        fake = self.closed(provider="fake")
        self.assertIn("checked: 0", run())
        self.assertEqual(self.gateway.fetch_calls, [])
        self.assertIsNone(self.now(invoice).provider_closed_at)
        self.assertIsNone(self.now(fake).provider_closed_at)

    def test_a_run_does_a_bounded_amount_of_work_oldest_due_first(self):
        first, second, third = self.closed(), self.closed(), self.closed()
        for payment in (first, second, third):
            self.gateway.answer_fetch_with(payment, "pending")
        with mock.patch.object(job, "BATCH_SIZE", 2):
            run()
        self.assertEqual(self.gateway.fetch_calls, [first.provider_order_id, second.provider_order_id])
        self.due_again(first)
        with mock.patch.object(job, "BATCH_SIZE", 1):
            run()  # third has never been checked, so it goes before first
        self.assertEqual(self.gateway.fetch_calls[-1], third.provider_order_id)

    def test_a_payment_not_due_is_left_alone(self):
        payment = self.closed()
        self.gateway.answer_fetch_with(payment, "pending")
        run()
        run()
        self.assertEqual(self.gateway.fetch_calls, [payment.provider_order_id])


class AnswerTests(ClosedTestCase):
    def test_a_closing_answer_keeps_the_status_and_notifies_nobody(self):
        for seen in CLOSING:
            for answer in CLOSING:
                with self.subTest(seen=seen, answer=answer):
                    payment = self.closed(seen)
                    self.past_window(payment)
                    self.gateway.answer_fetch_with(payment, answer)
                    with mock.patch("orders.notifications.notify_after_commit") as notify:
                        self.assertEqual(recheck_payment(self.now(payment), get_provider()), "closed")
                    current = self.assert_state(payment, seen, True)
                    self.assertEqual(current.provider_payload["closure"], "query_after_window")
                    notify.assert_not_called()

    def test_a_closing_answer_inside_the_window_proves_nothing_and_stays_due_later(self):
        payment = self.closed("expired")
        self.gateway.answer_fetch_with(payment, "failed")
        self.assertEqual(recheck_payment(self.now(payment), get_provider()), "unproven")
        self.assert_state(payment, "expired", False)

    def test_a_declared_final_status_proves_it_only_for_that_status(self):
        self.gateway.final_statuses = frozenset({"expired"})
        final, not_final = self.closed("failed"), self.closed("failed")
        self.gateway.answer_fetch_with(final, "expired")
        self.gateway.answer_fetch_with(not_final, "failed")
        run()
        self.assert_state(final, "failed", True)
        self.assert_state(not_final, "failed", False)

    def test_an_unknown_expiry_stays_unresolved(self):
        self.gateway.checkout_lifetime = None
        payment = self.closed()
        self.gateway.answer_fetch_with(payment, "expired")
        self.assertEqual(run(), counts(checked=1, unresolved=1))
        self.assert_state(payment, "expired", False)

    def test_a_late_paid_answer_is_counted(self):
        for seen in CLOSING:
            with self.subTest(seen=seen):
                payment = self.closed(seen)
                self.gateway.answer_fetch_with(payment, "paid")
                with self.assertLogs("payments.management.commands.reconcile_closed_payments", "ERROR"):
                    run()
                self.assert_state(payment, "paid", False)
                self.assertEqual(Order.objects.get(pk=payment.order_id).amount_paid, payment.amount)

    def test_a_late_paid_answer_on_a_cancelled_order_is_owed_back(self):
        payment = self.session()
        cancel_order(payment.order_id, "x", actor="operator", operator=True)
        self.gateway.answer_fetch_with(payment, "paid")
        with self.assertLogs("payments.management.commands.reconcile_closed_payments", "ERROR"):
            self.assertEqual(run(), counts(checked=1, paid=1, **{"needs review": 1}))
        order = Order.objects.get(pk=payment.order_id)
        self.assertEqual((order.status, order.amount_paid), (Order.STATUS_CANCELLED, payment.amount))
        self.assertEqual(order.balance_due, -payment.amount)

    def test_a_session_still_open_at_the_provider_changes_nothing(self):
        payment = self.closed()
        self.gateway.answer_fetch_with(payment, "pending")
        with self.assertLogs("payments.management.commands.reconcile_closed_payments", "WARNING"):
            self.assertEqual(run(), counts(checked=1, open=1, unresolved=1))
        self.assert_state(payment, "expired", False)

    def test_provider_errors_and_invalid_answers_change_nothing_and_stay_scheduled(self):
        for answer in (RuntimeError("secret-token"), "not-a-status"):
            with self.subTest(answer=answer):
                payment = self.closed()
                self.gateway.answer_fetch_with(payment, answer)
                with self.assertLogs("payments.management.commands.reconcile_closed_payments", "ERROR") as logs:
                    self.assertIn("errors: 1", run())
                self.assertNotIn("secret-token", logs.output[0])
                current = self.assert_state(payment, "expired", False)
                self.assertEqual(current.closure_checks, 1)
                self.assertGreater(current.closure_next_check_at, timezone.now())

    def test_a_result_arriving_during_the_query_wins(self):
        payment = self.closed("expired")
        self.past_window(payment)

        def answer(_):
            self.gateway.send_callback(payment, "paid")
            return "failed"

        with mock.patch.object(SimulatedProvider, "fetch_status", side_effect=answer):
            self.assertEqual(recheck_payment(self.now(payment), get_provider()), "superseded")
        self.assert_state(payment, "paid", False)

    def test_a_status_change_during_the_query_drops_the_answer(self):
        payment = self.closed("expired")
        self.past_window(payment)

        def answer(_):
            self.gateway.send_callback(payment, "failed")
            return "expired"

        with mock.patch.object(SimulatedProvider, "fetch_status", side_effect=answer):
            self.assertEqual(recheck_payment(self.now(payment), get_provider()), "superseded")
        self.assert_state(payment, "failed", False)

    def test_paid_then_unconfirmed_on_a_row_that_changed_is_held_as_unknown(self):
        payment = self.closed("expired")
        answers = iter(("paid", "pending"))

        def answer(_):
            value = next(answers)
            if value == "paid":
                self.gateway.send_callback(payment, "failed")
            return value

        with mock.patch.object(SimulatedProvider, "fetch_status", side_effect=answer), \
                self.assertLogs("payments.services", "ERROR"):
            self.assertIn("errors: 1", run())
        self.assert_state(payment, "unknown", False)


    def test_conflicting_answers_that_end_closed_are_not_counted_as_paid(self):
        payment = self.closed("expired")
        answers = iter(("paid", "expired"))

        def answer(_):
            value = next(answers)
            if value == "paid":
                self.gateway.send_callback(payment, "failed")
            return value

        with mock.patch.object(SimulatedProvider, "fetch_status", side_effect=answer):
            self.assertEqual(run(), counts(checked=1, unresolved=1))
        self.assert_state(payment, "expired", False)


class ScheduleTests(ClosedTestCase):
    def test_checks_back_off_to_one_a_day_and_never_stop(self):
        self.gateway.checkout_lifetime = None
        payment = self.closed()
        gaps = []
        for _ in range(9):
            self.due_again(payment)
            self.gateway.answer_fetch_with(payment, "pending")
            before = timezone.now()
            run()
            gaps.append(round((self.now(payment).closure_next_check_at - before).total_seconds() / 60))
        self.assertEqual(gaps, [15, 60, 180, 360, 720, 1440, 1440, 1440, 1440])
        self.assertEqual(self.now(payment).closure_checks, 9)

    def test_the_check_after_the_window_comes_on_time(self):
        payment = self.closed()
        now = timezone.now()
        Payment.objects.filter(pk=payment.pk).update(checkout_expires_at=now - PROVIDER_CLOSE_MARGIN + timedelta(minutes=5))
        self.assertTrue(claim_closure_check(self.now(payment), now))
        self.assertEqual(self.now(payment).closure_next_check_at, now + timedelta(minutes=5))

    def test_an_overlapping_run_or_a_stale_row_loses_the_claim(self):
        payment = self.closed()
        first, second = self.now(payment), self.now(payment)
        self.assertTrue(claim_closure_check(first, timezone.now()))
        self.assertFalse(claim_closure_check(second, timezone.now()))
        self.assertEqual(self.now(payment).closure_checks, 1)

    def test_a_crash_during_the_query_has_already_spent_the_check(self):
        payment = self.closed()
        with mock.patch.object(SimulatedProvider, "fetch_status", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            run()
        self.assertEqual(self.now(payment).closure_checks, 1)
        self.assertIsNotNone(self.now(payment).closure_next_check_at)
        self.assertIn("checked: 0", run())

    def test_an_outage_recovers_by_itself(self):
        payment = self.closed()
        self.past_window(payment)
        Payment.objects.filter(pk=payment.pk).update(closure_checks=40)
        self.gateway.answer_fetch_with(payment, "expired")
        self.assertIn("closed: 1", run())
        self.assert_state(payment, "expired", True)


    def test_an_outage_stops_the_run_and_leaves_the_other_checks_unspent(self):
        payments = [self.closed() for _ in range(4)]
        with mock.patch.object(job, "STOP_AFTER_ERRORS_IN_A_ROW", 2), \
                mock.patch.object(SimulatedProvider, "fetch_status", side_effect=RuntimeError), \
                self.assertLogs("payments.management.commands.reconcile_closed_payments", "ERROR"):
            self.assertIn("errors: 2", run())
        self.assertEqual([self.now(p).closure_checks for p in payments], [1, 1, 0, 0])

    def test_a_good_answer_resets_the_error_count(self):
        payments = [self.closed() for _ in range(3)]
        answers = iter((RuntimeError(), "pending", RuntimeError()))

        def answer(_):
            value = next(answers)
            if isinstance(value, Exception):
                raise value
            return value

        with mock.patch.object(job, "STOP_AFTER_ERRORS_IN_A_ROW", 2), \
                mock.patch.object(SimulatedProvider, "fetch_status", side_effect=answer), \
                self.assertLogs("payments.management.commands.reconcile_closed_payments", "ERROR"):
            run()
        self.assertEqual([self.now(p).closure_checks for p in payments], [1, 1, 1])

    def test_a_slow_run_stops_and_leaves_the_rest_due(self):
        first, second = self.closed(), self.closed()
        self.gateway.answer_fetch_with(first, "pending")
        with mock.patch.object(job, "RUN_SECONDS", 10), mock.patch.object(job.time, "monotonic", side_effect=[0, 0, 11]), \
                self.assertLogs("payments.management.commands.reconcile_closed_payments", "ERROR"):
            run()
        self.assertEqual((self.now(first).closure_checks, self.now(second).closure_checks), (1, 0))


class AlertTests(ClosedTestCase):
    def test_overdue_payments_are_reported_on_every_run(self):
        payment = self.closed(closure_checks=8, closure_next_check_at=timezone.now() + timedelta(days=1))
        for _ in range(2):
            with self.assertLogs("payments.management.commands.reconcile_closed_payments", "ERROR") as logs:
                self.assertEqual(run(), counts(unresolved=1))
            self.assertIn(str(payment.pk), logs.output[0])

    def test_payments_of_another_provider_are_not_hidden(self):
        for status in ("expired", "pending", "unknown"):
            payment = self.session()
            Payment.objects.filter(pk=payment.pk).update(provider="previous", status=status)
        with self.assertLogs("payments.management.commands.reconcile_closed_payments", "ERROR") as logs:
            self.assertEqual(run(), counts(**{"other provider": 3}))
        self.assertIn("3 unresolved", logs.output[0])
        self.assertEqual(self.gateway.fetch_calls, [])


class OperatorRecheckTests(ClosedTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))

    def recheck(self, payment):
        response = self.client.post("/admin/payments/payment/", {"action": "recheck_with_provider",
                                                                 "_selected_action": [payment.pk]}, follow=True)
        self.assertEqual(response.status_code, 200)
        return [str(m) for m in response.context["messages"]]

    @override_settings(LANGUAGE_CODE="en")
    def test_an_operator_can_ask_about_an_unresolved_session(self):
        payment = self.closed()
        self.past_window(payment)
        self.gateway.answer_fetch_with(payment, "expired", "pending")
        self.assertIn("confirmed that the payment session is closed", self.recheck(payment)[0])
        self.assert_state(payment, "expired", True)
        self.assertIn("can be re-checked", self.recheck(payment)[0])  # resolved: nothing left to ask

    @override_settings(LANGUAGE_CODE="en")
    def test_without_proof_the_operator_is_told_so(self):
        payment = self.closed()
        self.gateway.answer_fetch_with(payment, "expired")
        self.assertIn("not yet enough proof", self.recheck(payment)[0])
        self.assert_state(payment, "expired", False)


class LockTests(ClosedTestCase):
    def test_a_closing_answer_locks_only_the_payment(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        payment = self.closed()
        self.past_window(payment)
        self.gateway.answer_fetch_with(payment, "expired")
        with CaptureQueriesContext(connection) as queries:
            recheck_payment(self.now(payment), get_provider())
        self.assertFalse(any('"orders_order"' in q["sql"] and "FOR" in q["sql"] for q in queries.captured_queries))
