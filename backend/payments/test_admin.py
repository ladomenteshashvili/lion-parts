"""LP-BE-705: admin payment actions go through mark_payment / request_payment (G§7.10)."""

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.utils.translation import override

from accounts.testing import make_customer
from orders.models import Order, OrderItem
from orders.services import cancel_order, recalculate_order
from orders.testing import make_order
from payments.admin import RECHECK_OTHER, PaymentAdmin
from payments.models import Payment
from payments.providers import FakeProvider
from payments.services import create_payment, mark_payment, refund_whole_payment

D = Decimal
PAYMENTS = "/admin/payments/payment/"
ORDERS = "/admin/orders/order/"


class AdminActionTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        self.client.force_login(self.admin)
        self.order = make_order(make_customer())
        self.invoice = create_payment(self.order, "invoice")

    def act(self, url, action, *objects):
        return self.client.post(url, {"action": action, "_selected_action": [o.pk for o in objects]}, follow=True)

    def test_payment_received_records_who_and_when_and_moves_the_order(self):
        self.act(PAYMENTS, "payment_received", self.invoice)
        self.invoice.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual((self.invoice.status, self.invoice.confirmed_by), ("paid", self.admin))
        self.assertIsNotNone(self.invoice.paid_at)
        # the same transition as a card payment (mark_payment)
        self.assertEqual((self.order.status, self.order.amount_paid, self.order.balance_due),
                         ("processing", self.order.total, D("0.00")))
        self.assertEqual(set(self.order.items.values_list("item_status", flat=True)),
                         {OrderItem.STATUS_PAYMENT_CONFIRMED})

    def test_payment_received_from_the_order_admin(self):
        self.act(ORDERS, "payment_received", self.order)
        self.invoice.refresh_from_db()
        self.assertEqual((self.invoice.status, self.invoice.confirmed_by), ("paid", self.admin))

    def test_card_payments_are_not_confirmed_by_hand(self):
        card = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=D("1"),
                                      currency="GEL", status="pending")
        self.act(PAYMENTS, "payment_received", card)
        card.refresh_from_db()
        self.assertEqual(card.status, "pending")

    def test_mark_refunded_only_after_paid(self):
        self.act(PAYMENTS, "mark_refunded", self.invoice)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "pending")
        self.act(PAYMENTS, "payment_received", self.invoice)
        self.act(PAYMENTS, "mark_refunded", self.invoice)  # D20: nothing is owed back on an open order
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "paid")
        cancel_order(self.order.pk, "Supplier failure", actor="operator", operator=True)
        self.act(PAYMENTS, "mark_refunded", self.invoice)
        self.invoice.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual((self.invoice.status, self.order.amount_paid), ("refunded", D("0.00")))

    def test_create_balance_invoice(self):
        self.act(PAYMENTS, "payment_received", self.invoice)
        self.order.refresh_from_db()
        self.act(ORDERS, "create_balance_invoice", self.order)  # fully paid
        self.assertEqual(self.order.payments.count(), 1)
        OrderItem.objects.create(**{**self.order.items.values().first(), "id": None, "part_number": "EXTRA"})
        recalculate_order(Order.objects.get(pk=self.order.pk))
        self.act(ORDERS, "create_balance_invoice", self.order)
        balance = self.order.payments.get(kind="balance")
        self.order.refresh_from_db()
        self.assertEqual((balance.provider, balance.status, balance.amount, balance.currency),
                         ("invoice", "pending", self.order.balance_due, "GEL"))
        self.act(ORDERS, "create_balance_invoice", self.order)  # payment_in_progress: no duplicate
        self.assertEqual(self.order.payments.filter(kind="balance").count(), 1)

    def test_view_only_staff_cannot_run_actions(self):
        staff = get_user_model().objects.create_user("viewer", "v@example.com", "x", is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(codename__in=["view_payment", "view_order"]))
        self.client.force_login(staff)
        response = self.client.get(PAYMENTS)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("payment_received", response.content.decode())
        self.act(PAYMENTS, "payment_received", self.invoice)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "pending")
        self.assertNotIn("create_balance_invoice", self.client.get(ORDERS).content.decode())

    def test_payments_cannot_be_edited_directly(self):
        self.assertEqual(self.client.post(f"{PAYMENTS}{self.invoice.pk}/change/", {"status": "paid"}).status_code, 403)
        self.assertEqual(self.client.get(f"{PAYMENTS}add/").status_code, 403)


