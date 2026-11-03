from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Customer, PhoneVerificationCode

from api.legacy import legacy_endpoint_test


class AccountsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "test-profile-session"

        self.payload = {
            "session_id": self.session_id,
            "customer_name": "Lado Menteshashvili",
            "customer_phone": "+995555123456",
        }

    @legacy_endpoint_test("LP-BE-203..212")
    def test_get_profile_requires_session_id(self):
        response = self.client.get("/api/accounts/profile/")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "session_id is required")

    @legacy_endpoint_test("LP-BE-203..212")
    def test_get_profile_returns_404_when_not_found(self):
        response = self.client.get(
            f"/api/accounts/profile/?session_id={self.session_id}"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["detail"], "profile not found")

    @legacy_endpoint_test("LP-BE-203..212")
    def test_demo_login_requires_session_id(self):
        payload = {
            **self.payload,
            "session_id": "",
        }

        response = self.client.post(
            "/api/accounts/demo-login/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "session_id is required")

    @legacy_endpoint_test("LP-BE-203..212")
    def test_demo_login_requires_customer_name(self):
        payload = {
            **self.payload,
            "customer_name": "",
        }

        response = self.client.post(
            "/api/accounts/demo-login/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "customer_name is required")

    @legacy_endpoint_test("LP-BE-203..212")
    def test_demo_login_requires_customer_phone(self):
        payload = {
            **self.payload,
            "customer_phone": "",
        }

        response = self.client.post(
            "/api/accounts/demo-login/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "customer_phone is required")

    @legacy_endpoint_test("LP-BE-203..212")
    def test_demo_login_creates_profile(self):
        response = self.client.post(
            "/api/accounts/demo-login/",
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        customer = Customer.objects.get(session_id=self.session_id)

        self.assertEqual(customer.name, "Lado Menteshashvili")
        self.assertEqual(customer.phone, "+995555123456")
        self.assertFalse(customer.is_phone_verified)

        self.assertEqual(response.data["session_id"], self.session_id)
        self.assertEqual(response.data["customer_name"], "Lado Menteshashvili")
        self.assertEqual(response.data["customer_phone"], "+995555123456")

    @legacy_endpoint_test("LP-BE-203..212")
    def test_demo_login_updates_existing_profile(self):
        self.client.post(
            "/api/accounts/demo-login/",
            self.payload,
            format="json",
        )

        updated_payload = {
            **self.payload,
            "customer_name": "Updated Name",
            "customer_phone": "+995599999999",
        }

        response = self.client.post(
            "/api/accounts/demo-login/",
            updated_payload,
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(Customer.objects.count(), 1)

        customer = Customer.objects.get(session_id=self.session_id)

        self.assertEqual(customer.name, "Updated Name")
        self.assertEqual(customer.phone, "+995599999999")
        self.assertFalse(customer.is_phone_verified)

    @legacy_endpoint_test("LP-BE-203..212")
    def test_get_profile_returns_saved_profile(self):
        self.client.post(
            "/api/accounts/demo-login/",
            self.payload,
            format="json",
        )

        response = self.client.get(
            f"/api/accounts/profile/?session_id={self.session_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["session_id"], self.session_id)
        self.assertEqual(response.data["customer_name"], "Lado Menteshashvili")
        self.assertEqual(response.data["customer_phone"], "+995555123456")
        self.assertFalse(response.data["is_phone_verified"])

    @legacy_endpoint_test("LP-BE-203..212")
    @override_settings(SENDER_GE_ENABLED=False)
    def test_profile_password_sets_password_for_verified_customer(self):
        customer = Customer.objects.create(
            session_id=self.session_id,
            name="Password Customer",
            phone="555123456",
            is_phone_verified=True,
        )

        response = self.client.post(
            "/api/accounts/profile/password/",
            {
                "session_id": self.session_id,
                "new_password": "Strong1!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        customer.refresh_from_db()

        self.assertTrue(customer.has_password)
        self.assertTrue(customer.check_password("Strong1!"))
        self.assertTrue(response.data["has_password"])

    @legacy_endpoint_test("LP-BE-203..212")
    @override_settings(SENDER_GE_ENABLED=False)
    def test_profile_password_change_requires_current_password(self):
        customer = Customer.objects.create(
            session_id=self.session_id,
            name="Password Customer",
            phone="555123456",
            is_phone_verified=True,
        )
        customer.set_password("Strong1!")

        wrong_response = self.client.post(
            "/api/accounts/profile/password/",
            {
                "session_id": self.session_id,
                "current_password": "Wrong1!",
                "new_password": "Newstrong1!",
            },
            format="json",
        )

        self.assertEqual(wrong_response.status_code, 400)

        correct_response = self.client.post(
            "/api/accounts/profile/password/",
            {
                "session_id": self.session_id,
                "current_password": "Strong1!",
                "new_password": "Newstrong1!",
            },
            format="json",
        )

        self.assertEqual(correct_response.status_code, 200)

        customer.refresh_from_db()

        self.assertTrue(customer.check_password("Newstrong1!"))

    @legacy_endpoint_test("LP-BE-203..212")
    @override_settings(SENDER_GE_ENABLED=False)
    def test_password_reset_sets_new_password_and_logs_in_session(self):
        customer = Customer.objects.create(
            session_id=self.session_id,
            name="Password Customer",
            phone="555123456",
            is_phone_verified=True,
        )
        customer.set_password("Strong1!")

        reset_session_id = "password-reset-session"

        send_response = self.client.post(
            "/api/accounts/password-reset/send-code/",
            {
                "session_id": reset_session_id,
                "customer_phone": "+995555123456",
            },
            format="json",
        )

        self.assertEqual(send_response.status_code, 200)

        reset_response = self.client.post(
            "/api/accounts/password-reset/",
            {
                "session_id": reset_session_id,
                "customer_phone": "+995555123456",
                "code": send_response.data["demo_code"],
                "new_password": "Resetstrong1!",
            },
            format="json",
        )

        self.assertEqual(reset_response.status_code, 200)

        customer.refresh_from_db()

        self.assertTrue(customer.check_password("Resetstrong1!"))
        self.assertTrue(
            CustomerSession.objects.filter(
                customer=customer,
                session_id=reset_session_id,
            ).exists()
        )
