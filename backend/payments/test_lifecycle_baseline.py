"""LPK-219 S0: today's payment lifecycle through the simulated provider, including 70/30 split orders. These pin current
behaviour, so a later stage that changes it has to change the matching test on purpose."""

import threading
import unittest
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import make_customer
from orders.models import Order, OrderItem
from orders.services import cancel_order, payment_status, set_item_status
from orders.testing import make_order
from payments.models import Payment
from payments.providers import PROVIDERS, PaymentInitError, get_provider
from payments.services import confirm_manual_payment, recheck_payment, record_refund, request_payment, start_payment
from payments.testing import SimulatedProvider, use_simulated_provider

D = Decimal


def run_reconcile():
    out = StringIO()
    call_command("reconcile_payments", stdout=out)
    return out.getvalue().strip()


def age(payment, minutes):
    Payment.objects.filter(pk=payment.pk).update(created_at=timezone.now() - timedelta(minutes=minutes))


class SimulatedProviderIsTestOnlyTests(SimpleTestCase):
    def test_no_setting_can_select_it_outside_a_test(self):
        self.assertNotIn(SimulatedProvider.name, PROVIDERS)
        with override_settings(PAYMENT_PROVIDER=SimulatedProvider.name), self.assertRaises(ImproperlyConfigured):
            get_provider()

    def test_it_is_registered_only_until_the_test_ends(self):
        gateway = use_simulated_provider(self)
        self.assertEqual(get_provider().gateway, gateway)
        self.doCleanups()
        self.assertNotIn(SimulatedProvider.name, PROVIDERS)


class SimulatedGatewayTests(SimpleTestCase):
    """The provider's side changes only when the provider acts. Finality and retries are choices made per test."""

    def session(self, gateway):
        payment = Payment(order_id=1, kind="initial", provider="simulated", amount=D("10.00"), currency="GEL",
                          provider_order_id="LP-1-1")
        get_provider().start(payment)
        return payment, gateway.session(payment)

    def test_a_payment_page_takes_one_payment(self):
        gateway = use_simulated_provider(self)
        payment, session = self.session(gateway)
        self.assertEqual((gateway.pay(payment), gateway.pay(payment)), (True, False))
        self.assertEqual((session.status, session.charges), ("paid", 1))

    def test_a_decline_closes_the_page_or_not_as_the_test_chooses(self):
        for chargeable_after, paid in ((False, False), (True, True)):
            with self.subTest(chargeable_after=chargeable_after):
                gateway = use_simulated_provider(self)
                payment, session = self.session(gateway)
                gateway.decline(payment, chargeable_after=chargeable_after)
                self.assertEqual(session.status, "failed")
                self.assertEqual(gateway.pay(payment), paid)

    def test_an_expired_page_takes_no_payment(self):
        gateway = use_simulated_provider(self)
        payment, session = self.session(gateway)
        gateway.expire(payment)
        self.assertEqual((gateway.pay(payment), session.charges), (False, 0))

    def test_fetch_answers_are_scripted_then_follow_the_session(self):
        gateway = use_simulated_provider(self)
        payment, _session = self.session(gateway)
        gateway.answer_fetch_with(payment, "paid", TimeoutError("down"))
        provider = get_provider()
        self.assertEqual(provider.fetch_status(payment), "paid")
        with self.assertRaises(TimeoutError):
            provider.fetch_status(payment)
        self.assertEqual(provider.fetch_status(payment), "pending")

    def test_a_provider_that_is_down_cannot_start(self):
        gateway = use_simulated_provider(self)
        gateway.start_error = True
        with self.assertRaises(PaymentInitError):
            self.session(gateway)


class LifecycleTestCase(TestCase):
    payment_option = "full"
    items = (("A", D("100"), 1),)  # GEL 260.00

    def setUp(self):
        self.gateway = use_simulated_provider(self)
        self.gateway.final_statuses = frozenset(("failed", "expired"))  # enforcement has its own tests
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.client = APIClient()
        self.client.force_login(self.customer.user)
        self.order = make_order(self.customer, payment_option=self.payment_option, items=self.items,
                                payment_method="card")

    def order_now(self):
        return Order.objects.get(pk=self.order.pk)

    def post_payment(self, method="card"):
        return self.client.post(f"/api/orders/{self.order.order_number}/payments/", {"method": method}, format="json")

    def start(self, method="card"):
        response = self.post_payment(method)
        self.assertEqual(response.status_code, 201, response.content)
        return Payment.objects.get(pk=response.json()["id"])

    def status(self, payment):
        return Payment.objects.get(pk=payment.pk).status


