"""LP-BE-803: customer decisions (G§7.9)."""

import threading
import unittest
from decimal import Decimal

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import make_customer
from orders.models import Order, OrderItem, OrderItemEvent
from orders.testing import make_order
from payments.services import create_payment, mark_payment

D = Decimal

ACTIONS = {  # type -> (proposed fields, applied fields after accept)
    "price_change": ({"proposed_unit_price": D("300.00")}, {"unit_price": D("300.00"), "list_unit_price": D("300.00")}),
    "eta_change": ({"proposed_eta_min_days": 20, "proposed_eta_max_days": 30}, {"eta_min_days": 20, "eta_max_days": 30}),
    "alternative_required": ({"proposed_part_number": "ALT-1", "proposed_name": "Alt", "proposed_unit_price": D("250.00")},
                             {"part_number": "ALT-1", "name": "Alt", "unit_price": D("250.00")}),
    "fitment_issue": ({}, {"vin": ""}),  # the item is kept as is
    "weight_change": ({"proposed_weight_kg": D("3.50"), "proposed_unit_price": D("280.00")},
                      {"weight_kg": D("3.50"), "weight_source": "operator", "unit_price": D("280.00")}),
    "other": ({}, {}),
}


class DecisionTestCase(TestCase):
    def setUp(self):
        self.customer = make_customer()
        self.client = APIClient()
        self.client.force_login(self.customer.user)
        self.order = make_order(self.customer)  # 1 x 260.00 GEL
        mark_payment(create_payment(self.order, "invoice"), "paid")
        self.item = self.order.items.get()

    def request_action(self, action_type, item=None, purchased=False, **proposed):
        OrderItem.objects.filter(pk=(item or self.item).pk).update(
            action_required=True, action_type=action_type, action_message="Please confirm",
            item_status=OrderItem.STATUS_PURCHASED if purchased else OrderItem.STATUS_ACTION_REQUIRED,
            purchased_at=timezone.now() if purchased else None, **proposed)
        Order.objects.filter(pk=self.order.pk).update(status=Order.STATUS_ACTION_REQUIRED)

    def decide(self, decision, item=None, client=None):
        return (client or self.client).post(f"/api/orders/items/{(item or self.item).pk}/decision/",
                                            {"decision": decision}, format="json", headers={"accept-language": "en"})


class AcceptAndCancelTests(DecisionTestCase):
    def test_accept_each_action_type(self):
        for action_type, (proposed, applied) in ACTIONS.items():
            with self.subTest(action_type=action_type):
                self.request_action(action_type, **proposed)
                response = self.decide("accept")
                self.assertEqual(response.status_code, 200, response.content)
                item = OrderItem.objects.get(pk=self.item.pk)
                for field, value in applied.items():
                    self.assertEqual(getattr(item, field), value, field)
                self.assertEqual((item.item_status, item.action_required, item.action_type, item.proposed_unit_price),
                                 (OrderItem.STATUS_CHECKING, False, "", None))
                body = response.json()
                self.assertIsNone(body["items"][0]["action"])
                self.assertEqual(body["items"][0]["events"][-1]["title"], "You accepted the change.")
                self.assertEqual(body["status"], "processing")

    def test_cancel_each_action_type(self):
        for action_type, (proposed, _) in ACTIONS.items():
            with self.subTest(action_type=action_type):
                order = make_order(self.customer, status=Order.STATUS_PROCESSING,
                                   items=(("KEEP", D("100"), 1), ("DROP", D("50"), 1)))
                drop = order.items.get(part_number="DROP")
                OrderItem.objects.filter(pk=drop.pk).update(action_required=True, action_type=action_type,
                                                            item_status="action_required", **proposed)
                body = self.decide("cancel", item=drop).json()
                self.assertEqual(OrderItem.objects.get(pk=drop.pk).item_status, "cancelled")
                self.assertEqual(body["total"], "260.00")  # only the kept item
                event = OrderItemEvent.objects.filter(item=drop).last()
                self.assertEqual((event.event_type, event.actor_type, event.visible_to_customer, event.message),
                                 ("decision_cancel", "customer", True, ""))

    def test_accepting_a_price_increase_raises_the_balance(self):
        order = make_order(self.customer, status=Order.STATUS_PROCESSING, items=(("A", D("100"), 3),))  # 260 x 3
        mark_payment(create_payment(order, "invoice"), "paid")
        item = order.items.get()
        self.request_action("price_change", item=item, proposed_unit_price=D("300.00"))
        body = self.decide("accept", item=item).json()
        self.assertEqual((body["balance_due"], body["payment_status"]), ("120.00", "partially_paid"))  # (300-260) x 3
        usd = OrderItem.objects.get(pk=item.pk)
        self.assertEqual(usd.unit_price_usd, D("100"))  # the checkout USD snapshot is kept

    def test_price_decrease_becomes_refund_pending_for_exactly_the_difference(self):
        self.request_action("price_change", proposed_unit_price=D("200.00"))
        body = self.decide("accept").json()
        self.assertEqual((body["balance_due"], body["payment_status"]), ("-60.00", "refund_pending"))


