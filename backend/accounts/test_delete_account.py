"""LP-BE-213: account deletion (anonymize) + purge_expired_billing (G§7.15, G§8.3)."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from cart.models import Cart
from accounts.models import Customer, LegalEntityProfile, PhoneVerificationCode
from accounts.testing import PASSWORD, make_customer
from orders.models import Order
from parts.models import PartQuoteRequest, PartSearchLog


def make_order(customer, status=Order.STATUS_COMPLETED, number="LP-2026-00001"):
    return Order.objects.create(
        order_number=number, session_id="s", customer=customer, customer_name=customer.full_name,
        customer_phone=customer.phone or "", payment_type=Order.PAYMENT_FULL, status=status, total_gel=Decimal("10.00"),
    )


def login(email="nino@example.com", password=PASSWORD):
    client = APIClient()
    response = client.post("/api/auth/login/", {"email": email, "password": password}, format="json")
    return client, response


class DeleteAccountTests(TestCase):
    def setUp(self):
        self.customer = make_customer(account_type="legal", personal_id=None, marketing_opt_in=True)
        LegalEntityProfile.objects.create(customer=self.customer, company_official_name="Example LLC",
                                          company_identification_code="404123456")
        self.client, _ = login()

    def delete(self, password=PASSWORD):
        return self.client.delete("/api/profile/", {"current_password": password}, format="json")

    def test_wrong_password_changes_nothing(self):
        response = self.delete("Wrong!pass1")
        self.assertEqual((response.status_code, response.json()["code"]), (400, "invalid_credentials"))
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.anonymized_at)

    def test_open_order_blocks_deletion(self):
        make_order(self.customer, Order.STATUS_PROCESSING)
        response = self.delete()
        self.assertEqual((response.status_code, response.json()["code"]), (409, "account_has_open_orders"))
        self.customer.refresh_from_db()
        self.assertEqual((self.customer.anonymized_at, self.customer.phone), (None, "555123456"))
        self.assertTrue(LegalEntityProfile.objects.exists())

    def test_deletion_anonymizes_and_keeps_orders(self):
        order = make_order(self.customer, Order.STATUS_COMPLETED)
        make_order(self.customer, Order.STATUS_CANCELLED, number="LP-2026-00002")
        other_device, _ = login()
        PhoneVerificationCode.objects.create(session_key="x", phone="555123456", purpose="phone_change",
                                             code_hash="h", expires_at=timezone.now())
        log = PartSearchLog.objects.create(provider="demo", part_number="1", customer=self.customer)
        Cart.objects.create(customer=self.customer)
        PartQuoteRequest.objects.create(customer=self.customer, request_type="weight_price", part_number="1")
        resolved = PartQuoteRequest.objects.create(customer=self.customer, request_type="weight_price",
                                                   part_number="2", status=PartQuoteRequest.STATUS_RESOLVED)

        response = self.delete()
        self.assertEqual((response.status_code, response.content), (204, b""))

        self.customer.refresh_from_db()
        user = self.customer.user
        self.assertEqual((self.customer.first_name, self.customer.last_name), ("Deleted", "Deleted"))
        self.assertEqual((self.customer.phone, self.customer.personal_id, self.customer.marketing_opt_in),
                         (None, None, False))
        self.assertIsNotNone(self.customer.anonymized_at)
        self.assertEqual((user.username, user.email, user.is_active), (f"deleted-{user.pk}@invalid",) * 2 + (False,))
        self.assertFalse(user.has_usable_password())
        self.assertFalse(LegalEntityProfile.objects.exists())
        self.assertFalse(PhoneVerificationCode.objects.exists())
        self.assertFalse(Cart.objects.exists())
        log.refresh_from_db()
        self.assertEqual(log.customer, None)
        self.assertIsNotNone(log.hidden_at)
        self.assertEqual(list(PartQuoteRequest.objects.all()), [resolved])

        order.refresh_from_db()
        self.assertEqual((order.customer_id, order.customer_name, order.customer_phone),
                         (self.customer.pk, "Nino Beridze", "555123456"))  # snapshot kept (5-year retention)

        self.assertFalse(self.client.get("/api/auth/me/").json()["authenticated"])
        self.assertFalse(other_device.get("/api/auth/me/").json()["authenticated"])
        self.assertFalse([s for s in Session.objects.all() if s.get_decoded().get("_auth_user_id") == str(user.pk)])
        self.assertEqual(login()[1].json()["code"], "invalid_credentials")

    @override_settings(SMS_DEMO_MODE=True, ENABLE_DEMO_ENDPOINTS=True)
    def test_email_and_phone_are_free_again(self):
        self.delete()
        payload = {"account_type": "physical", "first_name": "Nino", "last_name": "Beridze",
                   "personal_id": "01234567890", "phone": "555123456", "email": "nino@example.com",
                   "password": PASSWORD, "terms_accepted": True}
        self.assertEqual(APIClient().post("/api/auth/register/", payload, format="json").status_code, 200)

    def test_requires_login(self):
        self.assertEqual(APIClient().delete("/api/profile/", {"current_password": "x"}, format="json").status_code, 401)


class PurgeExpiredBillingTests(TestCase):
    def setUp(self):
        self.gone = make_customer(email="gone@example.com", phone="599000001")
        self.active = make_customer(email="here@example.com", phone="599000002")
        self.old_gone = make_order(self.gone, number="LP-2020-00001")
        self.new_gone = make_order(self.gone, number="LP-2026-00002")
        self.old_active = make_order(self.active, number="LP-2020-00003")
        six_years_ago = timezone.now() - timedelta(days=6 * 365)
        Order.objects.filter(pk__in=[self.old_gone.pk, self.old_active.pk]).update(created_at=six_years_ago)
        Customer.objects.filter(pk=self.gone.pk).update(anonymized_at=timezone.now())

    def run_command(self):
        out = StringIO()
        call_command("purge_expired_billing", stdout=out)
        return out.getvalue()

    def test_only_old_orders_of_anonymized_customers(self):
        self.assertIn("1 order(s)", self.run_command())
        names = {o.pk: o.customer_name for o in Order.objects.all()}
        self.assertEqual(names[self.old_gone.pk], "")
        self.assertEqual(names[self.new_gone.pk], "Nino Beridze")
        self.assertEqual(names[self.old_active.pk], "Nino Beridze")
        self.assertEqual(Order.objects.get(pk=self.old_gone.pk).customer_phone, "")

    def test_idempotent(self):
        self.run_command()
        self.run_command()
        self.assertEqual(Order.objects.get(pk=self.old_gone.pk).customer_name, "")
