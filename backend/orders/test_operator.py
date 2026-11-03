"""LP-BE-1103: operator services and the rewired admin (G§10, G§7.8, G§7.9; owner decisions D19, D20)."""

import csv as csv_module
import threading
import unittest
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import make_customer
from orders.models import CourierFeeProposal, Order, OrderItem, OrderItemEvent, OrderSupportMessage
from orders import services
from orders.services import (OperatorError, cancel_order, decide, request_item_action, set_item_status)
from orders.testing import make_order
from payments.models import Payment, Refund
from payments.services import create_payment, mark_payment, record_refund, refund_whole_payment
from pricing.models import ExchangeRate

D = Decimal
ITEMS = "/admin/orders/orderitem/"
ORDERS = "/admin/orders/order/"


def paid_order(customer, items=(("A", D("100"), 1), ("B", D("50"), 1))):  # GEL 260 + 130 = 390
    order = make_order(customer, items=items)
    mark_payment(create_payment(order, "invoice"), "paid")
    return Order.objects.get(pk=order.pk)


def events(item, **filters):
    return OrderItemEvent.objects.filter(item=item, **filters)


class ItemActionTests(TestCase):
    def setUp(self):
        self.customer = make_customer()
        self.order = paid_order(self.customer)
        self.item = self.order.items.get(part_number="A")

    def test_price_change_request(self):
        request_item_action(self.item.pk, "price_change", "Supplier raised the price", actor_name="op",
                            proposed_unit_price=D("300.00"))
        item = OrderItem.objects.get(pk=self.item.pk)
        self.assertEqual((item.item_status, item.action_required, item.proposed_unit_price),
                         ("action_required", True, D("300.00")))
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, "action_required")
        event = events(item).get(event_type="action_requested")
        self.assertEqual((event.text_code, event.actor_type, event.actor_name, event.message, event.visible_to_customer,
                          event.text_params["amount"], event.old_value["unit_price"], event.new_value["unit_price"]),
                         ("action_requested_price_change", "operator", "op", "Supplier raised the price", True,
                          "300.00", "260.00", "300.00"))

    def test_validation_per_type(self):
        cases = [("price_change", {}), ("price_change", {"proposed_unit_price": D("260.00")}),
                 ("eta_change", {"proposed_eta_min_days": 5}),
                 ("eta_change", {"proposed_eta_min_days": 9, "proposed_eta_max_days": 5}),
                 ("alternative_required", {"proposed_part_number": "A", "proposed_name": "Same"}),
                 ("alternative_required", {"proposed_part_number": "ALT"}),
                 ("weight_change", {"proposed_unit_price": D("280")}), ("fitment_issue", {}), ("other", {}),
                 ("nonsense", {})]
        for action_type, proposed in cases:
            with self.subTest(action_type=action_type, proposed=proposed), self.assertRaises(OperatorError):
                request_item_action(self.item.pk, action_type, "", **proposed)
        self.assertFalse(OrderItem.objects.get(pk=self.item.pk).action_required)
        self.assertFalse(events(self.item, event_type="action_requested").exists())

    def test_not_on_unpaid_or_closed_orders_and_items(self):
        unpaid = make_order(self.customer)
        with self.assertRaises(OperatorError):
            request_item_action(unpaid.items.get().pk, "other", "x")
        set_item_status(self.item.pk, "cancelled", reason="Out of stock")
        with self.assertRaises(OperatorError):
            request_item_action(self.item.pk, "other", "x")

    def test_weight_change_after_purchase_is_a_notice(self):
        set_item_status(self.item.pk, "purchased")
        request_item_action(self.item.pk, "weight_change", "Heavier than estimated", proposed_weight_kg=D("4.00"),
                            proposed_unit_price=D("300.00"))
        item = OrderItem.objects.get(pk=self.item.pk)
        self.assertEqual((item.item_status, item.action_required), ("purchased", True))
        decide(self.customer, item.pk, "acknowledge")
        self.assertEqual(Order.objects.get(pk=self.order.pk).balance_due, D("40.00"))


