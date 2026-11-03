"""LP-BE-1102: customer admin (G§10)."""

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase

from accounts.models import Customer
from accounts.testing import make_customer
from orders.testing import make_order

URL = "/admin/accounts/customer/"


class CustomerAdminTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", "x")
        self.client.force_login(self.admin)
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)

    def act(self, action, *customers):
        return self.client.post(URL, {"action": action, "_selected_action": [c.pk for c in customers]}, follow=True,
                                headers={"accept-language": "en"})

    def form(self, **changes):
        data = {"account_type": "physical", "first_name": "Nino", "last_name": "Beridze", "personal_id": "",
                "phone": "555123456", "email": "nino@example.com", "tariff": "", "preferred_language": "ka",
                "company-TOTAL_FORMS": 0, "company-INITIAL_FORMS": 0, "company-MIN_NUM_FORMS": 0,
                "company-MAX_NUM_FORMS": 1}
        return self.client.post(f"{URL}{self.customer.pk}/change/", {**data, **changes},
                                headers={"accept-language": "en"})

    def test_anonymize_uses_the_api_rules_including_the_open_orders_block(self):
        make_order(self.customer)  # payment_pending: open
        page = self.act("anonymize", self.customer)
        self.assertContains(page, "You can delete your account once your active orders are completed.")
        self.assertIsNone(Customer.objects.get(pk=self.customer.pk).anonymized_at)
        self.customer.orders.update(status="completed")
        self.act("anonymize", self.customer)
        customer = Customer.objects.get(pk=self.customer.pk)
        self.assertEqual((customer.first_name, customer.phone), ("Deleted", None))
        self.assertIsNotNone(customer.anonymized_at)
        self.assertTrue(LogEntry.objects.filter(object_id=str(customer.pk), change_message="Anonymized").exists())
        self.assertEqual(self.form(first_name="Back").status_code, 403)  # anonymized: read-only

    def test_mark_email_verified(self):
        other = make_customer(email="o@example.com", phone="599000000")
        self.act("mark_email_verified", other)
        other.refresh_from_db()
        self.assertTrue((other.is_email_verified, other.email_verified_at is not None))

    def test_identity_changes_reset_verification(self):
        response = self.form(phone="+995 599 11 22 33", email="New@Example.com", first_name="Nina")
        self.assertEqual(response.status_code, 302, response.content[:3000])
        customer = Customer.objects.select_related("user").get(pk=self.customer.pk)
        self.assertEqual((customer.phone, customer.first_name, customer.user.email, customer.user.username),
                         ("599112233", "Nina", "new@example.com", "new@example.com"))
        self.assertEqual((customer.is_phone_verified, customer.is_email_verified), (False, False))

    def test_unchanged_identity_keeps_verification_and_duplicates_are_refused(self):
        self.assertEqual(self.form(first_name="Nina").status_code, 302)
        customer = Customer.objects.get(pk=self.customer.pk)
        self.assertEqual((customer.is_phone_verified, customer.is_email_verified), (True, True))
        make_customer(email="taken@example.com", phone="599000001")
        self.assertContains(self.form(email="taken@example.com"), "already linked to another account")
        self.assertEqual(self.form(phone="123").status_code, 200)  # invalid phone: form error

    def test_no_password_or_verification_flags_in_the_form_and_no_delete(self):
        page = self.client.get(f"{URL}{self.customer.pk}/change/").content.decode()
        for field in ('name="password"', 'name="is_phone_verified"', 'name="is_email_verified"'):
            self.assertNotIn(field, page)
        self.assertIn("/admin/auth/user/", page)
        self.assertEqual(self.client.get(f"{URL}{self.customer.pk}/delete/").status_code, 403)

    def test_view_only_staff_cannot_anonymize(self):
        staff = get_user_model().objects.create_user("viewer", "v@example.com", "x", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename="view_customer"))
        self.client.force_login(staff)
        self.act("anonymize", self.customer)
        self.assertIsNone(Customer.objects.get(pk=self.customer.pk).anonymized_at)
