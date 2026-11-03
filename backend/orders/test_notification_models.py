"""LP-BE-901: notification models and channel settings (G§6.5)."""

from django.test import TestCase

from accounts.services import anonymize_customer
from accounts.testing import make_customer
from orders.models import EVENT_TYPES, Notification, NotificationChannelSetting, SupportTicket


class ChannelSettingTests(TestCase):
    def test_one_setting_per_event_all_off_after_migrate(self):
        self.assertEqual(dict(NotificationChannelSetting.objects.values_list("event_type", "channel")),
                         {event: "none" for event in EVENT_TYPES})
        self.assertEqual(len(EVENT_TYPES), 6)


class TokenTests(TestCase):
    def test_tokens_are_random_and_unique(self):
        customer = make_customer()
        tokens = {Notification.objects.create(customer=customer, event_type="status_update").token
                  for _ in range(20)}
        self.assertEqual(len(tokens), 20)
        self.assertTrue(all(t.version == 4 for t in tokens))  # uuid4: 122 random bits (os.urandom)


class AnonymizeTests(TestCase):
    def test_anonymizing_removes_notifications_and_support_tickets(self):
        customer = make_customer()
        Notification.objects.create(customer=customer, event_type="status_update")
        SupportTicket.objects.create(customer=customer, context="general", message="My address is ...")
        anonymize_customer(customer)
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(SupportTicket.objects.exists())