class ItemStatusTests(TestCase):
    def setUp(self):
        self.customer = make_customer()
        self.order = paid_order(self.customer)
        self.a, self.b = self.order.items.order_by("id")

    def test_purchased_stamps_purchased_at_and_events_are_visible(self):
        self.assertTrue(set_item_status(self.a.pk, "purchased", actor_name="op"))
        self.assertFalse(set_item_status(self.a.pk, "purchased"))  # same status: nothing happens
        item = OrderItem.objects.get(pk=self.a.pk)
        self.assertIsNotNone(item.purchased_at)
        event = events(item).get(actor_type="operator")
        self.assertEqual((event.text_code, event.actor_type, event.old_value, event.new_value),
                         ("status_purchased", "operator", {"item_status": "payment_confirmed", "action_type": ""},
                          {"item_status": "purchased"}))

    def test_balance_guard(self):
        Order.objects.filter(pk=self.order.pk).update(balance_due=D("10.00"))
        for status in ("ready_for_pickup", "completed"):
            with self.subTest(status=status), self.assertRaises(OperatorError):
                set_item_status(self.a.pk, status)
        self.assertEqual(OrderItem.objects.get(pk=self.a.pk).item_status, "payment_confirmed")
        self.assertTrue(set_item_status(self.a.pk, "ready_for_pickup", bypass_balance_guard=True))

    def test_all_items_completed_completes_the_order(self):
        for item in (self.a, self.b):
            set_item_status(item.pk, "completed")
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, "completed")
        with self.assertRaises(OperatorError):
            set_item_status(self.a.pk, "checking")  # completed orders are closed

    def test_unpaid_order_only_allows_cancelling(self):
        unpaid = make_order(self.customer)
        with self.assertRaises(OperatorError):
            set_item_status(unpaid.items.get().pk, "purchased")

    def test_a_status_change_closes_a_pending_decision(self):
        request_item_action(self.a.pk, "other", "Call us")
        set_item_status(self.a.pk, "purchased")
        item = OrderItem.objects.get(pk=self.a.pk)
        self.assertEqual((item.action_required, item.action_type), (False, ""))
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, "processing")


