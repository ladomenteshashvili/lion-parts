"""LP-BE-706: reconcile_payments (G§7.10)."""

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from accounts.testing import make_customer
from orders.services import cancel_order
from orders.testing import make_order
from payments.models import Payment
from payments.providers import FakeProvider
from payments.services import mark_payment, open_payment


def run():
    out = StringIO()
    call_command("reconcile_payments", stdout=out)
    return out.getvalue().strip()


class ReconcileTests(TestCase):
    def setUp(self):
        self.order = make_order(make_customer())

    def payment(self, minutes, provider="fake", status="pending"):
        payment = Payment.objects.create(order=self.order, kind="initial", provider=provider, amount=self.order.total,
                                         currency="GEL", status=status)
        Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - timedelta(minutes=minutes))
        return payment

    def statuses(self, *payments):
        return [Payment.objects.get(pk=p.pk).status for p in payments]

    def test_old_pending_payment_reported_paid_becomes_paid_once(self):
        old, fresh = self.payment(5), self.payment(1)
        with mock.patch.object(FakeProvider, "fetch_status", return_value="paid") as fetch:
            self.assertEqual(run(), "updated: 1, unknown: 0, errors: 0")
            self.assertEqual(fetch.call_count, 1)  # the 1-minute-old payment is left alone
            self.assertEqual(self.statuses(old, fresh), ["paid", "pending"])
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, "processing")
            self.assertEqual(run(), "updated: 0, unknown: 0, errors: 0")  # second run changes nothing

    def test_still_pending_after_30_minutes_becomes_unknown(self):
        recent, stale = self.payment(10), self.payment(31)
        with self.assertLogs("payments.management.commands.reconcile_payments", "WARNING"):
            self.assertEqual(run(), "updated: 0, unknown: 1, errors: 0")
        self.assertEqual(self.statuses(recent, stale), ["pending", "unknown"])
        self.assertEqual(run(), "updated: 0, unknown: 0, errors: 0")

    def test_one_failure_does_not_stop_the_run_and_bad_statuses_are_ignored(self):
        broken, good, odd = self.payment(5), self.payment(5), self.payment(5)
        answers = {broken.pk: RuntimeError("timeout"), good.pk: "failed", odd.pk: "created"}

        def fetch(payment):
            answer = answers[payment.pk]
            if isinstance(answer, Exception):
                raise answer
            return answer

        with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch), \
                self.assertLogs("payments.management.commands.reconcile_payments", "ERROR"):
            self.assertEqual(run(), "updated: 1, unknown: 0, errors: 2")
        self.assertEqual(self.statuses(broken, good, odd), ["pending", "failed", "pending"])

    def test_a_result_arriving_during_the_provider_call_is_not_overwritten_with_unknown(self):
        for result in ("failed", "expired"):
            with self.subTest(result=result):
                stale = self.payment(31)

                def fetch(payment, stale=stale, result=result):
                    mark_payment(stale, result, payload={"callback": True})  # lands while the job waits
                    return "pending"

                with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch), \
                        self.assertNoLogs("payments.management.commands.reconcile_payments", "WARNING"):
                    self.assertEqual(run(), "updated: 0, unknown: 0, errors: 0")
                self.assertEqual(self.statuses(stale), [result])
                self.assertIsNone(open_payment(self.order))

    def test_invoice_payments_are_not_reconciled(self):
        invoice = self.payment(60, provider="invoice")
        run()
        self.assertEqual(self.statuses(invoice), ["pending"])

    def test_unknown_becomes_the_final_answer_from_the_provider(self):
        for answer in ("paid", "failed", "expired"):
            with self.subTest(answer=answer):
                unknown = self.payment(120, status="unknown")
                with mock.patch.object(FakeProvider, "fetch_status", return_value=answer):
                    self.assertEqual(run(), "updated: 1, unknown: 0, errors: 0")
                    self.assertEqual(run(), "updated: 0, unknown: 0, errors: 0")  # nothing left to change
                unknown.refresh_from_db()
                self.assertEqual((unknown.status, unknown.provider_payload), (answer, {"reconciled": answer}))
                if answer == "paid":
                    self.order.refresh_from_db()
                    self.assertEqual((self.order.status, self.order.amount_paid), ("processing", unknown.amount))
                    self.order = make_order(self.order.customer)
                else:
                    self.assertIsNone(open_payment(self.order))

    def test_unknown_stays_unknown_while_the_provider_says_pending_however_old(self):
        unknown = self.payment(30 * 24 * 60, status="unknown")
        with mock.patch.object(FakeProvider, "fetch_status", return_value="pending") as fetch:
            self.assertEqual(run(), "updated: 0, unknown: 0, errors: 0")
            self.assertEqual(run(), "updated: 0, unknown: 0, errors: 0")
        self.assertEqual((fetch.call_count, self.statuses(unknown)), (2, ["unknown"]))
        self.assertEqual(open_payment(self.order), unknown)

    def test_a_timeout_or_invalid_answer_changes_nothing_and_the_run_continues(self):
        bad = [(TimeoutError("timed out"), "pending"), (TimeoutError("timed out"), "unknown"),
               ("created", "pending"), ("created", "unknown"), ("unknown", "unknown"), ("refunded", "unknown"),
               ("refunded", "pending"), (None, "unknown")]
        for answer, status in bad:
            with self.subTest(answer=answer, status=status):
                payment, good = self.payment(120, status=status), self.payment(120, status="unknown")
                answers = {payment.pk: answer, good.pk: "failed"}

                def fetch(p, answers=answers):
                    if isinstance(answers[p.pk], Exception):
                        raise answers[p.pk]
                    return answers[p.pk]

                with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch), self.assertLogs(
                        "payments.management.commands.reconcile_payments", "ERROR"):
                    self.assertEqual(run(), "updated: 1, unknown: 0, errors: 1")
                self.assertEqual(self.statuses(payment, good), [status, "failed"])
                payment.delete()

    def test_provider_errors_are_logged_without_their_text_or_a_traceback(self):
        unknown = self.payment(120, status="unknown")
        error = RuntimeError("https://provider.example/?token=SECRET-TOKEN")
        with mock.patch.object(FakeProvider, "fetch_status", side_effect=error), self.assertLogs(
                "payments.management.commands.reconcile_payments", "ERROR") as logs:
            run()
        self.assertEqual(logs.records[0].getMessage(), f"Reconciling payment {unknown.pk} failed: RuntimeError")
        self.assertIsNone(logs.records[0].exc_info)

    def test_invalid_answers_are_not_logged_either(self):
        self.payment(120, status="unknown")
        with mock.patch.object(FakeProvider, "fetch_status", return_value="SECRET-TOKEN"), self.assertLogs(
                "payments.management.commands.reconcile_payments", "ERROR") as logs:
            run()
        self.assertNotIn("SECRET-TOKEN", logs.output[0])

    def test_pending_over_30_minutes_and_unknown_in_one_run(self):
        pending, unknown, fresh = self.payment(31), self.payment(31, status="unknown"), self.payment(1)
        with self.assertLogs("payments.management.commands.reconcile_payments", "WARNING"):
            self.assertEqual(run(), "updated: 0, unknown: 1, errors: 0")  # the fake provider has no answer
        self.assertEqual(self.statuses(pending, unknown, fresh), ["unknown", "unknown", "pending"])

    def test_a_payment_of_another_provider_is_never_asked_or_changed(self):
        unknown = self.payment(120, provider="oldprovider", status="unknown")
        pending = self.payment(120, provider="oldprovider")
        with mock.patch.object(FakeProvider, "fetch_status", return_value="paid") as fetch:
            self.assertEqual(run(), "updated: 0, unknown: 0, errors: 0")
        fetch.assert_not_called()
        self.assertEqual(self.statuses(unknown, pending), ["unknown", "pending"])

    def race(self, payment, callback, *answers):
        """The callback lands during the first provider call; each call then gets the next answer."""
        remaining = list(answers)

        def fetch(_):
            if len(remaining) == len(answers):
                mark_payment(payment, callback, payload={"callback": True})
            answer = remaining.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        return mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch)

    def races(self):
        for status in ("pending", "unknown"):
            for callback in ("failed", "expired"):
                self.order = make_order(self.order.customer)
                payment = self.payment(120, status=status)
                yield payment, callback
                payment.delete()  # an unknown one would be polled again in the next case

    def test_a_stale_failure_never_overwrites_a_newer_callback_and_no_repeat_query_is_needed(self):
        for status in ("pending", "unknown"):
            for answer, callback in (("expired", "failed"), ("failed", "expired")):
                with self.subTest(status=status, answer=answer, callback=callback):
                    self.order = make_order(self.order.customer)
                    payment = self.payment(120, status=status)
                    with self.race(payment, callback, answer) as fetch:
                        self.assertEqual(run(), "updated: 0, unknown: 0, errors: 0")
                    payment.refresh_from_db()
                    self.assertEqual((payment.status, payment.provider_payload, fetch.call_count),
                                     (callback, {"callback": True}, 1))

    def test_a_paid_callback_is_never_downgraded_by_a_stale_failure(self):
        for status in ("pending", "unknown"):
            for answer in ("failed", "expired"):
                with self.subTest(status=status, answer=answer):
                    self.order = make_order(self.order.customer)
                    payment = self.payment(120, status=status)
                    with self.race(payment, "paid", answer) as fetch:
                        self.assertEqual(run(), "updated: 0, unknown: 0, errors: 0")
                    payment.refresh_from_db()
                    self.order.refresh_from_db()
                    self.assertEqual((payment.status, self.order.amount_paid, fetch.call_count),
                                     ("paid", payment.amount, 1))

    def test_paid_confirmed_by_a_repeat_query_beats_a_closing_callback(self):
        for payment, callback in self.races():
            with self.race(payment, callback, "paid", "paid") as fetch, self.assertLogs("payments.services", "WARNING"):
                self.assertEqual(run(), "updated: 1, unknown: 0, errors: 0")
                self.assertEqual(run(), "updated: 0, unknown: 0, errors: 0")  # idempotent: not polled again
            payment.refresh_from_db()
            self.order.refresh_from_db()
            self.assertEqual((payment.status, fetch.call_count), ("paid", 2))
            self.assertEqual(payment.provider_payload, {"callback": True, "reconciled": "paid", "conflict_with": callback,
                                                        "conflict_second_answer": "paid"})
            self.assertEqual((self.order.status, self.order.amount_paid), ("processing", payment.amount))
            self.assertEqual(self.order.balance_due, 0)  # nothing_to_pay: the customer cannot pay again

    def test_paid_then_a_final_answer_gives_that_final_status(self):
        for second in ("failed", "expired"):
            for payment, callback in self.races():
                changes = second != callback
                with self.race(payment, callback, "paid", second), self.assertLogs("payments.services", "WARNING"):
                    self.assertEqual(run(), f"updated: {int(changes)}, unknown: 0, errors: 0")
                    self.assertEqual(run(), "updated: 0, unknown: 0, errors: 0")
                payment.refresh_from_db()
                self.order.refresh_from_db()
                self.assertEqual((payment.status, self.order.amount_paid), (second, 0))
                self.assertIs(payment.provider_payload["callback"], True)
                if changes:
                    self.assertEqual((payment.provider_payload["conflict_with"],
                                      payment.provider_payload["conflict_second_answer"]), (callback, second))

    def test_an_unconfirmed_paid_answer_moves_the_payment_to_unknown_with_an_audit_record(self):
        cases = (("pending", "pending"), ("refunded", "invalid"), ("created", "invalid"), ("SECRET-TOKEN", "invalid"),
                 (None, "invalid"), (TimeoutError("?token=SECRET-TOKEN"), "error"))
        for second, label in cases:
            with self.subTest(second=second):
                for payment, callback in self.races():
                    with self.race(payment, callback, "paid", second), \
                            self.assertLogs("payments.services", "WARNING") as logs:
                        self.assertEqual(run(), "updated: 0, unknown: 0, errors: 1")
                    payment.refresh_from_db()
                    self.order.refresh_from_db()
                    self.assertEqual((payment.status, self.order.amount_paid), ("unknown", 0))
                    self.assertEqual(payment.provider_payload, {
                        "callback": True, "reconciled": "unknown", "conflict_with": callback,
                        "conflict_second_answer": label})
                    self.assertEqual(open_payment(self.order), payment)
                    self.assertTrue(any("provider says paid" in line for line in logs.output))
                    self.assertNotIn("SECRET-TOKEN", "".join(logs.output) + str(payment.provider_payload))

    def test_a_conflict_is_idempotent_and_resolves_when_the_provider_answers(self):
        for answer in ("paid", "failed", "expired"):
            with self.subTest(answer=answer):
                self.order = make_order(self.order.customer)
                payment = self.payment(120)
                with self.race(payment, "failed", "paid", "pending"), self.assertLogs("payments.services", "ERROR"):
                    run()
                with mock.patch.object(FakeProvider, "fetch_status", return_value="pending"):
                    self.assertEqual(run(), "updated: 0, unknown: 0, errors: 0")
                    self.assertEqual(run(), "updated: 0, unknown: 0, errors: 0")
                self.assertEqual(self.statuses(payment), ["unknown"])
                with mock.patch.object(FakeProvider, "fetch_status", return_value=answer):
                    self.assertEqual(run(), "updated: 1, unknown: 0, errors: 0")
                payment.refresh_from_db()
                self.assertEqual((payment.status, payment.provider_payload["conflict_with"]), (answer, "failed"))

    def test_a_result_arriving_during_the_repeat_query_is_never_overwritten(self):
        cases = (("paid", "paid", "updated: 0, unknown: 0, errors: 0"),
                 ("paid", "pending", "updated: 0, unknown: 0, errors: 0"),
                 ("paid", "failed", "updated: 0, unknown: 0, errors: 0"),
                 ("paid", TimeoutError("down"), "updated: 0, unknown: 0, errors: 0"),
                 ("refunded", "paid", "updated: 0, unknown: 0, errors: 0"),
                 ("refunded", "pending", "updated: 0, unknown: 0, errors: 0"),
                 ("refunded", "expired", "updated: 0, unknown: 0, errors: 0"),
                 ("refunded", TimeoutError("down"), "updated: 0, unknown: 0, errors: 0"),
                 ("expired", "failed", "updated: 0, unknown: 0, errors: 0"),
                 ("expired", "expired", "updated: 0, unknown: 0, errors: 0"))
        for newest, second, output in cases:
            with self.subTest(newest=newest, second=second):
                self.order = make_order(self.order.customer)
                payment = self.payment(120)
                answers = ["paid", second]

                def fetch(_, payment=payment, newest=newest, answers=answers):
                    if len(answers) == 2:
                        mark_payment(payment, "failed", payload={"callback": True})
                    else:
                        if newest == "refunded":
                            mark_payment(payment, "paid")
                        mark_payment(payment, newest, payload={"newest": True})
                    answer = answers.pop(0)
                    if isinstance(answer, Exception):
                        raise answer
                    return answer

                with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch):
                    self.assertEqual(run(), output)
                self.assertEqual(self.statuses(payment), [newest])

    def test_a_third_result_with_unresolved_evidence_of_payment_is_held_as_unknown(self):
        cases = (("failed", "expired", "paid", "paid"), ("expired", "failed", "paid", "paid"),
                 ("failed", "expired", "pending", "pending"), ("expired", "failed", "pending", "pending"),
                 ("failed", "expired", "bad", "invalid"), ("failed", "expired", TimeoutError("down"), "error"))
        for first_close, second_close, second, label in cases:
            with self.subTest(first_close=first_close, second_close=second_close, second=second):
                self.order = make_order(self.order.customer)
                payment = self.payment(120)
                answers = ["paid", second]

                def fetch(_, payment=payment, closes=[first_close, second_close], answers=answers):
                    mark_payment(payment, closes.pop(0), payload={"callback": True})
                    answer = answers.pop(0)
                    if isinstance(answer, Exception):
                        raise answer
                    return answer

                with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch), \
                        self.assertLogs("payments.services", "ERROR"):
                    self.assertEqual(run(), "updated: 0, unknown: 0, errors: 1")
                payment.refresh_from_db()
                self.order.refresh_from_db()
                self.assertEqual((payment.status, self.order.amount_paid), ("unknown", 0))
                self.assertEqual(payment.provider_payload, {
                    "callback": True, "reconciled": "unknown", "conflict_with": second_close,
                    "conflict_second_answer": label})
                self.assertEqual(open_payment(self.order), payment)
                with mock.patch.object(FakeProvider, "fetch_status", return_value="failed"):
                    self.assertEqual(run(), "updated: 1, unknown: 0, errors: 0")  # settled once the provider answers
                self.assertEqual(self.statuses(payment), ["failed"])

    def test_a_third_closing_result_the_provider_agrees_with_stays_and_allows_a_retry(self):
        for second in ("failed", "expired"):
            with self.subTest(second=second):
                self.order = make_order(self.order.customer)
                payment = self.payment(120)
                answers = ["paid", second]

                def fetch(_, payment=payment, closes=["failed", "expired"], answers=answers):
                    mark_payment(payment, closes.pop(0), payload={"callback": True})
                    return answers.pop(0)

                with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch):
                    self.assertEqual(run(), "updated: 0, unknown: 0, errors: 0")
                self.assertEqual(self.statuses(payment), ["expired"])  # the newest callback, not the provider's answer
                self.assertIsNone(open_payment(self.order))

    def test_paid_on_an_order_cancelled_during_the_call_is_owed_back(self):
        payment = self.payment(120)
        total = self.order.total

        def fetch(_):
            if Payment.objects.get(pk=payment.pk).status == "pending":
                cancel_order(self.order.pk, "Changed my mind", actor="customer")  # expires the payment
            return "paid"

        with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch), \
                self.assertLogs("payments.services", "WARNING"):
            self.assertEqual(run(), "updated: 1, unknown: 0, errors: 0")
        self.order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual((payment.status, self.order.status, self.order.amount_paid, self.order.balance_due),
                         ("paid", "cancelled", total, -total))

    def test_a_refunded_payment_stays_refunded(self):
        payment = self.payment(120)

        def fetch(_):
            mark_payment(payment, "paid")
            mark_payment(payment, "refunded")
            return "paid"

        with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch) as fetched:
            self.assertEqual(run(), "updated: 0, unknown: 0, errors: 0")
        self.assertEqual((self.statuses(payment), fetched.call_count), (["refunded"], 1))

    def test_every_provider_call_is_outside_a_transaction(self):
        payment = self.payment(120)
        baseline, depths = len(connection.atomic_blocks), []

        def fetch(_):
            depths.append(len(connection.atomic_blocks))
            mark_payment(payment, "failed")
            return "paid"

        with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch), \
                self.assertLogs("payments.services", "WARNING"):
            run()
        self.assertEqual(depths, [baseline, baseline])

    def test_the_provider_is_never_called_inside_a_transaction(self):
        self.payment(120, status="unknown")
        baseline, seen = len(connection.atomic_blocks), {}

        def fetch(p):
            seen["depth"] = len(connection.atomic_blocks)
            return "failed"

        with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch):
            run()
        self.assertEqual(seen["depth"], baseline)

    def test_the_fake_provider_has_no_answer_for_an_unknown_payment(self):
        self.assertEqual(FakeProvider().fetch_status(self.payment(120, status="unknown")), "pending")
