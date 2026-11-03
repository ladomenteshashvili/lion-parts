"""LP-BE-706: reconcile_payments (G§7.10)."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.testing import make_customer
from orders.testing import make_order
from payments.models import Payment
from payments.providers import FakeProvider


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

    def test_invoice_payments_are_not_reconciled(self):
        invoice = self.payment(60, provider="invoice")
        run()
        self.assertEqual(self.statuses(invoice), ["pending"])