class OperatorCancellationAndRefundTests(TestCase):
    """D19 + D20: any open order or item, reason required, refunds only what is owed and was collected."""

    def setUp(self):
        self.customer = make_customer()
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")

    def test_item_cancel_after_purchase_refunds_only_its_share(self):
        order = paid_order(self.customer)
        a, b = order.items.order_by("id")
        set_item_status(a.pk, "purchased")
        with self.assertRaises(OperatorError):
            set_item_status(b.pk, "cancelled")  # reason required
        set_item_status(b.pk, "cancelled", actor_name="op", reason="Supplier out of stock")
        order.refresh_from_db()
        self.assertEqual((order.total, order.amount_paid, order.balance_due), (D("260.00"), D("390.00"), D("-130.00")))
        self.assertEqual(events(b).get(actor_type="operator").message, "Supplier out of stock")
        self.assertEqual(record_refund(order.pk, self.admin, "item B"), D("130.00"))
        self.assertEqual(record_refund(order.pk, self.admin, "again"), 0)  # idempotent
        order.refresh_from_db()
        self.assertEqual((order.amount_paid, order.balance_due), (D("260.00"), D("0.00")))
        self.assertEqual(OrderItem.objects.get(pk=b.pk).supplier_price_usd, D("50"))  # history kept

    def test_split_order_item_cancel_owes_nothing_back(self):
        order = make_order(self.customer, payment_option="split_70_30",
                           items=(("A", D("300"), 1), ("B", D("100"), 1)))  # 780 + 260 = 1040
        Order.objects.filter(pk=order.pk).update(amount_due_now=D("728.00"))
        mark_payment(create_payment(Order.objects.get(pk=order.pk), "invoice"), "paid")
        set_item_status(order.items.get(part_number="B").pk, "cancelled", reason="Not available")
        order.refresh_from_db()
        self.assertEqual((order.total, order.amount_paid, order.balance_due), (D("780.00"), D("728.00"), D("52.00")))
        self.assertEqual(record_refund(order.pk, self.admin), 0)

    def test_whole_order_cancel_after_purchase_refunds_at_most_what_was_collected(self):
        order = make_order(self.customer, payment_option="split_70_30", items=(("A", D("100"), 1),))  # 260
        Order.objects.filter(pk=order.pk).update(amount_due_now=D("182.00"))
        mark_payment(create_payment(Order.objects.get(pk=order.pk), "invoice"), "paid")
        balance = create_payment(Order.objects.get(pk=order.pk), "invoice", "balance")  # 78 pending
        set_item_status(order.items.get().pk, "purchased")
        with self.assertRaises(OperatorError):
            cancel_order(order.pk, " ", actor="operator", operator=True)
        cancel_order(order.pk, "Lost parcel", actor="operator", actor_name="op", operator=True)
        order.refresh_from_db()
        self.assertEqual((order.status, order.cancel_reason, order.amount_paid), ("cancelled", "Lost parcel",
                                                                                 D("182.00")))
        self.assertEqual(Payment.objects.get(pk=balance.pk).status, "expired")
        self.assertEqual(record_refund(order.pk, self.admin, "Lost parcel"), D("182.00"))
        self.assertEqual(Refund.objects.get().amount, D("182.00"))

    def test_refunds_are_spread_over_payments_without_exceeding_any(self):
        order = make_order(self.customer, items=(("A", D("100"), 1),))
        Order.objects.filter(pk=order.pk).update(amount_due_now=D("182.00"))
        first = create_payment(Order.objects.get(pk=order.pk), "invoice")
        mark_payment(first, "paid")
        second = create_payment(Order.objects.get(pk=order.pk), "invoice", "balance")
        mark_payment(second, "paid")
        cancel_order(order.pk, "Customer request", actor="operator", operator=True)
        self.assertEqual(record_refund(order.pk, self.admin), D("260.00"))
        self.assertEqual(sorted(Refund.objects.values_list("payment_id", "amount")),
                         sorted([(first.pk, D("182.00")), (second.pk, D("78.00"))]))

    def test_whole_payment_refund_only_when_fully_owed(self):
        order = paid_order(self.customer)
        payment = order.payments.get()
        set_item_status(order.items.order_by("id").last().pk, "cancelled", reason="x")  # owes back 130 of 390
        self.assertEqual(refund_whole_payment(payment, self.admin), "exceeds_owed")
        self.assertEqual(Payment.objects.get(pk=payment.pk).status, "paid")


