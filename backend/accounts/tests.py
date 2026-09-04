from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Customer, PhoneVerificationCode


class AccountsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "test-profile-session"

        self.payload = {
            "session_id": self.session_id,
            "customer_name": "Lado Menteshashvili",
            "customer_phone": "+995555123456",
        }

    def test_get_profile_requires_session_id(self):
        response = self.client.get("/api/accounts/profile/")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "session_id is required")

    def test_get_profile_returns_404_when_not_found(self):
        response = self.client.get(
            f"/api/accounts/profile/?session_id={self.session_id}"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["detail"], "profile not found")

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

    @override_settings(SENDER_GE_ENABLED=False)
    def test_send_phone_verification_code_creates_code(self):
        response = self.client.post(
            "/api/accounts/send-code/",
            {
                "session_id": self.session_id,
                "customer_phone": "+995555123456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["phone"], "555123456")
        self.assertIn("demo_code", response.data)

        verification = PhoneVerificationCode.objects.get(
            session_id=self.session_id,
            phone="555123456",
        )

        self.assertEqual(verification.status, PhoneVerificationCode.STATUS_PENDING)
        self.assertTrue(verification.check_code(response.data["demo_code"]))

    @override_settings(SENDER_GE_ENABLED=False)
    def test_send_phone_verification_code_rejects_invalid_phone(self):
        response = self.client.post(
            "/api/accounts/send-code/",
            {
                "session_id": self.session_id,
                "customer_phone": "123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(PhoneVerificationCode.objects.count(), 0)

    @override_settings(SENDER_GE_ENABLED=False)
    def test_send_phone_verification_code_reuses_recent_code(self):
        first_response = self.client.post(
            "/api/accounts/send-code/",
            {
                "session_id": self.session_id,
                "customer_phone": "+995555123456",
            },
            format="json",
        )

        self.assertEqual(first_response.status_code, 200)

        second_response = self.client.post(
            "/api/accounts/send-code/",
            {
                "session_id": self.session_id,
                "customer_phone": "+995555123456",
            },
            format="json",
        )

        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.data["detail"], "verification code already sent")
        self.assertTrue(second_response.data["already_sent"])
        self.assertEqual(second_response.data["phone"], "555123456")
        self.assertIn("expires_in_seconds", second_response.data)
        self.assertEqual(PhoneVerificationCode.objects.count(), 1)

    @override_settings(SENDER_GE_ENABLED=False)
    def test_verify_phone_code_creates_verified_customer(self):
        send_response = self.client.post(
            "/api/accounts/send-code/",
            {
                "session_id": self.session_id,
                "customer_phone": "+995555123456",
            },
            format="json",
        )

        code = send_response.data["demo_code"]

        verify_response = self.client.post(
            "/api/accounts/verify-code/",
            {
                "session_id": self.session_id,
                "customer_name": "Lado Menteshashvili",
                "customer_phone": "+995555123456",
                "code": code,
            },
            format="json",
        )

        self.assertEqual(verify_response.status_code, 200)

        customer = Customer.objects.get(session_id=self.session_id)

        self.assertEqual(customer.name, "Lado Menteshashvili")
        self.assertEqual(customer.phone, "555123456")
        self.assertTrue(customer.is_phone_verified)

        verification = PhoneVerificationCode.objects.get(
            session_id=self.session_id,
            phone="555123456",
        )

        self.assertEqual(verification.status, PhoneVerificationCode.STATUS_VERIFIED)
        self.assertIsNotNone(verification.verified_at)

    @override_settings(SENDER_GE_ENABLED=False)
    def test_verify_phone_code_rejects_wrong_code(self):
        self.client.post(
            "/api/accounts/send-code/",
            {
                "session_id": self.session_id,
                "customer_phone": "+995555123456",
            },
            format="json",
        )

        response = self.client.post(
            "/api/accounts/verify-code/",
            {
                "session_id": self.session_id,
                "customer_phone": "+995555123456",
                "code": "000000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "invalid verification code")

        verification = PhoneVerificationCode.objects.get()
        self.assertEqual(verification.attempts, 1)

    def test_verify_phone_code_rejects_expired_code(self):
        verification = PhoneVerificationCode(
            session_id=self.session_id,
            phone="555123456",
            purpose=PhoneVerificationCode.PURPOSE_LOGIN,
            status=PhoneVerificationCode.STATUS_PENDING,
            max_attempts=5,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        verification.set_code("123456")
        verification.save()

        response = self.client.post(
            "/api/accounts/verify-code/",
            {
                "session_id": self.session_id,
                "customer_phone": "555123456",
                "code": "123456",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "verification code expired")

        verification.refresh_from_db()
        self.assertEqual(verification.status, PhoneVerificationCode.STATUS_EXPIRED)

    @override_settings(SENDER_GE_ENABLED=False)
    def test_verify_phone_code_uses_existing_customer_name_without_requiring_name(self):
        Customer.objects.create(
            session_id="old-session",
            name="Existing Customer",
            phone="555123456",
            is_phone_verified=True,
            can_request_quote=True,
        )

        send_response = self.client.post(
            "/api/accounts/send-code/",
            {
                "session_id": self.session_id,
                "customer_phone": "+995555123456",
            },
            format="json",
        )

        code = send_response.data["demo_code"]

        verify_response = self.client.post(
            "/api/accounts/verify-code/",
            {
                "session_id": self.session_id,
                "customer_phone": "+995555123456",
                "code": code,
            },
            format="json",
        )

        self.assertEqual(verify_response.status_code, 200)
        self.assertEqual(verify_response.data["customer_name"], "Existing Customer")
        self.assertEqual(verify_response.data["customer_phone"], "555123456")
        self.assertTrue(verify_response.data["is_phone_verified"])

        customer = Customer.objects.get(session_id=self.session_id)
        self.assertEqual(customer.name, "Existing Customer")
        self.assertEqual(customer.phone, "555123456")
        self.assertTrue(customer.is_phone_verified)
        self.assertTrue(customer.can_request_quote)

    @override_settings(SENDER_GE_ENABLED=False)
    def test_verify_phone_code_requires_name_for_new_phone(self):
        send_response = self.client.post(
            "/api/accounts/send-code/",
            {
                "session_id": self.session_id,
                "customer_phone": "+995555123456",
            },
            format="json",
        )

        code = send_response.data["demo_code"]

        first_verify_response = self.client.post(
            "/api/accounts/verify-code/",
            {
                "session_id": self.session_id,
                "customer_phone": "+995555123456",
                "code": code,
            },
            format="json",
        )

        self.assertEqual(first_verify_response.status_code, 200)
        self.assertTrue(first_verify_response.data["requires_customer_name"])
        self.assertEqual(first_verify_response.data["phone"], "555123456")
        self.assertEqual(Customer.objects.count(), 0)

        verification = PhoneVerificationCode.objects.get()
        self.assertEqual(verification.status, PhoneVerificationCode.STATUS_PENDING)

        second_verify_response = self.client.post(
            "/api/accounts/verify-code/",
            {
                "session_id": self.session_id,
                "customer_name": "New Customer",
                "customer_phone": "+995555123456",
                "code": code,
            },
            format="json",
        )

        self.assertEqual(second_verify_response.status_code, 200)
        self.assertEqual(second_verify_response.data["customer_name"], "New Customer")
        self.assertEqual(second_verify_response.data["customer_phone"], "555123456")
        self.assertTrue(second_verify_response.data["is_phone_verified"])

        verification.refresh_from_db()
        self.assertEqual(verification.status, PhoneVerificationCode.STATUS_VERIFIED)