class AcknowledgeTests(DecisionTestCase):
    def test_after_purchase_weight_change_is_acknowledge_only(self):
        self.request_action("weight_change", purchased=True, proposed_weight_kg=D("4.00"),
                            proposed_unit_price=D("300.00"))
        self.assertEqual(self.client.get(f"/api/orders/{self.order.order_number}/").json()["items"][0]["action"]
                         ["decisions"], ["acknowledge"])
        for refused in ("accept", "cancel"):
            self.assertEqual(self.decide(refused).json()["code"], "no_pending_action")
        body = self.decide("acknowledge").json()
        item = OrderItem.objects.get(pk=self.item.pk)
        self.assertEqual((item.item_status, item.weight_kg, item.unit_price), ("purchased", D("4.00"), D("300.00")))
        self.assertEqual(body["balance_due"], "40.00")
        self.assertEqual(body["items"][0]["events"][-1]["title"], "You acknowledged the notice.")

    def test_acknowledge_is_not_offered_for_other_actions(self):
        self.request_action("price_change", proposed_unit_price=D("300.00"))
        self.assertEqual(self.decide("acknowledge").status_code, 409)


class GuardTests(DecisionTestCase):
    def test_no_pending_action_is_409_and_a_repeat_changes_nothing(self):
        self.assertEqual((self.decide("accept").status_code, self.decide("accept").json()["code"]),
                         (409, "no_pending_action"))
        self.request_action("price_change", proposed_unit_price=D("300.00"))
        self.assertEqual(self.decide("accept").status_code, 200)
        events = OrderItemEvent.objects.count()
        self.assertEqual(self.decide("accept").status_code, 409)  # double submit
        self.assertEqual(OrderItemEvent.objects.count(), events)

    def test_foreign_item_anonymous_and_bad_decision(self):
        self.request_action("price_change", proposed_unit_price=D("300.00"))
        other = APIClient()
        other.force_login(make_customer(email="o@example.com", phone="599000000").user)
        self.assertEqual(self.decide("accept", client=other).status_code, 404)
        self.assertEqual(self.decide("accept", client=APIClient()).status_code, 401)
        self.assertEqual(self.decide("maybe").json()["fields"]["decision"][0]["code"], "invalid")
        self.assertEqual(OrderItem.objects.get(pk=self.item.pk).action_required, True)

    def test_csrf_is_required(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.customer.user)
        self.request_action("other")
        self.assertEqual(self.decide("accept", client=client).json()["code"], "csrf_failed")


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL (CI)")
class ParallelDecisionTests(TransactionTestCase):
    serialized_rollback = True

    def test_two_parallel_decisions_apply_once(self):
        customer = make_customer()
        order = make_order(customer, status=Order.STATUS_ACTION_REQUIRED)
        item = order.items.get()
        OrderItem.objects.filter(pk=item.pk).update(action_required=True, action_type="price_change",
                                                    proposed_unit_price=D("300.00"), item_status="action_required")
        results, barrier = [], threading.Barrier(2)

        def run(decision):
            client = APIClient()
            client.force_login(customer.user)
            barrier.wait()
            try:
                results.append(client.post(f"/api/orders/items/{item.pk}/decision/", {"decision": decision},
                                           format="json").status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(d,)) for d in ("accept", "cancel")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(OrderItemEvent.objects.filter(item=item, event_type__startswith="decision_").count(), 1)
