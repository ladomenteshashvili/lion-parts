"""LP-BE-1105: payment and delivery log admin (G§10)."""

from decimal import Decimal
from unittest import mock

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings

from accounts.testing import make_customer
from orders.models import Notification, NotificationChannelSetting, NotificationDelivery
from orders.notifications import notify
from orders.testing import make_order
from payments.models import Payment

D = Decimal


class PaymentAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))
        self.order = make_order(make_customer())
        self.payment = Payment.objects.create(order=self.order, kind="initial", provider="fake", amount=D("1"),
                                              currency="GEL", status="unknown")

    def test_status_cannot_be_edited_in_the_change_form(self):
        page = self.client.get(f"/admin/payments/payment/{self.payment.pk}/change/").content.decode()
        self.assertNotIn('name="status"', page)
        self.assertNotIn('name="amount"', page)
        response = self.client.post(f"/admin/payments/payment/{self.payment.pk}/change/",
                                    {"status": "paid", "amount": "999"})
        self.assertEqual(response.status_code, 403)
        self.payment.refresh_from_db()
        self.assertEqual((self.payment.status, self.payment.amount), ("unknown", D("1.00")))

    def test_unknown_payments_are_highlighted(self):
        page = self.client.get("/admin/payments/payment/?status=unknown").content.decode()
        self.assertIn('<strong style="color:#ba2121">unknown</strong>', page)


@override_settings(SENDER_GE_ENABLED=False)
class DeliveryAdminTests(TestCase):
    URL = "/admin/orders/notificationdelivery/"

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        self.client.force_login(self.admin)
        NotificationChannelSetting.objects.filter(event_type="status_update").update(channel="sms")
        with mock.patch("accounts.sms.send_sms", side_effect=RuntimeError("down")):
            customer = make_customer()
            self.notification = notify(customer, "status_update", order=make_order(customer))
        self.failed = self.notification.deliveries.get()

    def resend(self, client=None):
        return (client or self.client).post(self.URL, {"action": "resend", "_selected_action": [self.failed.pk]},
                                            follow=True, headers={"accept-language": "en"})

    def test_resend_creates_a_new_row_and_never_edits_the_old_one(self):
        with mock.patch("accounts.sms.send_sms", return_value={"messageId": "ok"}) as sms:
            self.resend()
        self.assertEqual(sms.call_count, 1)
        self.failed.refresh_from_db()
        self.assertEqual(self.failed.status, "failed")
        self.assertEqual(list(self.notification.deliveries.order_by("id").values_list("status", flat=True)),
                         ["failed", "sent"])
        self.assertTrue(LogEntry.objects.filter(change_message="Resent").exists())

    def test_a_second_resend_within_a_minute_is_refused(self):
        with mock.patch("accounts.sms.send_sms", return_value={}) as sms:
            self.resend()
            page = self.resend()
        self.assertEqual(sms.call_count, 1)
        self.assertContains(page, "already resent in the last minute")

    def test_log_is_read_only_and_resend_needs_permission(self):
        self.assertEqual(self.client.post(f"{self.URL}{self.failed.pk}/change/", {"status": "sent"}).status_code, 403)
        viewer = get_user_model().objects.create_user("viewer", "v@example.com", "x", is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(codename="view_notificationdelivery"))
        client = self.client_class()
        client.force_login(viewer)
        with mock.patch("accounts.sms.send_sms") as sms:
            self.resend(client)
        sms.assert_not_called()
        self.assertEqual(Notification.objects.get().deliveries.count(), 1)
