"""LPK-219 S3: a checkout link is shown only while paying it is what the order needs, and a paid result a person should look at
is flagged durably, once. Hiding a link does not close the provider's page, so these results are still counted."""

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from orders.models import Order
from orders.serializers import order_queryset, payment_data
from orders.services import cancel_order, payment_status
from payments import test_provider_evidence as evidence
from payments.models import Payment
from payments.services import mark_payment, mark_reviewed, record_refund, request_payment, start_payment

D = Decimal
TWO_ITEMS = (("A", D("100"), 1), ("B", D("50"), 1))
KEYS = {"id", "kind", "provider", "status", "amount", "currency", "checkout_url", "paid_at", "created_at"}


class SafetyTestCase(evidence.EvidenceTestCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_login(self.customer.user)

    def link(self, payment):
        response = self.api.get(f"/api/orders/{payment.order.order_number}/payments/{payment.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), KEYS)
        return response.json()["checkout_url"]

    def order_of(self, payment):
        return Order.objects.get(pk=payment.order_id)


class CheckoutLinkTests(SafetyTestCase):
    def test_a_pending_payment_the_order_needs_shows_its_link(self):
        payment = self.session()
        self.assertEqual(self.link(payment), payment.checkout_url)

    def test_every_other_status_hides_it(self):
        for status in ("failed", "expired", "unknown", "paid"):
            with self.subTest(status=status):
                payment = self.session()
                mark_payment(payment, status)
                self.assertIsNone(self.link(payment))
                self.assertTrue(self.now(payment).checkout_url)  # kept: it is evidence that a page was opened

    def test_a_cancelled_order_hides_it(self):
        payment = self.session()
        Order.objects.filter(pk=payment.order_id).update(status=Order.STATUS_CANCELLED)
        self.assertIsNone(self.link(payment))

    def test_a_balance_that_no_longer_covers_the_amount_hides_it(self):
        payment = self.session()
        Order.objects.filter(pk=payment.order_id).update(balance_due=payment.amount - 1)
        self.assertIsNone(self.link(payment))

    def test_a_fully_paid_order_hides_it(self):
        payment = self.session()
        Order.objects.filter(pk=payment.order_id).update(balance_due=0, amount_paid=payment.amount)
        self.assertIsNone(self.link(payment))

    def test_an_initial_payment_overtaken_by_money_already_paid_hides_it(self):
        payment = self.session(items=TWO_ITEMS)
        Order.objects.filter(pk=payment.order_id).update(amount_paid=D("1.00"), balance_due=payment.amount)
        self.assertIsNone(self.link(payment))

    def test_a_balance_payment_after_the_first_keeps_its_link(self):
        first = self.session()
        self.gateway.send_callback(first, "paid")
        Order.objects.filter(pk=first.order_id).update(balance_due=D("50.00"))
        balance = Payment.objects.create(order=first.order, kind="balance", provider="simulated", amount=D("50.00"),
                                         currency="GEL", status="pending", checkout_url="https://provider.invalid/b")
        self.assertEqual(self.link(balance), balance.checkout_url)

    def test_the_order_detail_and_the_409_apply_the_same_rule(self):
        payment = self.session()
        mark_payment(payment, "expired")
        detail = self.api.get(f"/api/orders/{payment.order.order_number}/").json()
        self.assertIsNone(detail["payments"][0]["checkout_url"])
        conflict = self.api.post(f"/api/orders/{payment.order.order_number}/payments/", {"method": "card"}, format="json")
        self.assertEqual(conflict.status_code, 409)
        self.assertIsNone(conflict.json()["payment"]["checkout_url"])

    def test_the_order_detail_costs_no_extra_query_per_payment(self):
        order = self.order()
        Payment.objects.create(order=order, kind="initial", provider="invoice", amount=D("1"), currency="GEL", status="failed")
        loaded = order_queryset(detail=True).get(pk=order.pk)
        payments = list(loaded.payments.all())
        with CaptureQueriesContext(connection) as queries:
            [payment_data(p) for p in payments]
        self.assertEqual(len(queries), 0)

    def test_a_page_already_opened_can_still_complete_and_is_counted(self):
        payment = self.session()
        cancel_order(payment.order_id, "x", actor="operator", operator=True)
        self.assertIsNone(self.link(payment))
        self.gateway.send_callback(payment, "paid")
        order = self.order_of(payment)
        self.assertEqual((order.amount_paid, payment_status(order)), (payment.amount, "refund_pending"))


class ReviewFlagTests(SafetyTestCase):
    def flag(self, payment):
        current = self.now(payment)
        return current.review_reasons, current.review_required_at is not None

    def test_a_normal_payment_is_not_flagged(self):
        payment = self.session()
        self.gateway.send_callback(payment, "paid")
        self.assertEqual(self.flag(payment), ([], False))

    def test_a_late_paid_result_is_flagged(self):
        payment = self.session()
        mark_payment(payment, "expired")
        self.gateway.send_callback(payment, "paid")
        self.assertEqual(self.flag(payment), (["late_paid"], True))

    def test_paid_after_closure_is_flagged(self):
        self.gateway.final_statuses = frozenset({"failed"})
        payment = self.session()
        self.gateway.send_callback(payment, "failed")
        self.gateway.send_callback(payment, "paid")
        self.assertEqual(self.flag(payment), (["late_paid", "paid_after_closure"], True))

    def test_paid_on_a_cancelled_order_is_flagged(self):
        payment = self.session()
        cancel_order(payment.order_id, "x", actor="operator", operator=True)
        with self.assertLogs("payments.services", "ERROR") as logs:
            self.gateway.send_callback(payment, "paid")
        self.assertEqual(self.flag(payment), (["late_paid", "cancelled_order", "overpaid"], True))
        self.assertNotIn(self.customer.first_name or "\0", logs.output[0])

    def test_two_paid_results_flag_the_excess(self):
        self.gateway.final_statuses = frozenset({"failed"})
        first = self.session()
        self.gateway.send_callback(first, "failed")
        second = request_payment(first.order_id, "card")
        start_payment(second)
        self.gateway.send_callback(second, "paid")
        self.assertEqual(self.flag(second), ([], False))
        self.gateway.send_callback(first, "paid")
        self.assertEqual(self.flag(first)[0], ["late_paid", "paid_after_closure", "overpaid"])
        self.assertLess(self.order_of(first).balance_due, 0)

    def test_paid_after_a_conflict_held_it_as_unknown_is_flagged_as_late(self):
        payment = self.session()
        mark_payment(payment, "failed")
        mark_payment(payment, "unknown", provider_conflict=True, payload={"conflict_with": "failed"})
        self.gateway.send_callback(payment, "paid")
        self.assertEqual(self.flag(payment), (["late_paid"], True))

    def test_an_ordinary_unknown_that_turns_out_paid_is_not_flagged(self):
        payment = self.session()
        mark_payment(payment, "unknown")
        self.gateway.send_callback(payment, "paid")
        self.assertEqual(self.flag(payment), ([], False))

    def test_it_is_recorded_once_whatever_is_repeated(self):
        payment = self.session()
        mark_payment(payment, "expired")
        self.gateway.send_callback(payment, "paid")
        first = self.now(payment).review_required_at
        self.gateway.send_callback(payment, "paid")
        self.assertEqual(self.now(payment).review_required_at, first)

    def test_it_rolls_back_with_the_paid_result(self):
        payment = self.session()
        mark_payment(payment, "expired")
        with mock.patch("payments.services._flag_for_review", side_effect=RuntimeError), self.assertRaises(RuntimeError):
            mark_payment(payment, "paid")
        self.assertEqual(self.now(payment).status, "expired")
        self.assertEqual(self.flag(payment), ([], False))

    def test_a_person_marking_it_reviewed_changes_no_money(self):
        admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        payment = self.session()
        cancel_order(payment.order_id, "x", actor="operator", operator=True)
        self.gateway.send_callback(payment, "paid")
        before = self.order_of(payment).balance_due
        self.assertTrue(mark_reviewed(payment, admin))
        self.assertFalse(mark_reviewed(payment, admin))
        current = self.now(payment)
        self.assertEqual((current.reviewed_by, current.status), (admin, "paid"))
        self.assertEqual(self.order_of(payment).balance_due, before)
        self.assertEqual(record_refund(payment.order_id, admin, "Late"), payment.amount)

    def test_nothing_to_review_on_an_unflagged_payment(self):
        admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        self.assertFalse(mark_reviewed(self.session(), admin))


@override_settings(LANGUAGE_CODE="en")
class AdminTests(SafetyTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))

    def flagged(self):
        payment = self.session()
        cancel_order(payment.order_id, "x", actor="operator", operator=True)
        self.gateway.send_callback(payment, "paid")
        return payment

    def test_flagged_payments_are_listed_with_their_reasons(self):
        flagged, normal = self.flagged(), self.session()
        page = self.client.get("/admin/payments/payment/", {"review": "waiting"})
        self.assertEqual([p.pk for p in page.context["cl"].result_list], [flagged.pk])
        self.assertContains(page, "paid on a cancelled order")
        self.assertNotIn(normal.pk, [p.pk for p in page.context["cl"].result_list])

    def test_the_action_records_who_reviewed_it(self):
        payment = self.flagged()
        response = self.client.post("/admin/payments/payment/", {"action": "mark_as_reviewed",
                                                                 "_selected_action": [payment.pk]}, follow=True)
        self.assertIn("marked as reviewed", str(list(response.context["messages"])[0]))
        self.assertIsNotNone(self.now(payment).reviewed_at)
        waiting = self.client.get("/admin/payments/payment/", {"review": "waiting"})
        self.assertEqual(list(waiting.context["cl"].result_list), [])
        reviewed = self.client.get("/admin/payments/payment/", {"review": "reviewed"})
        self.assertEqual([p.pk for p in reviewed.context["cl"].result_list], [payment.pk])

    def test_payments_not_proven_closed_can_be_listed(self):
        unproven = self.session()
        mark_payment(unproven, "expired")
        self.session()
        page = self.client.get("/admin/payments/payment/", {"session": "unproven"})
        self.assertEqual([p.pk for p in page.context["cl"].result_list], [unproven.pk])

    def test_the_action_needs_the_operate_permission(self):
        payment = self.flagged()
        viewer = get_user_model().objects.create_user("viewer", "v@example.com", "x", is_staff=True)
        from django.contrib.auth.models import Permission
        viewer.user_permissions.add(Permission.objects.get(codename="view_payment"))
        self.client.force_login(viewer)
        self.client.post("/admin/payments/payment/", {"action": "mark_as_reviewed", "_selected_action": [payment.pk]})
        self.assertIsNone(self.now(payment).reviewed_at)
