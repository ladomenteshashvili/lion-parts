"""M3 audit fix 1, LPK-179: no duplicate collection of the same amount (G§7.10).
A pending or unknown payment blocks new payments whatever its age; operators confirm exactly the
payment they pick, never more than the order still owes."""

import threading
import unittest
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import make_customer
from orders.models import Order, OrderItemEvent
from orders.services import cancel_order
from orders.testing import make_order
from payments.models import Payment
from payments.providers import FakeProvider
from payments.services import (confirm_manual_payment, create_payment, mark_payment, recheck_payment, record_refund,
                               start_payment)

D = Decimal
PAYMENTS = "/admin/payments/payment/"
ORDERS = "/admin/orders/order/"


def age(payment, minutes):
    Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - timedelta(minutes=minutes))


class DuplicateRequestTests(TestCase):
    def setUp(self):
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.client = APIClient()
        self.client.force_login(self.customer.user)
        self.order = make_order(self.customer)  # 260.00, full payment

    def pay(self, method):
        return self.client.post(f"/api/orders/{self.order.order_number}/payments/", {"method": method}, format="json")

    def test_a_stale_pending_invoice_still_blocks_a_second_invoice_and_a_card_payment(self):
        invoice = create_payment(self.order, "invoice")
        age(invoice, 24 * 60)
        for method in ("invoice", "card"):
            response = self.pay(method)
            self.assertEqual((response.status_code, response.json()["code"], response.json()["payment"]["id"]),
                             (409, "payment_in_progress", invoice.pk))
        self.assertEqual(self.order.payments.count(), 1)

    def payment(self, status, provider="fake", minutes=0):
        payment = Payment.objects.create(order=self.order, kind="initial", provider=provider, amount=self.order.total,
                                         currency="GEL", status=status)
        age(payment, minutes)
        return payment

    def assert_blocked_by(self, blocking):
        count = self.order.payments.count()
        for method in ("card", "invoice"):
            with self.subTest(method=method):
                response = self.pay(method)
                body = response.json()
                self.assertEqual((response.status_code, body["code"], body["payment"]["id"], body["payment"]["status"]),
                                 (409, "payment_in_progress", blocking.pk, blocking.status))
        self.assertEqual(self.order.payments.count(), count)

    def test_an_unknown_card_payment_blocks(self):
        self.assert_blocked_by(self.payment("unknown"))

    def test_an_unknown_payment_blocks_whatever_its_age_or_provider(self):
        for provider in ("fake", "invoice", "manual"):
            with self.subTest(provider=provider):
                unknown = self.payment("unknown", provider, minutes=24 * 60)
                self.assert_blocked_by(unknown)
                unknown.delete()

    def test_a_pending_card_payment_blocks_whatever_its_age(self):
        for minutes in (0, 31, 45, 24 * 60):
            with self.subTest(minutes=minutes):
                pending = self.payment("pending", minutes=minutes)
                self.assert_blocked_by(pending)
                pending.delete()

    def test_a_failed_or_expired_card_payment_allows_a_retry(self):
        for status in ("failed", "expired"):
            for minutes in (0, 45):
                with self.subTest(status=status, minutes=minutes):
                    self.payment(status, minutes=minutes)
                    self.assertEqual(self.pay("card").status_code, 201)
                    self.order.payments.all().delete()  # the retry itself is pending and would block the next case

    def test_a_created_card_payment_blocks_for_30_minutes_only(self):
        created = self.payment("created", minutes=29)
        self.assert_blocked_by(created)
        age(created, 31)
        self.assertEqual(self.pay("card").status_code, 201)

    def test_a_created_invoice_or_manual_payment_blocks_whatever_its_age(self):
        for provider in ("invoice", "manual"):
            with self.subTest(provider=provider):
                created = self.payment("created", provider, minutes=24 * 60)
                self.assert_blocked_by(created)
                created.delete()

    def test_a_card_payment_still_being_started_blocks(self):
        starting = create_payment(self.order, "card")  # status created until the provider call returns
        self.assertEqual(starting.status, "created")
        response = self.pay("card")
        self.assertEqual((response.status_code, response.json()["payment"]["id"]), (409, starting.pk))

    def test_a_cancelled_invoice_unblocks_the_customer(self):
        invoice = create_payment(self.order, "invoice")
        mark_payment(invoice, "expired")  # admin "Cancel invoice"
        body = self.pay("card").json()
        self.assertEqual((body["kind"], body["amount"]), ("initial", "260.00"))

    def third_result(self, payment, second):
        answers = ["paid", second]
        closes = ["failed", "expired"]

        def fetch(_):
            mark_payment(payment, closes.pop(0), payload={"callback": True})
            return answers.pop(0)

        return mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch)

    def test_a_third_result_with_unresolved_evidence_blocks_card_and_invoice_requests(self):
        payment = self.payment("pending", minutes=120)
        with self.third_result(payment, "paid"), self.assertLogs("payments.services", "ERROR"):
            self.assertEqual(recheck_payment(payment, FakeProvider()), "conflict")
        payment.refresh_from_db()
        self.assertEqual(payment.status, "unknown")
        self.assert_blocked_by(payment)

    def test_a_third_result_the_provider_agrees_with_allows_a_retry(self):
        payment = self.payment("pending", minutes=120)
        with self.third_result(payment, "failed"):
            self.assertEqual(recheck_payment(payment, FakeProvider()), "superseded")
        payment.refresh_from_db()
        self.assertEqual(payment.status, "expired")
        self.assertEqual(self.pay("card").status_code, 201)

    def test_a_provider_conflict_blocks_card_and_invoice_requests(self):
        for callback, second in (("failed", "pending"), ("expired", TimeoutError("down")), ("failed", "bad")):
            with self.subTest(callback=callback, second=second):
                self.order.payments.all().delete()
                payment = self.payment("pending", minutes=120)
                answers = ["paid", second]

                def fetch(_, payment=payment, callback=callback, answers=answers):
                    if len(answers) == 2:
                        mark_payment(payment, callback, payload={"callback": True})
                    answer = answers.pop(0)
                    if isinstance(answer, Exception):
                        raise answer
                    return answer

                with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch), \
                        self.assertLogs("payments.services", "ERROR"):
                    self.assertEqual(recheck_payment(payment, FakeProvider()), "conflict")
                payment.refresh_from_db()
                self.assertEqual(payment.status, "unknown")
                self.assert_blocked_by(payment)


