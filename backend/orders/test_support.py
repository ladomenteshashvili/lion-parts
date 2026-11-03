"""LP-BE-806: order support messages (G§7.16, G§8.7)."""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.testing import make_customer
from orders.models import OrderSupportMessage
from orders.testing import make_order

D = Decimal


class SupportMessageTests(TestCase):
    def setUp(self):
        self.customer = make_customer()
        self.client = APIClient()
        self.client.force_login(self.customer.user)
        self.order = make_order(self.customer)
        self.url = f"/api/orders/{self.order.order_number}/support/"

    def send(self, client=None, **body):
        return (client or self.client).post(self.url + "messages/", body, format="json")

    def test_message_about_an_item(self):
        item = self.order.items.get()
        response = self.send(message="When will it arrive?", item_id=item.pk)
        self.assertEqual(response.status_code, 201)
        message = response.json()["support_messages"][-1]
        self.assertEqual((message["sender_type"], message["message"], message["item_id"], message["item_part_number"]),
                         ("customer", "When will it arrive?", item.pk, item.part_number))
        self.assertEqual(response.json()["support_unread_count"], 0)  # own messages are never "unread"
        stored = OrderSupportMessage.objects.get()
        self.assertEqual((stored.sender_name, stored.is_read_by_operator), ("Nino Beridze", False))

    def test_item_of_another_order_and_empty_message(self):
        foreign = make_order(make_customer(email="o@example.com", phone="599000000")).items.get()
        self.assertEqual(self.send(message="x", item_id=foreign.pk).json()["fields"]["item_id"][0]["code"], "invalid")
        self.assertEqual(self.send(message="").json()["fields"]["message"][0]["code"], "required")
        self.assertEqual(self.send(message="x" * 2001).status_code, 400)
        self.assertFalse(OrderSupportMessage.objects.exists())

    def test_another_customers_order_is_404(self):
        other = APIClient()
        other.force_login(make_customer(email="o@example.com", phone="599000000").user)
        self.assertEqual(self.send(other, message="hi").status_code, 404)
        self.assertEqual(other.post(self.url + "acknowledge/", {}, format="json").status_code, 404)
        self.assertEqual(self.send(APIClient(), message="hi").status_code, 401)

    def test_acknowledge_clears_the_unread_count(self):
        OrderSupportMessage.objects.create(order=self.order, sender_type="operator", message="Tomorrow")
        OrderSupportMessage.objects.create(order=self.order, sender_type="system", text_code="courier_fee_declined")
        hidden = OrderSupportMessage.objects.create(order=self.order, sender_type="operator", message="internal",
                                                    visible_to_customer=False)
        self.assertEqual(self.client.get(f"/api/orders/{self.order.order_number}/").json()["support_unread_count"], 2)
        body = self.client.post(self.url + "acknowledge/", {}, format="json").json()
        self.assertEqual(body["support_unread_count"], 0)
        self.assertTrue(all(m["is_read_by_customer"] for m in body["support_messages"]))
        self.assertNotIn("internal", [m["message"] for m in body["support_messages"]])
        hidden.refresh_from_db()
        self.assertEqual(hidden.is_read_by_operator, False)
