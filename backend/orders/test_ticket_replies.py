"""LP-BE-1002: operator replies to tickets and order messages (G§7.16)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import TestCase, override_settings

from accounts.testing import make_customer
from orders.models import Notification, NotificationChannelSetting, SupportTicket
from orders.services import OperatorError
from orders.support import reply_to_ticket

URL = "/admin/orders/supportticket/"


@override_settings(SENDER_GE_ENABLED=False)
class TicketReplyTests(TestCase):
    def setUp(self):
        self.operator = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        NotificationChannelSetting.objects.filter(event_type="support_reply").update(channel="email")

    def reply(self, ticket, text="We found it."):
        with self.captureOnCommitCallbacks(execute=True):
            return reply_to_ticket(ticket.pk, text, self.operator)

    def test_a_guest_reply_is_emailed_to_the_contact(self):
        ticket = SupportTicket.objects.create(contact="x@y.ge", language="en", context="general", message="?")
        self.reply(ticket)
        ticket.refresh_from_db()
        self.assertEqual((ticket.status, ticket.operator_reply, ticket.replied_by), ("answered", "We found it.",
                                                                                   self.operator))
        self.assertIsNotNone(ticket.replied_at)
        self.assertEqual((mail.outbox[-1].to, mail.outbox[-1].subject), (["x@y.ge"], "Lion Parts: Support reply"))
        self.assertIn("We found it.", mail.outbox[-1].body)

    def test_a_customer_reply_is_an_in_app_notification(self):
        customer = make_customer()
        ticket = SupportTicket.objects.create(customer=customer, context="general", message="?")
        self.reply(ticket)
        n = Notification.objects.get()
        self.assertEqual((n.customer, n.event_type, n.support_ticket, n.message), (customer, "support_reply", ticket,
                                                                                 "We found it."))
        self.assertNotIn("We found it.", mail.outbox[-1].body)  # D24: the customer reads it after login

    def test_repeat_closed_and_empty_replies_are_refused(self):
        ticket = SupportTicket.objects.create(contact="x@y.ge", context="general", message="?")
        self.reply(ticket)
        for text in ("We found it.", " "):
            with self.assertRaises(OperatorError):
                self.reply(ticket, text)
        self.assertEqual(len(mail.outbox), 1)
        self.reply(ticket, "Correction: it arrives Friday.")  # a new text is a new reply
        SupportTicket.objects.filter(pk=ticket.pk).update(status="closed")
        with self.assertRaises(OperatorError):
            self.reply(ticket, "Another one")
        self.assertEqual(len(mail.outbox), 2)

    def test_admin_send_reply_and_permissions(self):
        ticket = SupportTicket.objects.create(contact="x@y.ge", context="general", message="?")
        viewer = get_user_model().objects.create_user("viewer", "v@example.com", "x", is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(codename="view_supportticket"))
        self.client.force_login(viewer)
        self.assertNotIn('value="send_reply"', self.client.get(URL).content.decode())
        self.client.post(URL, {"action": "send_reply", "_selected_action": [ticket.pk], "apply": "1",
                               "message": "x"}, follow=True)
        self.assertEqual(SupportTicket.objects.get(pk=ticket.pk).status, "new")
        self.client.force_login(self.operator)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(URL, {"action": "send_reply", "_selected_action": [ticket.pk], "apply": "1",
                                   "message": "Hello"}, follow=True)
        self.assertEqual(SupportTicket.objects.get(pk=ticket.pk).replied_by, self.operator)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.client.post(f"{URL}{ticket.pk}/change/", {"status": "closed"}).status_code, 403)