class AdminParityTests(TestCase):
    """Every admin action gives the same events and money as the service call."""

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        self.client.force_login(self.admin)
        self.customer = make_customer()

    def act(self, url, action, objects, **data):
        payload = {"action": action, "_selected_action": [o.pk for o in objects], **data}
        if data:
            payload["apply"] = "1"
        return self.client.post(url, payload, follow=True, headers={"accept-language": "en"})

    def test_request_decision_matches_the_service(self):
        via_admin, via_service = paid_order(self.customer), paid_order(self.customer)
        page = self.act(ITEMS, "request_decision", [via_admin.items.first()])
        self.assertContains(page, "proposed_unit_price")  # the form comes first
        self.act(ITEMS, "request_decision", [via_admin.items.first()], action_type="price_change",
                 message="New price", proposed_unit_price="300.00")
        request_item_action(via_service.items.first().pk, "price_change", "New price", actor_name="root",
                            proposed_unit_price=D("300.00"))
        a, s = (e.get(event_type="action_requested") for e in (events(via_admin.items.first()),
                                                                 events(via_service.items.first())))
        self.assertEqual((a.text_code, a.text_params, a.actor_name, a.old_value, a.new_value),
                         (s.text_code, s.text_params, s.actor_name, s.old_value, s.new_value))

    def test_status_with_balance_due_shows_an_error_and_changes_nothing(self):
        order = make_order(self.customer, status=Order.STATUS_PROCESSING)  # nothing paid: balance due
        item = order.items.get()
        page = self.act(ITEMS, "change_status", [item], item_status="ready_for_pickup", reason="")
        self.assertContains(page, "The order still has a balance due.")
        self.assertEqual((OrderItem.objects.get(pk=item.pk).item_status, events(item).count()), ("created", 0))

    def test_change_status_and_cancel_order_and_refund_via_admin(self):
        order = paid_order(self.customer)
        a, b = order.items.order_by("id")
        self.act(ITEMS, "change_status", [a], item_status="purchased", reason="")
        self.assertEqual(OrderItem.objects.get(pk=a.pk).item_status, "purchased")
        page = self.act(ORDERS, "cancel_order", [order], reason="")  # invalid form: shown again, nothing done
        self.assertEqual(Order.objects.get(pk=order.pk).status, "processing")
        self.assertContains(page, "reason")
        self.act(ORDERS, "cancel_order", [order], reason="Supplier failure")
        self.assertEqual(Order.objects.get(pk=order.pk).status, "cancelled")
        self.act(ORDERS, "refund_owed", [order], reason="Supplier failure")
        order.refresh_from_db()
        self.assertEqual((order.amount_paid, Refund.objects.get().created_by), (D("0.00"), self.admin))

    def test_courier_fee_and_reply(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        order = paid_order(self.customer)
        Order.objects.filter(pk=order.pk).update(delivery_method="delivery")
        self.act(ORDERS, "propose_courier_fee", [order], fee_gel="15.00", message="Saburtalo")
        proposal = CourierFeeProposal.objects.get()
        self.assertEqual((proposal.amount, proposal.proposed_by, proposal.message), (D("15.00"), self.admin, "Saburtalo"))
        OrderSupportMessage.objects.create(order=order, sender_type="customer", message="When?")
        self.act(ORDERS, "reply_to_customer", [order], message="Tomorrow")
        reply = OrderSupportMessage.objects.get(sender_type="operator")
        self.assertEqual((reply.message, reply.sender_name), ("Tomorrow", "root"))
        self.assertTrue(OrderSupportMessage.objects.get(sender_type="customer").is_read_by_operator)

    def test_invoice_print_and_csv_export(self):
        order = paid_order(self.customer)
        page = self.client.get(f"{ORDERS}{order.pk}/change/")
        self.assertContains(page, f"{ORDERS}{order.pk}/invoice/")
        self.assertContains(self.client.get(f"{ORDERS}{order.pk}/invoice/"), order.order_number)
        csv = self.act(ORDERS, "export_invoice_csv", [order]).content.decode("utf-8-sig").splitlines()
        self.assertEqual(len(csv), 3)  # header + 2 part lines
        self.assertIn(order.order_number, csv[1])
        self.assertIn("260.00", csv[1])

    def test_csv_export_neutralises_formulas_and_invoice_language_is_checked(self):
        customer = make_customer(email="x@example.com", phone="599111222", first_name="=HYPERLINK(\"http://x\")")
        order = paid_order(customer)
        content = self.act(ORDERS, "export_invoice_csv", [order]).content.decode("utf-8-sig")
        cells = [cell for row in csv_module.reader(content.splitlines()) for cell in row]
        self.assertIn("'=HYPERLINK(\"http://x\") Beridze", cells)
        self.assertFalse([c for c in cells if c[:1] in ("=", "+", "@")])
        self.assertEqual(self.client.get(f"{ORDERS}{order.pk}/invoice/?lang=xx").status_code, 200)

    def test_view_only_staff_has_no_operator_actions(self):
        staff = get_user_model().objects.create_user("viewer", "v@example.com", "x", is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(codename__in=["view_order", "view_orderitem"]))
        self.client.force_login(staff)
        order = paid_order(self.customer)
        for url, action in ((ORDERS, "cancel_order"), (ITEMS, "change_status")):
            self.assertNotIn(f'value="{action}"', self.client.get(url).content.decode())
        self.act(ORDERS, "cancel_order", [order], reason="x")
        self.assertEqual(Order.objects.get(pk=order.pk).status, "processing")
        self.assertEqual(self.client.post(f"{ORDERS}{order.pk}/change/", {"status": "cancelled"}).status_code, 403)


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL (CI)")
class ParallelRefundTests(TransactionTestCase):
    serialized_rollback = True

    def test_two_parallel_refunds_record_the_amount_owed_once(self):
        admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        order = paid_order(make_customer())
        cancel_order(order.pk, "x", actor="operator", operator=True)
        barrier = threading.Barrier(2)

        def run():
            barrier.wait()
            try:
                record_refund(order.pk, admin)
            finally:
                connection.close()

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(Refund.objects.values_list("amount", flat=True)), D("390.00"))
        self.assertEqual(Order.objects.get(pk=order.pk).amount_paid, D("0.00"))


