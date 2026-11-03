"""LP-BE-904: notification endpoints, badges and the public link (G§8.9, G§8.2; owner decision D23)."""

import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import make_customer
from orders.models import Notification, Order, OrderSupportMessage, SupportTicket
from orders.testing import make_order

D = Decimal
PRIVATE = ("Nino", "Beridze", "555123456", "nino@example.com", "WBA8E9G50GNU12345", "260", "Rustaveli",
           "operator secret")


class NotificationListTests(TestCase):
    def setUp(self):
        self.customer = make_customer()
        self.client = APIClient()
        self.client.force_login(self.customer.user)
        self.order = make_order(self.customer)
        self.item = self.order.items.get()

    def test_list_titles_targets_and_unread_count(self):
        Notification.objects.create(customer=self.customer, event_type="action_required", order=self.order,
                                    item=self.item)
        reply = Notification.objects.create(customer=self.customer, event_type="support_reply", order=self.order,
                                            message="Tomorrow", read_at=timezone.now())
        Notification.objects.create(customer=make_customer(email="o@example.com", phone="599000000"),
                                    event_type="status_update")
        body = self.client.get("/api/notifications/", headers={"accept-language": "en"}).json()
        self.assertEqual((body["count"], body["unread_count"]), (2, 1))
        first, second = body["results"]
        self.assertEqual((first["id"], first["title"], first["message"], first["blocking"], first["target"]),
                         (reply.pk, "Support reply", "Tomorrow", True,
                          {"screen": "order", "order_number": self.order.order_number}))
        self.assertEqual((second["title"], second["target"]), ("Your confirmation is needed", {
            "screen": "decision", "order_number": self.order.order_number, "item_id": self.item.pk}))
        self.assertEqual(set(first), {"id", "event_type", "title", "message", "target", "blocking", "read_at",
                                      "created_at"})
        self.assertEqual(self.client.get("/api/notifications/?unread=1").json()["count"], 1)
        self.assertNotEqual(self.client.get("/api/notifications/", headers={"accept-language": "ka"}).json()
                            ["results"][0]["title"], "Support reply")

    def test_ticket_and_quote_targets(self):
        ticket = SupportTicket.objects.create(customer=self.customer, context="general", message="?")
        Notification.objects.create(customer=self.customer, event_type="support_reply", support_ticket=ticket)
        self.assertEqual(self.client.get("/api/notifications/").json()["results"][0]["target"],
                         {"screen": "support_ticket", "ticket_id": ticket.pk})

    def test_reading_a_support_reply_clears_the_order_unread_count(self):
        message = OrderSupportMessage.objects.create(order=self.order, sender_type="operator", message="Hi")
        n = Notification.objects.create(customer=self.customer, event_type="support_reply", order=self.order,
                                        support_message=message)
        self.assertEqual(self.client.get(f"/api/orders/{self.order.order_number}/").json()["support_unread_count"], 1)
        self.assertEqual(self.client.post(f"/api/notifications/{n.pk}/read/", {}, format="json").status_code, 204)
        self.assertEqual(self.client.get(f"/api/orders/{self.order.order_number}/").json()["support_unread_count"], 0)
        n.refresh_from_db()
        self.assertIsNotNone(n.read_at)
        self.assertEqual(self.client.post(f"/api/notifications/{n.pk}/read/", {}, format="json").status_code, 204)

    def test_other_customers_notifications_are_404_and_anonymous_401(self):
        foreign = Notification.objects.create(customer=make_customer(email="o@example.com", phone="599000000"),
                                              event_type="status_update")
        self.assertEqual(self.client.post(f"/api/notifications/{foreign.pk}/read/", {}, format="json").status_code, 404)
        foreign.refresh_from_db()
        self.assertIsNone(foreign.read_at)
        self.assertEqual(APIClient().get("/api/notifications/").status_code, 401)

    def test_me_badges(self):
        Notification.objects.create(customer=self.customer, event_type="status_update")
        make_order(self.customer, status=Order.STATUS_ACTION_REQUIRED)
        make_order(self.customer, status=Order.STATUS_CANCELLED)
        badges = self.client.get("/api/auth/me/").json()["badges"]
        self.assertEqual(badges, {"search_new": 0, "orders_action": 1, "orders_to_pay": 2, "notifications_unread": 1})
        self.assertIsNone(APIClient().get("/api/auth/me/").json()["badges"])


class PublicLinkTests(TestCase):
    def setUp(self):
        cache.clear()
        self.customer = make_customer()
        self.order = make_order(self.customer)
        self.order.items.update(vin="WBA8E9G50GNU12345")
        Order.objects.filter(pk=self.order.pk).update(delivery_address="Rustaveli 1")
        self.n = Notification.objects.create(customer=self.customer, event_type="support_reply", order=self.order,
                                             item=self.order.items.get(), message="operator secret")

    def get(self, token, **headers):
        return APIClient().get(f"/api/public/n/{token}/", headers=headers)

    def test_minimal_payload_without_private_data(self):
        response = self.get(self.n.token, **{"accept-language": "en"})
        body = response.json()
        self.assertEqual(body, {"event_type": "support_reply", "title": "Support reply", "message": "",
                                "order_number": self.order.order_number, "item_part_number": "PART-1",
                                "target": {"screen": "order", "order_number": self.order.order_number},
                                "requires_login": True})
        for private in PRIVATE:
            self.assertNotIn(private, response.content.decode())

    def test_unknown_malformed_expired_and_guest_tokens_are_the_same_404(self):
        guest = Notification.objects.create(event_type="support_reply")
        Notification.objects.filter(pk=self.n.pk).update(created_at=timezone.now() - timedelta(days=31))
        responses = [self.get(t) for t in (uuid.uuid4(), "not-a-token", self.n.token, guest.token)]
        self.assertEqual({r.status_code for r in responses}, {404})
        self.assertEqual(len({r.content for r in responses}), 1)  # nothing tells them apart

    def test_still_valid_on_day_29(self):
        Notification.objects.filter(pk=self.n.pk).update(created_at=timezone.now() - timedelta(days=29))
        self.assertEqual(self.get(self.n.token).status_code, 200)

    @override_settings(REST_FRAMEWORK={"DEFAULT_THROTTLE_RATES": {"public_link": "3/min"}})
    def test_guessing_is_throttled_per_ip(self):
        from rest_framework.settings import api_settings
        api_settings.reload()
        try:
            codes = [self.get(uuid.uuid4()).status_code for _ in range(4)]
        finally:
            api_settings.reload()
        self.assertEqual(codes, [404, 404, 404, 429])

    def test_tokens_are_masked_in_request_logs(self):
        handler_records = []

        class Capture(logging.Handler):
            def emit(self, record):
                handler_records.append(record.getMessage())

        capture = Capture()
        logging.getLogger("django").addHandler(capture)
        try:
            token = uuid.uuid4()
            self.get(token)
        finally:
            logging.getLogger("django").removeHandler(capture)
        self.assertTrue(handler_records)
        self.assertTrue(all(str(token) not in m for m in handler_records), handler_records)
        self.assertTrue(any("/api/public/n/<token>/" in m for m in handler_records), handler_records)