class SecondPaymentTests(TestCase):
    """LPK-180, LPK-219: a retry exists when the earlier payment turns out paid. Money stays accounted for."""

    def setUp(self):
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.client = APIClient()
        self.client.force_login(self.customer.user)
        self.order = make_order(self.customer)  # 260.00, full payment
        self.first = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=self.order.total,
                                            currency="GEL", status="failed")

    def pay(self, method):
        return self.client.post(f"/api/orders/{self.order.order_number}/payments/", {"method": method}, format="json")

    def retry(self, method="card"):
        return Payment.objects.get(pk=self.pay(method).json()["id"])

    def order_now(self):
        return Order.objects.get(pk=self.order.pk)

    def test_a_retry_stays_pending_but_loses_its_link_when_the_first_turns_out_paid(self):
        retry = self.retry()
        self.assertEqual(retry.status, "pending")
        mark_payment(self.first, "paid", payload={"callback": True})
        body = self.client.get(f"/api/orders/{self.order.order_number}/payments/{retry.pk}/").json()
        self.assertEqual(body["status"], "pending")
        self.assertIsNone(body["checkout_url"])
        self.assertEqual((self.order_now().status, self.order_now().balance_due), ("processing", D("0.00")))

    def test_no_third_payment_can_start_once_the_order_is_settled(self):
        self.retry()
        mark_payment(self.first, "paid")
        for method in ("card", "invoice"):
            response = self.pay(method)
            self.assertEqual((response.status_code, response.json()["code"]), (409, "nothing_to_pay"))
        self.assertEqual(self.order.payments.count(), 2)

    def test_every_completed_payment_is_counted_and_any_excess_is_owed_back(self):
        retry = self.retry()
        mark_payment(self.first, "paid")
        mark_payment(retry, "paid", payload={"callback": True})
        total = self.order.total
        order = self.order_now()
        self.assertEqual((order.amount_paid, order.balance_due), (total * 2, -total))
        body = self.client.get(f"/api/orders/{self.order.order_number}/").json()
        self.assertEqual(body["payment_status"], "refund_pending")
        admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        self.assertEqual(record_refund(self.order.pk, admin, "Excess payment"), total)
        self.assertEqual((self.order_now().amount_paid, self.order_now().balance_due), (total, D("0.00")))

    def test_a_retry_by_invoice_cannot_be_confirmed_once_the_first_is_paid(self):
        retry = self.retry("invoice")
        mark_payment(self.first, "paid")
        admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        self.assertEqual(confirm_manual_payment(retry, admin), "overpayment")
        retry.refresh_from_db()
        self.assertEqual((retry.status, self.order_now().amount_paid), ("pending", self.order.total))

    def test_a_conflict_on_the_first_blocks_a_third_payment(self):
        pending = Payment.objects.filter(pk=self.first.pk)
        pending.update(status="pending")
        retries = []

        def fetch(_):
            if not retries:
                mark_payment(self.first, "failed", payload={"callback": True})
                retries.append(self.retry())  # the customer retries while the job waits for the provider
                return "paid"
            return "pending"

        with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch), \
                self.assertLogs("payments.services", "ERROR"):
            self.assertEqual(recheck_payment(pending.get(), FakeProvider()), "conflict")
        statuses = {p.pk: p.status for p in self.order.payments.all()}
        self.assertEqual(statuses, {self.first.pk: "unknown", retries[0].pk: "pending"})
        self.assertEqual(self.pay("card").status_code, 409)
        self.assertEqual(self.order.payments.count(), 2)


class ConfirmTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        self.order = make_order(make_customer(), payment_option="split_70_30")
        Order.objects.filter(pk=self.order.pk).update(amount_due_now=D("182.00"))
        self.initial = create_payment(Order.objects.get(pk=self.order.pk), "invoice")

    def paid(self):
        return Order.objects.get(pk=self.order.pk).amount_paid

    def test_split_terms_initial_then_balance(self):
        self.assertIsNone(confirm_manual_payment(self.initial, self.admin))
        balance = create_payment(Order.objects.get(pk=self.order.pk), "invoice", Payment.KIND_BALANCE)
        self.assertEqual(balance.amount, D("78.00"))
        self.assertIsNone(confirm_manual_payment(balance, self.admin))
        self.assertEqual(self.paid(), D("260.00"))

    def test_invoice_after_the_order_was_paid_by_card_is_refused(self):
        card = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=D("260.00"),
                                      currency="GEL", status="pending")
        mark_payment(card, "paid")
        self.assertEqual(confirm_manual_payment(self.initial, self.admin), "overpayment")
        self.initial.refresh_from_db()
        self.assertEqual((self.initial.status, self.paid()), ("pending", D("260.00")))

    def test_partial_payment_is_kept(self):
        partial = Payment.objects.create(order=self.order, kind="balance", provider="manual", amount=D("50.00"),
                                         currency="GEL", status="pending")
        self.assertIsNone(confirm_manual_payment(partial, self.admin))
        self.assertIsNone(confirm_manual_payment(self.initial, self.admin))
        self.assertEqual(self.paid(), D("232.00"))

    def test_card_and_non_pending_payments_are_refused(self):
        card = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=D("1"),
                                      currency="GEL", status="pending")
        self.assertEqual(confirm_manual_payment(card, self.admin), "not_manual")
        confirm_manual_payment(self.initial, self.admin)
        self.assertEqual(confirm_manual_payment(self.initial, self.admin), "not_pending")


class AdminConfirmTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))
        self.order = make_order(make_customer())
        self.first = create_payment(self.order, "invoice")
        # an older duplicate (e.g. created before this fix)
        self.second = Payment.objects.create(order=self.order, kind="initial", provider="invoice",
                                             amount=self.order.total, currency="GEL", status="pending")

    def act(self, url, action, *objects):
        return self.client.post(url, {"action": action, "_selected_action": [o.pk for o in objects]}, follow=True,
                                headers={"accept-language": "en"})

    def statuses(self):
        return sorted(self.order.payments.values_list("status", flat=True))

    def test_order_action_refuses_to_pick_between_two_pending_invoices(self):
        response = self.act(ORDERS, "payment_received", self.order)
        self.assertEqual(self.statuses(), ["pending", "pending"])
        self.assertIn("choose one in Payments", response.content.decode())

    def test_selecting_both_duplicates_confirms_only_one(self):
        response = self.act(PAYMENTS, "payment_received", self.first, self.second)
        self.assertEqual(self.statuses(), ["paid", "pending"])
        self.assertEqual(Order.objects.get(pk=self.order.pk).balance_due, D("0.00"))
        self.assertIn("no longer owes this amount", response.content.decode())

    def test_cancel_invoice(self):
        self.act(PAYMENTS, "cancel_invoice", self.second)
        self.act(ORDERS, "payment_received", self.order)  # now exactly one pending
        self.assertEqual(self.statuses(), ["expired", "paid"])


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL (CI)")
class ParallelPaymentTests(TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.order = make_order(self.customer)
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")

    def parallel(self, work):
        results, barrier = [], threading.Barrier(2)

        def run(arg):
            barrier.wait()
            try:
                results.append(work(arg))
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def test_two_parallel_invoice_requests_create_one_payment(self):
        def request_invoice(_):
            client = APIClient()
            client.force_login(self.customer.user)
            return client.post(f"/api/orders/{self.order.order_number}/payments/", {"method": "invoice"},
                               format="json").status_code

        self.assertEqual(sorted(self.parallel(request_invoice)), [201, 409])
        self.assertEqual(self.order.payments.count(), 1)

    def test_two_duplicate_invoices_confirmed_in_parallel_collect_once(self):
        payments = [Payment.objects.create(order=self.order, kind="initial", provider="invoice",
                                           amount=self.order.total, currency="GEL", status="pending")
                    for _ in range(2)]
        results = self.parallel(lambda i: confirm_manual_payment(payments[i], self.admin))
        self.assertEqual(sorted(results, key=str), [None, "overpayment"])
        self.assertEqual(Order.objects.get(pk=self.order.pk).amount_paid, self.order.total)

    def in_another_connection(self, work):
        """Commits `work` while the caller waits. It would hang if the caller held a row lock across the provider call."""
        def run():
            try:
                work()
            finally:
                connection.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(10)
        self.assertFalse(thread.is_alive(), "blocked by a row lock held during the provider call")

    def test_a_cancellation_committed_during_provider_start_stays_closed(self):
        payment = create_payment(self.order, "card")

        def start(provider, _):
            self.in_another_connection(lambda: cancel_order(self.order.pk, "Changed my mind", actor="customer"))
            return "https://lionparts.ge/late"

        with mock.patch("payments.providers.FakeProvider.start", start):
            self.assertFalse(start_payment(payment))
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.checkout_url), ("expired", ""))

    def test_a_callback_committed_during_reconciliation_is_not_overwritten(self):
        payment = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=self.order.total,
                                         currency="GEL", status="pending")
        age(payment, 31)

        def fetch(_):
            self.in_another_connection(lambda: mark_payment(payment, "failed", payload={"callback": True}))
            return "pending"

        out = StringIO()
        with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch):
            call_command("reconcile_payments", stdout=out)
        payment.refresh_from_db()
        self.assertEqual((payment.status, out.getvalue().strip()), ("failed", "updated: 0, unknown: 0, errors: 0"))

    def test_a_newer_callback_committed_during_a_recheck_of_an_unknown_payment_wins(self):
        payment = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=self.order.total,
                                         currency="GEL", status="unknown")

        def fetch(_):
            self.in_another_connection(lambda: mark_payment(payment, "failed", payload={"callback": True}))
            return "expired"

        age(payment, 120)
        out = StringIO()
        with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch):
            call_command("reconcile_payments", stdout=out)
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.provider_payload, out.getvalue().strip()),
                         ("failed", {"callback": True}, "updated: 0, unknown: 0, errors: 0"))

    def test_paid_confirmed_after_a_closing_callback_committed_elsewhere_wins(self):
        for status in ("pending", "unknown"):
            for callback in ("failed", "expired"):
                with self.subTest(status=status, callback=callback):
                    order = make_order(self.customer)
                    payment = Payment.objects.create(order=order, kind="initial", provider="fake", amount=order.total,
                                                     currency="GEL", status=status)
                    age(payment, 120)
                    calls = []

                    def fetch(_, payment=payment, callback=callback, calls=calls):
                        calls.append(1)
                        if len(calls) == 1:
                            self.in_another_connection(
                                lambda: mark_payment(payment, callback, payload={"callback": True}))
                        return "paid"

                    out = StringIO()
                    with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch):
                        call_command("reconcile_payments", stdout=out)
                    payment.refresh_from_db()
                    self.assertEqual((payment.status, len(calls), out.getvalue().strip()),
                                     ("paid", 2, "updated: 1, unknown: 0, errors: 0"))
                    self.assertEqual(Order.objects.get(pk=order.pk).amount_paid, order.total)

    def test_an_unconfirmed_paid_after_a_callback_committed_elsewhere_becomes_unknown_and_blocks(self):
        payment = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=self.order.total,
                                         currency="GEL", status="unknown")
        answers = ["paid", "pending"]

        def fetch(_):
            if len(answers) == 2:
                self.in_another_connection(lambda: mark_payment(payment, "failed", payload={"callback": True}))
            return answers.pop(0)

        with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch), \
                self.assertLogs("payments.services", "ERROR"):
            self.assertEqual(recheck_payment(payment, FakeProvider()), "conflict")
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.provider_payload["callback"]), ("unknown", True))
        client = APIClient()
        client.force_login(self.customer.user)
        response = client.post(f"/api/orders/{self.order.order_number}/payments/", {"method": "invoice"}, format="json")
        self.assertEqual((response.status_code, response.json()["code"]), (409, "payment_in_progress"))

    def test_a_third_result_committed_elsewhere_during_the_repeat_query_is_held_as_unknown(self):
        payment = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=self.order.total,
                                         currency="GEL", status="unknown")
        closes = ["failed", "expired"]

        def fetch(_):
            status = closes.pop(0)
            self.in_another_connection(lambda: mark_payment(payment, status, payload={"callback": True}))
            return "paid"

        with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch), \
                self.assertLogs("payments.services", "ERROR"):
            self.assertEqual(recheck_payment(payment, FakeProvider()), "conflict")
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.provider_payload["conflict_with"], payment.provider_payload["callback"]),
                         ("unknown", "expired", True))
        client = APIClient()
        client.force_login(self.customer.user)
        for method in ("card", "invoice"):
            response = client.post(f"/api/orders/{self.order.order_number}/payments/", {"method": method}, format="json")
            self.assertEqual((response.status_code, response.json()["code"]), (409, "payment_in_progress"))

    def test_a_paid_callback_committed_elsewhere_survives_a_stale_failure(self):
        payment = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=self.order.total,
                                         currency="GEL", status="unknown")
        age(payment, 120)

        def fetch(_):
            self.in_another_connection(lambda: mark_payment(payment, "paid", payload={"callback": True}))
            return "failed"

        with mock.patch.object(FakeProvider, "fetch_status", side_effect=fetch):
            call_command("reconcile_payments", stdout=StringIO())
        payment.refresh_from_db()
        self.assertEqual(payment.status, "paid")
        self.assertEqual(Order.objects.get(pk=self.order.pk).amount_paid, self.order.total)

    def test_two_parallel_rechecks_apply_a_paid_answer_once(self):
        payment = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=self.order.total,
                                         currency="GEL", status="unknown")

        def recheck(_):
            return recheck_payment(Payment.objects.get(pk=payment.pk), FakeProvider())

        with mock.patch.object(FakeProvider, "fetch_status", return_value="paid"):
            results = self.parallel(recheck)
        self.assertEqual(sorted(results), ["superseded", "updated"])
        order = Order.objects.get(pk=self.order.pk)
        self.assertEqual((order.amount_paid, order.status), (self.order.total, "processing"))
        self.assertEqual(OrderItemEvent.objects.filter(item__order=order, text_code="payment_confirmed").count(), 1)
