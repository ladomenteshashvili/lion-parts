"""LP-BE-902: notify() and delivery (G§7.11; D23, D24)."""

import re
import threading
import unittest
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from accounts.testing import make_customer
from orders.models import Notification, NotificationChannelSetting, NotificationDelivery, NotificationTemplate, \
    SupportTicket
from orders.notifications import notify, notify_after_commit, notify_contact
from orders.testing import make_order
from parts.models import PartQuoteRequest

D = Decimal


def channel(event_type, value):
    NotificationChannelSetting.objects.filter(event_type=event_type).update(channel=value)


@override_settings(FRONTEND_BASE_URL="https://lionparts.ge", SENDER_GE_ENABLED=False)
class NotifyTests(TestCase):
    def setUp(self):
        self.customer = make_customer(preferred_language="en")
        self.order = make_order(self.customer)

    def test_channel_both_none_and_sms_failure(self):
        channel("status_update", "both")
        n = notify(self.customer, "status_update", order=self.order)
        # SENDER_GE_ENABLED=False here: the SMS is skipped (nothing left the server), the email really went out
        self.assertEqual(sorted(n.deliveries.values_list("channel", "status")), [("email", "sent"), ("sms", "skipped")])
        self.assertEqual(mail.outbox[-1].to, ["nino@example.com"])
        channel("payment_update", "none")
        self.assertEqual(notify(self.customer, "payment_update", order=self.order).deliveries.count(), 0)
        channel("action_required", "sms")
        with mock.patch("accounts.sms.send_sms", side_effect=RuntimeError("provider down")), \
                self.assertLogs("orders.notifications", "WARNING") as logs:
            n = notify(self.customer, "action_required", order=self.order)
        delivery = n.deliveries.get()
        self.assertEqual((delivery.status, delivery.error), ("failed", "provider down"))
        self.assertNotIn(str(n.token), "".join(logs.output))  # neither the text nor the link token is logged

    def test_links_language_and_text(self):
        channel("status_update", "sms")
        with mock.patch("accounts.sms.send_sms", return_value={"statusId": 1, "messageId": "m1"}) as sms:
            n = notify(self.customer, "status_update", order=self.order)
        text = sms.call_args.args[1]
        self.assertEqual(text, f"Lion Parts: the status of order {self.order.order_number} was updated. "
                               f"https://lionparts.ge/n/{n.token}?lang=en")
        self.assertEqual(n.deliveries.get().provider_message_id, "m1")
        qr = PartQuoteRequest.objects.create(customer=self.customer, request_type="manual_search", part_number="P-9")
        channel("price_ready", "sms")
        with mock.patch("accounts.sms.send_sms", return_value={"statusId": 1}) as sms:
            notify(self.customer, "price_ready", quote_request=qr)
        self.assertTrue(sms.call_args.args[1].endswith(f"https://lionparts.ge/q/{qr.notification_token}?lang=en"))

    def test_language_without_a_template_falls_back_to_georgian(self):
        self.customer.preferred_language = "ru"
        self.customer.save()
        NotificationTemplate.objects.filter(language="ru").delete()
        channel("status_update", "email")
        notify(self.customer, "status_update", order=self.order)
        ka = NotificationTemplate.objects.get(event_type="status_update", language="ka")
        self.assertIn(ka.sms_template.split("{{")[0].strip()[:20], mail.outbox[-1].body)
        self.assertIn("?lang=ru", mail.outbox[-1].body)

    def test_email_holds_no_amounts_names_or_operator_text(self):
        """D24. The link's random token may contain any digits, so amounts and the phone are checked in the
        human-readable text (the link removed); the link itself must be exactly the /n/<uuid>?lang= URL."""
        channel("support_reply", "email")
        n = notify(self.customer, "support_reply", order=self.order, message="Your address Rustaveli 1 is wrong")
        email = mail.outbox[-1]
        text = f"{email.subject}\n{email.body}"
        link = re.compile(r"https://lionparts\.ge/n/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
                          r"\?lang=(?:ka|en|ru)(?=\s|$)")
        self.assertEqual(link.findall(text), [f"https://lionparts.ge/n/{n.token}?lang=en"])
        self.assertEqual(re.findall(r"https?://\S+", text), link.findall(text))  # no other URL anywhere
        readable = link.sub("<link>", text)
        for private in ("Rustaveli", "address", "wrong", "Nino", "Beridze", "nino@example.com"):  # anywhere
            self.assertNotIn(private, text)
        total = f"{self.order.total:.2f}"
        for private in (total, total.split(".")[0], "555123456"):  # amounts and the phone, outside the token
            self.assertNotIn(private, readable)
        self.assertIsNone(re.search(r"\d+[.,]\d{2}\b|₾|\$|\bGEL\b|\bUSD\b", readable), readable)

    def test_the_same_notification_is_not_sent_twice(self):
        channel("payment_update", "sms")
        first = notify(self.customer, "payment_update", order=self.order, params={"payment_id": 1})
        self.assertIsNone(notify(self.customer, "payment_update", order=self.order, params={"payment_id": 1}))
        other = notify(self.customer, "payment_update", order=self.order, params={"payment_id": 2})
        self.assertTrue(first and other)
        self.assertEqual(Notification.objects.count(), 2)

    def test_only_after_commit_and_never_on_rollback(self):
        channel("status_update", "email")
        with self.captureOnCommitCallbacks(execute=True):
            notify_after_commit(self.customer, "status_update", order=self.order)
            self.assertEqual(len(mail.outbox), 0)  # not before the commit
        self.assertEqual(len(mail.outbox), 1)
        try:
            with self.captureOnCommitCallbacks(execute=True), transaction.atomic():
                notify_after_commit(self.customer, "status_update", order=self.order, params={"n": 2})
                raise RuntimeError("rolled back")
        except RuntimeError:
            pass
        self.assertEqual((len(mail.outbox), Notification.objects.count()), (1, 1))

    def test_a_transient_database_error_is_retried_not_lost(self):
        from django.db import OperationalError

        real_create = Notification.objects.create
        calls = []

        def flaky(**kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise OperationalError("deadlock detected")
            return real_create(**kwargs)

        with mock.patch.object(Notification.objects, "create", side_effect=flaky), \
                self.assertLogs("orders.notifications", "WARNING"):
            n = notify(self.customer, "status_update", order=self.order)
        self.assertEqual((len(calls), Notification.objects.filter(pk=n.pk).exists()), (2, True))

    def test_persistent_database_errors_are_reported_as_errors(self):
        from django.db import OperationalError

        with mock.patch.object(Notification.objects, "create", side_effect=OperationalError("deadlock")), \
                self.assertLogs("orders.notifications", "ERROR") as logs:
            self.assertIsNone(notify(self.customer, "status_update", order=self.order))
        self.assertIn("was not created after 3 attempts", "".join(logs.output))

    def test_never_raises_and_anonymized_customers_get_nothing(self):
        with mock.patch("orders.notifications.send", side_effect=RuntimeError("boom")), \
                self.assertLogs("orders.notifications", "ERROR"):
            n = notify(self.customer, "status_update", order=self.order)
        self.assertTrue(Notification.objects.filter(pk=n.pk).exists())  # the in-app notification is kept
        from django.utils import timezone
        self.customer.anonymized_at = timezone.now()
        self.assertIsNone(notify(self.customer, "status_update", order=self.order, params={"x": 1}))


@override_settings(SENDER_GE_ENABLED=False)
class GuestReplyTests(TestCase):
    def test_email_contact_gets_the_reply_in_the_ticket_language(self):
        channel("support_reply", "both")
        ticket = SupportTicket.objects.create(contact="x@y.ge", language="en", context="general", message="Help")
        n = notify_contact(ticket, "We found your part.")
        self.assertEqual(list(n.deliveries.values_list("channel", "recipient")), [("email", "x@y.ge")])
        self.assertEqual(mail.outbox[-1].subject, "Lion Parts: Support reply")
        self.assertIn("We found your part.", mail.outbox[-1].body)

    def test_georgian_guest_reply_text(self):
        from django.utils import translation
        from django.utils.translation import gettext

        channel("support_reply", "email")
        ticket = SupportTicket.objects.create(contact="x@y.ge", language="ka", context="general", message="?")
        notify_contact(ticket, "Found it.")
        with translation.override("ka"):
            intro = gettext("Lion Parts support replied to your request:")
        self.assertNotEqual(intro, "Lion Parts support replied to your request:")
        self.assertNotIn("%(", intro)
        self.assertEqual(mail.outbox[-1].body, f"{intro}\n\nFound it.")  # the translation exactly once

    def test_phone_contact_gets_an_sms_only_when_the_channel_has_sms(self):
        ticket = SupportTicket.objects.create(contact="555123456", language="ka", context="general", message="?")
        channel("support_reply", "email")
        self.assertEqual(notify_contact(ticket, "ok").deliveries.count(), 0)
        channel("support_reply", "sms")
        self.assertEqual(notify_contact(ticket, "ok").deliveries.get().recipient, "555123456")


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL (CI)")
@override_settings(SENDER_GE_ENABLED=False)
class ParallelNotifyTests(TransactionTestCase):
    """M5 audit: the same notification triggered twice at once (e.g. a provider retrying a callback) is
    created and sent once."""

    serialized_rollback = True

    def test_two_parallel_identical_notifications_send_once(self):
        customer = make_customer()
        order = make_order(customer)
        channel("payment_update", "email")
        barrier = threading.Barrier(2)

        def run():
            barrier.wait()
            try:
                notify(customer, "payment_update", order=order, params={"payment_id": 7})
            finally:
                connection.close()

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(Notification.objects.filter(event_type="payment_update").count(), 1)
        self.assertEqual(NotificationDelivery.objects.count(), 1)


class DeliveryStatusTests(TestCase):
    """M5 review: "sent" only when a real provider/backend accepted the message."""

    def setUp(self):
        self.customer = make_customer()
        self.n = Notification.objects.create(customer=self.customer, event_type="status_update")

    def sms(self, **patch):
        from orders.notifications import deliver_sms

        with mock.patch("accounts.sms.send_sms", **patch):
            return deliver_sms(self.n, "555123456", "secret-token-text")

    @override_settings(SENDER_GE_ENABLED=False)
    def test_disabled_sms_is_skipped_and_never_logged_with_its_text(self):
        from orders.notifications import deliver_sms

        with self.assertLogs("accounts.sms", "WARNING") as logs:
            delivery = deliver_sms(self.n, "555123456", "Lion Parts: https://x/n/secret-token")
        self.assertEqual((delivery.status, delivery.provider_message_id), ("skipped", ""))
        self.assertIn("disabled", delivery.error)
        self.assertNotIn("secret-token", "".join(logs.output) + delivery.error)

    def test_provider_accepted(self):
        delivery = self.sms(return_value={"statusId": 1, "messageId": 991})
        self.assertEqual((delivery.status, delivery.provider_message_id, delivery.error), ("sent", "991", ""))

    def test_provider_errors_and_unconfirmed_answers_fail(self):
        from accounts.sms import SenderGeError

        for patch in ({"side_effect": SenderGeError("Sender.ge returned HTTP 500")}, {"return_value": {}},
                      {"return_value": {"statusId": 0}}, {"return_value": None}):
            with self.subTest(patch=patch), self.assertLogs("orders.notifications", "WARNING") as logs:
                delivery = self.sms(**patch)
            self.assertEqual(delivery.status, "failed")
            self.assertNotIn("secret-token-text", "".join(logs.output) + delivery.error)

    def test_email_accepted_none_accepted_and_non_delivering_backends(self):
        from orders.notifications import deliver_email

        self.assertEqual(deliver_email(self.n, "a@b.ge", "S", "B").status, "sent")  # locmem: really in the outbox
        with mock.patch("django.core.mail.send_mail", return_value=0), \
                self.assertLogs("orders.notifications", "WARNING"):
            self.assertEqual(deliver_email(self.n, "a@b.ge", "S", "B").status, "failed")
        for backend in ("console", "dummy"):
            with self.subTest(backend=backend), \
                    override_settings(MAILERS={"default": {
                        "BACKEND": f"django.core.mail.backends.{backend}.EmailBackend"}}):
                delivery = deliver_email(self.n, "a@b.ge", "S", "B")
            self.assertEqual((delivery.status, backend in delivery.error), ("skipped", True))