class RecheckActionTests(TestCase):
    """LPK-180: "Re-check with provider"."""

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        self.client.force_login(self.admin)
        self.order = make_order(make_customer())

    def payment(self, status="unknown", provider="fake"):
        return Payment.objects.create(order=self.order, kind="initial", provider=provider, amount=self.order.total,
                                      currency="GEL", status=status)

    def recheck(self, *payments, language="en", **fetch):
        with mock.patch.object(FakeProvider, "fetch_status", **fetch) as self.fetch:
            response = self.client.post(PAYMENTS, {"action": "recheck_with_provider",
                                                   "_selected_action": [p.pk for p in payments]}, follow=True,
                                        headers={"accept-language": language})
        return response.content.decode()

    def test_a_final_answer_is_applied_and_reported(self):
        expected = {"paid": "the provider confirmed the payment", "failed": "the payment failed",
                    "expired": "the payment expired"}
        for answer, text in expected.items():
            for status in ("pending", "unknown"):
                with self.subTest(answer=answer, status=status):
                    self.order = make_order(self.order.customer)
                    payment = self.payment(status)
                    page = self.recheck(payment, return_value=answer)
                    payment.refresh_from_db()
                    self.assertEqual((payment.status, payment.provider_payload),
                                     (answer, {"reconciled": answer, "rechecked_by": self.admin.pk}))
                    self.assertIn(text, page)

    def test_a_confirmed_payment_moves_the_order(self):
        payment = self.payment()
        self.recheck(payment, return_value="paid")
        self.order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual((self.order.status, self.order.amount_paid), ("processing", payment.amount))
        self.assertIsNone(payment.confirmed_by)  # the provider confirmed it, not the operator

    def test_provider_pending_changes_nothing(self):
        for status in ("pending", "unknown"):
            with self.subTest(status=status):
                payment = self.payment(status)
                page = self.recheck(payment, return_value="pending")
                payment.refresh_from_db()
                self.assertEqual(payment.status, status)
                self.assertIn("no final result yet", page)

    def test_a_timeout_or_invalid_answer_changes_nothing_and_stays_private(self):
        for fetch in ({"side_effect": TimeoutError("https://p.example/?token=SECRET-TOKEN")},
                      {"return_value": "SECRET-TOKEN"}, {"return_value": "refunded"}):
            with self.subTest(fetch=fetch), self.assertLogs("payments.admin", "ERROR") as logs:
                payment = self.payment()
                page = self.recheck(payment, **fetch)
                payment.refresh_from_db()
                self.assertEqual(payment.status, "unknown")
                self.assertIn("could not confirm the status", page)
                self.assertNotIn("SECRET-TOKEN", page)
                self.assertNotIn("SECRET-TOKEN", logs.output[0])
                self.assertIsNone(logs.records[0].exc_info)

    def test_one_failure_does_not_stop_the_other_payments(self):
        broken, good = self.payment(), self.payment()

        def fetch(payment):
            if payment.pk == broken.pk:
                raise TimeoutError("down")
            return "failed"

        with self.assertLogs("payments.admin", "ERROR"):
            self.recheck(broken, good, side_effect=fetch)
        broken.refresh_from_db()
        good.refresh_from_db()
        self.assertEqual((broken.status, good.status), ("unknown", "failed"))

    def test_ineligible_payments_are_ignored_and_the_provider_is_not_asked(self):
        ineligible = [self.payment(status) for status in ("created", "paid", "failed", "expired", "refunded")]
        ineligible += [self.payment("pending", "invoice"), self.payment("unknown", "invoice"),
                       self.payment("pending", "manual"), self.payment("unknown", "manual")]
        before = [p.status for p in ineligible]
        page = self.recheck(*ineligible, return_value="paid")
        self.fetch.assert_not_called()
        self.assertEqual([Payment.objects.get(pk=p.pk).status for p in ineligible], before)
        self.assertEqual(page.count("can be re-checked"), len(ineligible))

    def test_a_payment_of_another_provider_is_not_asked(self):
        old = self.payment("unknown", "oldprovider")
        page = self.recheck(old, return_value="paid")
        self.fetch.assert_not_called()
        old.refresh_from_db()
        self.assertEqual(old.status, "unknown")
        self.assertIn("belongs to another provider", page)

    def test_the_provider_is_never_called_inside_a_transaction(self):
        baseline, seen = len(connection.atomic_blocks), {}

        def fetch(payment):
            seen["depth"] = len(connection.atomic_blocks)
            return "failed"

        self.recheck(self.payment(), side_effect=fetch)
        self.assertEqual(seen["depth"], baseline)

    def test_a_stale_answer_never_overwrites_a_newer_callback(self):
        payment = self.payment()

        def fetch(_):
            mark_payment(payment, "failed", payload={"callback": True})
            return "expired"

        page = self.recheck(payment, side_effect=fetch)
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.provider_payload), ("failed", {"callback": True}))
        self.assertIn("nothing was overwritten", page)

    def paid_answers_over_a_failed_callback(self, payment, second):
        answers = ["paid", second]

        def fetch(_):
            if len(answers) == 2:
                mark_payment(payment, "failed", payload={"callback": True})
            return answers.pop(0)

        return {"side_effect": fetch}

    def test_paid_confirmed_twice_beats_a_newer_failure_and_the_audit_trail_is_complete(self):
        payment = self.payment()
        with self.assertLogs("payments.services", "WARNING"):
            page = self.recheck(payment, **self.paid_answers_over_a_failed_callback(payment, "paid"))
        payment.refresh_from_db()
        self.assertEqual(payment.status, "paid")
        self.assertEqual(payment.provider_payload, {"callback": True, "reconciled": "paid", "conflict_with": "failed",
                                                    "conflict_second_answer": "paid", "rechecked_by": self.admin.pk})
        self.assertIn("the provider confirmed the payment", page)

    def test_paid_not_confirmed_against_a_newer_failure_becomes_unknown_and_stays_eligible(self):
        payment = self.payment()
        with self.assertLogs("payments.services", "ERROR"):
            page = self.recheck(payment, **self.paid_answers_over_a_failed_callback(payment, "pending"))
        payment.refresh_from_db()
        self.assertEqual(payment.status, "unknown")
        self.assertEqual(payment.provider_payload, {
            "callback": True, "reconciled": "unknown", "conflict_with": "failed", "conflict_second_answer": "pending",
            "rechecked_by": self.admin.pk})
        self.assertIn("blocks new payments until the provider confirms", page)
        self.recheck(payment, return_value="paid")  # a later re-check can settle it
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.provider_payload["conflict_with"]), ("paid", "failed"))

    def test_a_third_closing_result_during_the_repeat_query_is_held_as_unknown(self):
        payment = self.payment()
        answers = ["paid", "paid"]

        def fetch(_):
            mark_payment(payment, "failed" if len(answers) == 2 else "expired", payload={"callback": True})
            return answers.pop(0)

        with self.assertLogs("payments.services", "ERROR"):
            page = self.recheck(payment, side_effect=fetch)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "unknown")
        self.assertEqual(payment.provider_payload, {
            "callback": True, "reconciled": "unknown", "conflict_with": "expired", "conflict_second_answer": "paid",
            "rechecked_by": self.admin.pk})
        self.assertIn("blocks new payments until the provider confirms", page)

    def test_the_operator_stays_on_record_through_later_changes(self):
        payment = self.payment()
        self.recheck(payment, return_value="failed")
        mark_payment(payment, "paid", payload={"callback": True})
        cancel_order(self.order.pk, "Supplier failure", actor="operator", operator=True)
        self.assertIsNone(refund_whole_payment(Payment.objects.get(pk=payment.pk), self.admin))
        payment.refresh_from_db()
        self.assertEqual(payment.provider_payload, {"reconciled": "failed", "rechecked_by": self.admin.pk,
                                                    "callback": True, "refunded_by": self.admin.pk})

    def test_every_re_check_is_logged_with_the_operator_and_no_provider_data(self):
        payment = self.payment()
        with self.assertLogs("payments.admin", "INFO") as logs:
            self.recheck(payment, return_value="pending")
        self.assertEqual(logs.records[0].getMessage(), f"Payment {payment.pk} re-checked by user {self.admin.pk}: pending")

    def test_a_payment_that_moved_on_right_after_the_update_still_gets_a_message(self):
        payment = self.payment()

        def applied(p, provider, checked_by=None):
            Payment.objects.filter(pk=p.pk).update(status="refunded")
            return "updated"

        with mock.patch("payments.admin.recheck_payment", applied):
            response = self.client.post(PAYMENTS, {"action": "recheck_with_provider", "_selected_action": [payment.pk]},
                                        follow=True, headers={"accept-language": "en"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("nothing was overwritten", response.content.decode())

    def test_view_only_staff_cannot_see_or_run_it(self):
        staff = get_user_model().objects.create_user("viewer", "v@example.com", "x", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename="view_payment"))
        self.client.force_login(staff)
        payment = self.payment()  # the list shows its actions only when it has rows
        self.assertNotIn("recheck_with_provider", self.client.get(PAYMENTS).content.decode())
        self.recheck(payment, return_value="paid")
        self.fetch.assert_not_called()
        payment.refresh_from_db()
        self.assertEqual(payment.status, "unknown")

    def test_change_payment_permission_can_run_it(self):
        staff = get_user_model().objects.create_user("ops", "o@example.com", "x", is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(codename__in=["view_payment", "change_payment"]))
        self.client.force_login(staff)
        payment = self.payment()
        self.assertIn("recheck_with_provider", self.client.get(PAYMENTS).content.decode())
        self.recheck(payment, return_value="failed")
        payment.refresh_from_db()
        self.assertEqual(payment.status, "failed")

    def test_messages_are_translated(self):
        self.payment()  # the list shows its actions only when it has rows
        english_label = str(PaymentAdmin.recheck_with_provider.short_description)
        english_text = str(RECHECK_OTHER["pending"][0]).split(": ", 1)[1]
        for language in ("ka", "ru"):
            with self.subTest(language=language), override(language):
                label = str(PaymentAdmin.recheck_with_provider.short_description)
                text = str(RECHECK_OTHER["pending"][0]).split(": ", 1)[1]
                self.assertNotIn(english_label, label)
                self.assertNotIn(english_text, text)
                self.assertIn(label, self.client.get(PAYMENTS, headers={"accept-language": language}).content.decode())
                self.assertIn(text, self.recheck(self.payment(), language=language, return_value="pending"))