class UnpaidItemCancelTests(TestCase):
    """M4 audit fix 1: cancelling an item before the first payment never leaves an out-of-date amount due."""

    def test_amount_due_now_follows_the_new_total_and_old_payments_expire(self):
        order = make_order(make_customer(), payment_option="split_70_30",
                           items=(("A", D("300"), 1), ("B", D("100"), 1)))  # 780 + 260 = 1040
        Order.objects.filter(pk=order.pk).update(amount_due_now=D("728.00"), split_upfront_percent=D("70"))
        invoice = create_payment(Order.objects.get(pk=order.pk), "invoice")
        set_item_status(order.items.get(part_number="A").pk, "cancelled", reason="Not available")
        order.refresh_from_db()
        self.assertEqual((order.total, order.amount_due_now, order.status), (D("260.00"), D("182.00"),
                                                                              "payment_pending"))
        self.assertEqual(Payment.objects.get(pk=invoice.pk).status, "expired")
        retry = create_payment(order, "invoice")
        self.assertEqual(retry.amount, D("182.00"))

    def test_full_payment_terms_use_the_new_total(self):
        order = make_order(make_customer(), items=(("A", D("300"), 1), ("B", D("100"), 1)))
        set_item_status(order.items.get(part_number="A").pk, "cancelled", reason="x")
        self.assertEqual(Order.objects.get(pk=order.pk).amount_due_now, D("260.00"))


