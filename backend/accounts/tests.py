from django.test import TestCase

# Create your tests here.
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Customer


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