"""M3 audit fix 1: no duplicate collection of the same amount (G§7.10).
A pending invoice/manual payment blocks new payments whatever its age; operators confirm exactly the
payment they pick, never more than the order still owes."""

import threading
import unittest
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import make_customer
from orders.models import Order
from orders.testing import make_order
from payments.models import Payment
from payments.services import confirm_manual_payment, create_payment, mark_payment

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

    def test_a_stale_card_payment_allows_a_new_one(self):
        card = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=self.order.total,
                                      currency="GEL", status="pending")
        age(card, 31)
        self.assertEqual(self.pay("card").status_code, 201)

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