class LastItemCancellationTests(TestCase):
    """D22: cancelling the last active item closes the order exactly like a whole-order cancellation."""

    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.customer = make_customer()
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")

    def delivery_order(self, fee=D("15.00")):
        order = paid_order(self.customer)  # 390.00 paid
        Order.objects.filter(pk=order.pk).update(delivery_method="delivery")
        if fee is not None:
            services.propose_courier_fee(order.pk, fee, "Vake")
            services.respond_courier_fee(order.pk, accept=True)
            mark_payment(create_payment(Order.objects.get(pk=order.pk), "invoice", "balance"), "paid")  # +15
        return Order.objects.get(pk=order.pk)

    def test_operator_cancels_the_last_item_after_a_paid_courier_fee(self):
        order = self.delivery_order()
        self.assertEqual((order.total, order.amount_paid), (D("405.00"), D("405.00")))
        a, b = order.items.order_by("id")
        set_item_status(a.pk, "purchased")
        set_item_status(a.pk, "cancelled", actor_name="op", reason="Lost parcel")
        self.assertEqual(Order.objects.get(pk=order.pk).status, "processing")  # B is still active
        set_item_status(b.pk, "cancelled", actor_name="op", reason="Supplier failure")
        order.refresh_from_db()
        self.assertEqual((order.status, order.cancel_reason, order.courier_fee, order.total, order.amount_paid,
                          order.balance_due), ("cancelled", "Supplier failure", D("0.00"), D("0.00"), D("405.00"),
                                               D("-405.00")))
        self.assertIsNotNone(order.cancelled_at)
        audit = OrderItemEvent.objects.get(item__order=order, event_type="order_cancelled")
        self.assertEqual((audit.actor_type, audit.actor_name, audit.visible_to_customer, audit.old_value["courier_fee"],
                          audit.old_value["order_status"], audit.new_value["balance_due"]),
                         ("operator", "op", False, "15.00", "processing", "-405.00"))
        self.assertEqual(CourierFeeProposal.objects.get().status, "accepted")  # history kept
        # the manual refund pays back exactly what was collected, once
        self.assertEqual(record_refund(order.pk, self.admin, "Supplier failure"), D("405.00"))
        self.assertEqual(record_refund(order.pk, self.admin), 0)
        self.assertEqual(Order.objects.get(pk=order.pk).amount_paid, D("0.00"))
        with self.assertRaises(OperatorError):  # closed: a repeat changes nothing
            set_item_status(b.pk, "cancelled", reason="again")
        self.assertEqual(OrderItemEvent.objects.filter(item__order=order, event_type="order_cancelled").count(), 1)

    def test_whole_order_cancel_releases_a_confirmed_courier_fee_too(self):
        order = self.delivery_order()
        cancel_order(order.pk, "Customer request", actor="operator", actor_name="op", operator=True)
        order.refresh_from_db()
        self.assertEqual((order.courier_fee, order.balance_due), (D("0.00"), D("-405.00")))
        self.assertEqual(OrderItemEvent.objects.get(item__order=order, event_type="order_cancelled")
                         .old_value["courier_fee"], "15.00")

    def test_unpaid_order_expires_open_payments_and_a_pending_courier_proposal(self):
        order = make_order(self.customer)
        Order.objects.filter(pk=order.pk).update(delivery_method="delivery")
        invoice = create_payment(order, "invoice")
        Order.objects.filter(pk=order.pk).update(status="processing")
        services.propose_courier_fee(order.pk, D("15.00"), "Vake")
        Order.objects.filter(pk=order.pk).update(status="payment_pending")
        set_item_status(order.items.get().pk, "cancelled", reason="Out of stock")
        order.refresh_from_db()
        self.assertEqual((order.status, order.balance_due, order.courier_fee_action_required),
                         ("cancelled", D("0.00"), False))
        self.assertEqual((Payment.objects.get(pk=invoice.pk).status, CourierFeeProposal.objects.get().status),
                         ("expired", "cancelled"))

    def test_customer_cancel_decision_on_the_last_item(self):
        order = paid_order(self.customer, items=(("A", D("100"), 1),))
        item = order.items.get()
        request_item_action(item.pk, "price_change", "Higher", proposed_unit_price=D("300.00"))
        decide(self.customer, item.pk, "cancel", actor_name="Nino Beridze")
        order.refresh_from_db()
        self.assertEqual((order.status, order.amount_paid, order.balance_due), ("cancelled", D("260.00"),
                                                                                D("-260.00")))
        audit = OrderItemEvent.objects.get(item=item, event_type="order_cancelled")
        self.assertEqual((audit.actor_type, audit.actor_name), ("customer", "Nino Beridze"))

    def test_the_timeline_ends_with_the_cancellation(self):
        order = paid_order(self.customer, items=(("A", D("100"), 1),))
        set_item_status(order.items.get().pk, "cancelled", reason="x")
        client = APIClient()
        client.force_login(self.customer.user)
        body = client.get(f"/api/orders/{order.order_number}/").json()
        self.assertEqual((body["status"], body["payment_status"], body["timeline"][-1]["type"], body["can_cancel"]),
                         ("cancelled", "refund_pending", "cancelled", False))
        self.assertNotIn("order_cancelled", [e["event_type"] for e in body["items"][0]["events"]])  # internal


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL (CI)")
class ParallelLastItemTests(TransactionTestCase):
    serialized_rollback = True

    def test_the_two_last_items_cancelled_in_parallel_close_the_order_once(self):
        order = paid_order(make_customer())
        a, b = order.items.order_by("id")
        barrier = threading.Barrier(2)

        def run(item):
            barrier.wait()
            try:
                set_item_status(item.pk, "cancelled", reason="parallel")
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(i,)) for i in (a, b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        order.refresh_from_db()
        self.assertEqual((order.status, order.balance_due), ("cancelled", D("-390.00")))
        self.assertEqual(OrderItemEvent.objects.filter(item__order=order, event_type="order_cancelled").count(), 1)
