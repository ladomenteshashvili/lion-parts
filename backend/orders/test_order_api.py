"""LP-BE-801: order list & detail serializers (G§8.0, G§7.8)."""

from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import make_customer
from orders.models import Order, OrderItem, OrderSupportMessage
from orders.services import create_item_event
from orders.testing import make_order
from payments.services import create_payment, mark_payment

D = Decimal
ORDER_KEYS = {"order_number", "status", "payment_status", "currency", "created_at", "items_count", "delivery_method",
              "pickup_point", "delivery", "courier", "payment_method", "payment_option", "items_total",
              "discount_total", "courier_fee", "total", "amount_due_now", "amount_paid", "balance_due",
              "expected_arrival_from", "expected_arrival_to", "progress", "attention", "can_cancel", "items"}
DETAIL_KEYS = ORDER_KEYS | {"timeline", "payments", "support_messages", "support_unread_count", "invoice_url"}
ITEM_KEYS = {"id", "part_number", "name", "brand", "condition", "quantity", "vin", "list_unit_price",
             "discount_unit_amount", "unit_price", "line_total", "weight_kg", "weight_source", "item_status",
             "eta_min_days", "eta_max_days", "expected_arrival_from", "expected_arrival_to", "action", "events",
             "image_urls"}
COURIER_KEYS = {"fee", "proposed_fee", "action_required", "message", "confirmed_at", "declined_at"}


class OrderApiTestCase(TestCase):
    def setUp(self):
        self.customer = make_customer()
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def detail(self, order, client=None, language="en"):
        return (client or self.client).get(f"/api/orders/{order.order_number}/", headers={"accept-language": language})


class ContractTests(OrderApiTestCase):
    def test_list_and_detail_keys(self):
        order = make_order(self.customer)
        listed = self.client.get("/api/orders/").json()
        self.assertEqual(set(listed["results"][0]), ORDER_KEYS)
        self.assertEqual(set(listed["results"][0]["items"][0]), ITEM_KEYS)
        body = self.detail(order).json()
        self.assertEqual(set(body), DETAIL_KEYS)
        self.assertEqual(set(body["courier"]), COURIER_KEYS)
        self.assertTrue(body["invoice_url"].endswith(f"/api/orders/{order.order_number}/invoice/"))

    def test_owner_only(self):
        order = make_order(self.customer)
        other = APIClient()
        other.force_login(make_customer(email="o@example.com", phone="599000000").user)
        self.assertEqual(self.detail(order, other).status_code, 404)
        self.assertEqual(other.get("/api/orders/").json()["count"], 0)
        self.assertEqual(APIClient().get(f"/api/orders/{order.order_number}/").status_code, 401)

    def test_phone_change_keeps_order_access_and_the_billing_snapshot(self):
        order = make_order(self.customer)
        self.customer.phone, self.customer.first_name = "599111222", "Changed"
        self.customer.save()
        self.assertEqual(self.detail(order).status_code, 200)
        self.assertEqual(Order.objects.get(pk=order.pk).billing_snapshot["first_name"], "Nino")


class QueryCountTests(OrderApiTestCase):
    def queries(self, url):
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(captured)

    def test_detail_queries_do_not_grow_with_items(self):
        small = make_order(self.customer, items=(("A", D("1"), 1),))
        big = make_order(self.customer, items=tuple((f"P{i}", D("1"), 1) for i in range(8)))
        for order in (small, big):
            for item in order.items.all():
                create_item_event(item, "created", text_code="order_item_added", visible=True)
            OrderSupportMessage.objects.create(order=order, item=order.items.first(), message="hi")
            mark_payment(create_payment(order, "invoice"), "paid")
        self.assertEqual(self.queries(f"/api/orders/{small.order_number}/"),
                         self.queries(f"/api/orders/{big.order_number}/"))

    def test_list_queries_do_not_grow_with_orders(self):
        make_order(self.customer)
        one = self.queries("/api/orders/")
        for _ in range(4):
            make_order(self.customer, items=(("A", D("1"), 2), ("B", D("1"), 1)))
        self.assertEqual(self.queries("/api/orders/"), one)


class DetailContentTests(OrderApiTestCase):
    def test_timeline_payments_and_events_in_the_request_language(self):
        order = make_order(self.customer, payment_option="split_70_30")
        Order.objects.filter(pk=order.pk).update(split_upfront_percent=D("70.00"), amount_due_now=D("182.00"))
        order.refresh_from_db()
        item = order.items.get()
        create_item_event(item, "created", text_code="order_item_added", visible=True)
        create_item_event(item, "internal", message="operator note")  # not visible
        mark_payment(create_payment(order, "invoice"), "paid")
        body = self.detail(order).json()
        self.assertEqual([(e["type"], e["title"]) for e in body["timeline"]][:2],
                         [("order_created", "Order placed."), ("item_event", "The part was added to the order.")])
        paid = [e for e in body["timeline"] if e["type"] == "payment_paid"]
        self.assertEqual((paid[0]["title"], paid[0]["amount"]), ("Payment confirmed (70%).", "182.00"))
        self.assertEqual([e["title"] for e in body["items"][0]["events"]],
                         ["The part was added to the order.", "Payment confirmed."])
        self.assertEqual(body["payments"][0]["status"], "paid")
        ka = self.detail(order, language="ka").json()
        self.assertNotEqual(ka["timeline"][0]["title"], "Order placed.")

    def test_support_messages_and_unread_count(self):
        order = make_order(self.customer)
        item = order.items.get()
        OrderSupportMessage.objects.create(order=order, item=item, sender_type="customer", message="When?")
        OrderSupportMessage.objects.create(order=order, sender_type="operator", message="Tomorrow")
        OrderSupportMessage.objects.create(order=order, sender_type="system", text_code="courier_fee_accepted",
                                           text_params={"amount": "10.00", "currency": "USD"})
        OrderSupportMessage.objects.create(order=order, sender_type="operator", message="hidden",
                                           visible_to_customer=False)
        body = self.detail(order).json()
        self.assertEqual([(m["sender_type"], m["message"], m["item_part_number"]) for m in body["support_messages"]],
                         [("customer", "When?", item.part_number), ("operator", "Tomorrow", None),
                          ("system", "Customer accepted the courier fee: $10.00.", None)])
        self.assertEqual(body["support_unread_count"], 2)

    def test_action_and_courier_fee_in_the_order_currency(self):
        order = make_order(self.customer, currency="USD", status=Order.STATUS_PROCESSING)
        Order.objects.filter(pk=order.pk).update(proposed_courier_fee_gel=D("26.00"), exchange_rate=D("2.6"))
        OrderItem.objects.filter(order=order).update(action_required=True, action_type="price_change",
                                                     action_message="New price", proposed_unit_price=D("120.00"),
                                                     item_status="action_required")
        body = self.detail(order).json()
        self.assertEqual(body["courier"]["proposed_fee"], "10.00")
        self.assertEqual(body["items"][0]["action"], {"type": "price_change", "message": "New price",
                                                      "old": {"unit_price": "100.00"}, "new": {"unit_price": "120.00"},
                                                      "decisions": ["accept", "cancel"]})
        OrderItem.objects.filter(order=order).update(action_type="weight_change", purchased_at=timezone.now(),
                                                     proposed_weight_kg=D("3.00"), weight_kg=D("2.00"))
        action = self.detail(order).json()["items"][0]["action"]
        self.assertEqual((action["old"], action["new"], action["decisions"]),
                         ({"weight_kg": "2.00", "unit_price": "100.00"}, {"weight_kg": "3.00", "unit_price": "120.00"},
                          ["acknowledge"]))