class CardLifecycleTests(LifecycleTestCase):
    def test_starting_a_payment_opens_a_provider_session(self):
        payment = self.start()
        self.assertEqual((payment.status, payment.provider), ("pending", "simulated"))
        self.assertEqual(payment.checkout_url, f"https://provider.invalid/pay/{payment.provider_order_id}")
        self.assertEqual(self.gateway.session(payment).amount, "260.00")

    def test_a_verified_paid_callback_settles_the_order(self):
        payment = self.start()
        self.assertEqual(self.gateway.send_callback(payment, "paid").status_code, 200)
        order = self.order_now()
        self.assertEqual((self.status(payment), order.status, order.amount_paid, order.balance_due),
                         ("paid", "processing", D("260.00"), D("0.00")))
        self.assertEqual(set(order.items.values_list("item_status", flat=True)), {OrderItem.STATUS_PAYMENT_CONFIRMED})

    def test_a_duplicate_paid_callback_counts_once(self):
        payment = self.start()
        for _ in range(3):
            self.assertEqual(self.gateway.send_callback(payment, "paid").status_code, 200)
        self.assertEqual(self.order_now().amount_paid, D("260.00"))

    def test_an_unverifiable_callback_changes_nothing(self):
        payment = self.start()
        cases = ({"key": b"another-key"}, {"amount": "1.00"}, {"currency": "USD"})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertLogs("payments.views", "WARNING"):
                self.assertEqual(self.gateway.send_callback(payment, "paid", **kwargs).status_code, 400)
        self.assertEqual((self.status(payment), self.order_now().amount_paid), ("pending", D("0.00")))

    def test_a_callback_for_another_provider_is_not_found(self):
        response = APIClient().post("/api/payments/fake/callback/", {}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_a_provider_that_cannot_start_leaves_a_failed_payment_and_allows_a_retry(self):
        self.gateway.start_error = True
        response = self.post_payment()
        self.assertEqual((response.json()["status"], response.json()["checkout_url"]), ("failed", None))
        self.gateway.start_error = False
        self.assertEqual(self.post_payment().status_code, 201)

    def test_a_retry_after_a_provider_failure_gets_its_own_session(self):
        first = self.start()
        self.gateway.send_callback(first, "failed")
        second = self.start()
        self.assertNotEqual(first.provider_order_id, second.provider_order_id)
        self.assertEqual(len(self.gateway.sessions), 2)

    def test_an_open_payment_answers_409_with_its_details(self):
        first = self.start()
        response = self.post_payment()
        self.assertEqual((response.status_code, response.json()["code"], response.json()["payment"]["id"]),
                         (409, "payment_in_progress", first.pk))

    def test_a_provider_result_after_a_local_close_is_still_recorded(self):
        payment = self.start()
        cancel_order(self.order.pk, "Changed my mind", actor="customer")
        self.assertEqual(self.status(payment), "expired")
        self.gateway.send_callback(payment, "paid")
        order = self.order_now()
        self.assertEqual((self.status(payment), order.status, order.amount_paid, payment_status(order)),
                         ("paid", "cancelled", D("260.00"), "refund_pending"))

    def test_a_result_for_the_first_payment_leaves_a_retry_as_it_was(self):
        first = self.start()
        self.gateway.send_callback(first, "failed")
        retry = self.start()
        self.gateway.send_callback(first, "paid")
        retry.refresh_from_db()
        self.assertEqual((retry.status, bool(retry.checkout_url)), ("pending", True))
        self.assertEqual(self.post_payment().json()["code"], "nothing_to_pay")


class ReconciliationBaselineTests(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.payment = self.start()
        age(self.payment, 5)

    def test_a_final_answer_is_applied(self):
        for answer in ("paid", "failed", "expired"):
            with self.subTest(answer=answer):
                Payment.objects.filter(pk=self.payment.pk).update(status="pending")
                self.gateway.answer_fetch_with(self.payment, answer)
                self.assertEqual(run_reconcile(), "updated: 1, unknown: 0, errors: 0")
                self.assertEqual(self.status(self.payment), answer)

    def test_still_pending_after_30_minutes_becomes_unknown_and_then_resolves(self):
        age(self.payment, 31)
        with self.assertLogs("payments.management.commands.reconcile_payments", "WARNING"):
            self.assertEqual(run_reconcile(), "updated: 0, unknown: 1, errors: 0")
        self.assertEqual(self.status(self.payment), "unknown")
        self.assertEqual(self.post_payment().json()["code"], "payment_in_progress")
        self.gateway.answer_fetch_with(self.payment, "paid")
        self.assertEqual(run_reconcile(), "updated: 1, unknown: 0, errors: 0")
        self.assertEqual(self.status(self.payment), "paid")

    def test_a_provider_error_changes_nothing(self):
        self.gateway.answer_fetch_with(self.payment, TimeoutError("down"))
        with self.assertLogs("payments.management.commands.reconcile_payments", "ERROR"):
            self.assertEqual(run_reconcile(), "updated: 0, unknown: 0, errors: 1")
        self.assertEqual(self.status(self.payment), "pending")

    def test_closed_payments_are_not_asked_again(self):
        self.gateway.send_callback(self.payment, "failed")
        self.gateway.fetch_calls.clear()
        self.assertEqual(run_reconcile(), "updated: 0, unknown: 0, errors: 0")
        self.assertEqual(self.gateway.fetch_calls, [])

    def test_a_late_callback_for_a_closed_payment_is_recorded(self):
        self.gateway.send_callback(self.payment, "failed")
        self.gateway.send_callback(self.payment, "paid")
        self.assertEqual((self.status(self.payment), self.order_now().amount_paid), ("paid", D("260.00")))


class SplitPaymentBaselineTests(LifecycleTestCase):
    payment_option = "split_70_30"
    items = (("A", D("100"), 1),)

    def setUp(self):
        super().setUp()
        Order.objects.filter(pk=self.order.pk).update(amount_due_now=D("182.00"), split_upfront_percent=D("70"))

    def assert_money(self, amount_paid, balance_due, state):
        order = self.order_now()
        self.assertEqual((order.amount_paid, order.balance_due, payment_status(order)),
                         (D(amount_paid), D(balance_due), state))

    def test_initial_then_balance_collects_exactly_the_total(self):
        initial = self.start()
        self.assertEqual((initial.kind, initial.amount), ("initial", D("182.00")))
        self.gateway.send_callback(initial, "paid")
        self.assert_money("182.00", "78.00", "partially_paid")
        self.assertEqual(self.order_now().status, "processing")
        balance = self.start()
        self.assertEqual((balance.kind, balance.amount), ("balance", D("78.00")))
        self.gateway.send_callback(balance, "paid")
        self.assert_money("260.00", "0.00", "paid")
        self.assertEqual(self.post_payment().json()["code"], "nothing_to_pay")

    def test_the_balance_can_be_paid_by_invoice(self):
        self.gateway.send_callback(self.start(), "paid")
        invoice = self.start("invoice")
        self.assertEqual((invoice.kind, invoice.amount, invoice.provider), ("balance", D("78.00"), "invoice"))
        admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        self.assertIsNone(confirm_manual_payment(invoice, admin))
        self.assert_money("260.00", "0.00", "paid")

    def test_a_retry_before_the_first_payment_is_still_the_initial_amount(self):
        first = self.start()
        self.gateway.send_callback(first, "failed")
        retry = self.start()
        self.assertEqual((retry.kind, retry.amount), ("initial", D("182.00")))
        self.assert_money("0.00", "260.00", "unpaid")

    def test_a_late_first_payment_with_a_retry_open(self):
        first = self.start()
        self.gateway.send_callback(first, "failed")
        retry = self.start()
        self.gateway.send_callback(first, "paid")
        self.assert_money("182.00", "78.00", "partially_paid")
        response = self.post_payment()
        self.assertEqual((response.status_code, response.json()["code"], response.json()["payment"]["id"]),
                         (409, "payment_in_progress", retry.pk))

    def test_the_balance_is_available_once_the_retry_has_closed(self):
        first = self.start()
        self.gateway.send_callback(first, "failed")
        retry = self.start()
        self.gateway.send_callback(first, "paid")
        self.gateway.send_callback(retry, "expired")
        balance = self.start()
        self.assertEqual((balance.kind, balance.amount), ("balance", D("78.00")))

    def test_every_completed_payment_is_counted_and_the_excess_is_owed_back_exactly(self):
        first = self.start()
        self.gateway.send_callback(first, "failed")
        retry = self.start()
        self.gateway.send_callback(first, "paid")
        self.gateway.send_callback(retry, "paid")
        self.assert_money("364.00", "-104.00", "refund_pending")
        admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        self.assertEqual(record_refund(self.order.pk, admin, "Excess payment"), D("104.00"))
        self.assert_money("260.00", "0.00", "paid")
        self.assertEqual(record_refund(self.order.pk, admin), 0)

    def test_an_invoice_for_more_than_is_owed_cannot_be_confirmed(self):
        first = self.start()
        self.gateway.send_callback(first, "failed")
        invoice = self.start("invoice")
        self.gateway.send_callback(first, "paid")
        admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        self.assertEqual(confirm_manual_payment(invoice, admin), "overpayment")
        self.assert_money("182.00", "78.00", "partially_paid")

    def test_a_result_arriving_after_the_balance_is_paid_is_counted(self):
        first = self.start()
        self.gateway.send_callback(first, "failed")
        retry = self.start()
        self.gateway.send_callback(retry, "paid")
        balance = self.start()
        self.gateway.send_callback(balance, "paid")
        self.assert_money("260.00", "0.00", "paid")
        self.gateway.send_callback(first, "paid")
        self.assert_money("442.00", "-182.00", "refund_pending")


class SplitAmountResetBaselineTests(LifecycleTestCase):
    payment_option = "split_70_30"
    items = (("A", D("100"), 1), ("B", D("50"), 1))  # GEL 260 + 130 = 390

    def setUp(self):
        super().setUp()
        Order.objects.filter(pk=self.order.pk).update(amount_due_now=D("273.00"), split_upfront_percent=D("70"))

    def test_an_item_cancelled_before_payment_resets_the_amount_and_closes_the_payment(self):
        first = self.start()
        set_item_status(self.order.items.get(part_number="A").pk, "cancelled", reason="Out of stock")
        self.assertEqual((self.status(first), self.order_now().amount_due_now), ("expired", D("91.00")))
        self.assertEqual(self.post_payment().status_code, 409)  # its provider page may still take money
        self.gateway.answer_fetch_with(first, "expired")
        self.assertEqual(recheck_payment(Payment.objects.get(pk=first.pk), get_provider()), "closed")
        retry = self.start()
        self.assertEqual((retry.kind, retry.amount), ("initial", D("91.00")))

    def test_a_result_for_the_old_amount_is_counted_and_any_excess_owed_back(self):
        first = self.start()
        set_item_status(self.order.items.get(part_number="A").pk, "cancelled", reason="Out of stock")
        self.gateway.send_callback(first, "paid")
        order = self.order_now()
        self.assertEqual((order.total, order.amount_paid, order.balance_due, payment_status(order)),
                         (D("130.00"), D("273.00"), D("-143.00"), "refund_pending"))


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL (CI)")
class ParallelLifecycleBaselineTests(TransactionTestCase):
    """Real parallel transactions, repeated. A deadlock surfaces as an exception in a thread, which fails the test."""

    serialized_rollback = True
    ROUNDS = 5

    def setUp(self):
        self.gateway = use_simulated_provider(self)
        self.gateway.final_statuses = frozenset(("failed", "expired"))
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")

    def parallel(self, *work):
        barrier, errors, results = threading.Barrier(len(work)), [], [None] * len(work)

        def run(index, function):
            barrier.wait()
            try:
                results[index] = function()
            except Exception as error:
                errors.append(repr(error))
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(index, function)) for index, function in enumerate(work)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        return results

    def order(self, option="full"):
        order = make_order(self.customer, payment_option=option, items=(("A", D("100"), 1), ("B", D("50"), 1)),
                           payment_method="card")
        if option != "full":
            Order.objects.filter(pk=order.pk).update(amount_due_now=D("273.00"), split_upfront_percent=D("70"))
        return Order.objects.get(pk=order.pk)

    def started(self, order):
        payment = request_payment(order.pk, "card")
        start_payment(payment)
        return Payment.objects.get(pk=payment.pk)

    def closed_and_retried(self, order):
        first = self.started(order)
        self.gateway.send_callback(first, "failed")
        return first, self.started(order)

    def assert_money_adds_up(self, order):
        order = Order.objects.get(pk=order.pk)
        paid = sum(p.amount for p in order.payments.filter(status="paid"))
        self.assertEqual(order.amount_paid, paid)
        self.assertEqual(order.balance_due, order.total - paid)
        return order

    def test_two_paid_results_on_one_order(self):
        for _ in range(self.ROUNDS):
            order = self.order()
            first, retry = self.closed_and_retried(order)
            self.parallel(lambda: self.gateway.send_callback(first, "paid"),
                          lambda: self.gateway.send_callback(retry, "paid"))
            self.assertEqual(self.assert_money_adds_up(order).amount_paid, D("780.00"))

    def test_a_paid_result_against_a_cancellation(self):
        for _ in range(self.ROUNDS):
            order = self.order()
            first, retry = self.closed_and_retried(order)
            self.parallel(lambda: self.gateway.send_callback(first, "paid"),
                          lambda: cancel_order(order.pk, "x", actor="operator", operator=True))
            self.assert_money_adds_up(order)

    def test_a_paid_result_against_an_amount_reset(self):
        for _ in range(self.ROUNDS):
            order = self.order("split_70_30")
            first, retry = self.closed_and_retried(order)
            item = order.items.get(part_number="A")
            self.parallel(lambda: self.gateway.send_callback(first, "paid"),
                          lambda: set_item_status(item.pk, "cancelled", reason="x"))
            self.assert_money_adds_up(order)

    def test_a_paid_result_against_a_refund(self):
        for _ in range(self.ROUNDS):
            order = self.order()
            first, retry = self.closed_and_retried(order)
            self.gateway.send_callback(first, "paid")
            self.gateway.send_callback(retry, "paid")
            third = Payment.objects.create(order=order, kind="balance", provider="simulated", amount=D("10.00"),
                                           currency="GEL", status="pending", provider_order_id=f"{order.pk}-third")
            self.parallel(lambda: self.gateway.send_callback(third, "paid"),
                          lambda: record_refund(order.pk, self.admin, "Excess payment"))
            order = Order.objects.get(pk=order.pk)
            self.assertGreaterEqual(order.balance_due, D("-10.00"))

    def test_a_new_payment_request_against_a_paid_result(self):
        for _ in range(self.ROUNDS):
            order = self.order("split_70_30")
            first = self.started(order)
            self.gateway.send_callback(first, "failed")
            client = APIClient()
            client.force_login(self.customer.user)

            def request_new():
                return client.post(f"/api/orders/{order.order_number}/payments/", {"method": "card"},
                                   format="json").status_code

            results = self.parallel(lambda: self.gateway.send_callback(first, "paid"), request_new)
            self.assertIn(results[1], (201, 409))
            self.assert_money_adds_up(order)

    def test_two_payment_requests_create_one_session(self):
        for _ in range(self.ROUNDS):
            order = self.order()
            client = APIClient()
            client.force_login(self.customer.user)

            def request_new():
                return client.post(f"/api/orders/{order.order_number}/payments/", {"method": "card"},
                                   format="json").status_code

            self.assertEqual(sorted(self.parallel(request_new, request_new)), [201, 409])
            self.assertEqual(order.payments.count(), 1)
