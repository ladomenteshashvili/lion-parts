"""LP-BE-206: csrf, login, logout, me (G§8.2). Restores the legacy scenarios
password_login_reuses_customer_from_new_session and same_phone_login_from_new_session_reuses_customer_account."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import PASSWORD, make_customer

CUSTOMER_KEYS = {"id", "account_type", "first_name", "last_name", "personal_id", "company", "phone",
                 "is_phone_verified", "email", "is_email_verified", "is_fully_verified", "tariff",
                 "marketing_opt_in", "preferred_language", "created_at"}


class CsrfFlowTests(TestCase):
    def test_csrf_endpoint_returns_and_sets_the_token(self):
        client = APIClient(enforce_csrf_checks=True)
        response = client.get("/api/auth/csrf/")
        self.assertEqual(set(response.json()), {"csrf_token"})
        self.assertIn("csrftoken", response.cookies)

    def test_login_needs_the_token_and_rotates_it(self):
        make_customer()
        client = APIClient(enforce_csrf_checks=True)
        body = {"email": "nino@example.com", "password": PASSWORD}
        response = client.post("/api/auth/login/", body, format="json")
        self.assertEqual((response.status_code, response.json()["code"]), (403, "csrf_failed"))

        token = client.get("/api/auth/csrf/").json()["csrf_token"]
        response = client.post("/api/auth/login/", body, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200, response.content)
        new_token = response.json()["csrf_token"]
        self.assertNotEqual(new_token, token)
        response = client.post("/api/auth/logout/", {}, format="json", HTTP_X_CSRFTOKEN=new_token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"csrf_token"})


class LoginTests(TestCase):
    def setUp(self):
        self.customer = make_customer()
        self.client = APIClient()

    def login(self, email="nino@example.com", password=PASSWORD, client=None):
        return (client or self.client).post("/api/auth/login/", {"email": email, "password": password}, format="json")

    def test_success_returns_the_customer_and_a_new_session(self):
        self.client.get("/api/auth/me/")
        self.client.session.save()
        guest_key = self.client.session.session_key
        response = self.login(email="  NINO@example.com ")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(set(response.json()), {"customer", "csrf_token"})
        self.assertEqual(set(response.json()["customer"]), CUSTOMER_KEYS)
        self.assertEqual(response.json()["customer"]["id"], self.customer.pk)
        self.assertNotEqual(self.client.cookies["sessionid"].value, guest_key)  # fixation protection
        me = self.client.get("/api/auth/me/").json()
        self.assertEqual((me["authenticated"], me["customer"]["id"]), (True, self.customer.pk))

    def test_login_from_another_session_reuses_the_same_customer(self):
        first = self.login().json()["customer"]["id"]
        second = self.login(client=APIClient()).json()["customer"]["id"]
        self.assertEqual(first, second)

    def test_every_failure_has_the_same_body(self):
        staff = get_user_model().objects.create_user("ops@example.com", "ops@example.com", PASSWORD, is_staff=True)
        inactive = make_customer(email="off@example.com", phone="599000001")
        inactive.user.is_active = False
        inactive.user.save()
        gone = make_customer(email="gone@example.com", phone="599000002", anonymized_at=timezone.now())
        bodies = [
            self.login(email="unknown@example.com"),
            self.login(password="Wrong!pass1"),
            self.login(email=staff.email),
            self.login(email=inactive.user.email),
            self.login(email=gone.user.email),
        ]
        self.assertEqual({r.status_code for r in bodies}, {400})
        self.assertEqual({r.content for r in bodies}, {bodies[0].content})
        self.assertEqual(bodies[0].json()["code"], "invalid_credentials")

    def test_missing_fields_are_validation_errors(self):
        response = self.client.post("/api/auth/login/", {}, format="json")
        self.assertEqual(set(response.json()["fields"]), {"email", "password"})

    def test_logout_invalidates_the_old_session_cookie(self):
        self.login()
        old_cookie = self.client.cookies["sessionid"].value
        self.assertEqual(self.client.post("/api/auth/logout/", {}, format="json").status_code, 200)
        replay = APIClient()
        replay.cookies["sessionid"] = old_cookie
        self.assertFalse(replay.get("/api/auth/me/").json()["authenticated"])

    def test_logout_needs_a_customer(self):
        response = self.client.post("/api/auth/logout/", {}, format="json")
        self.assertEqual((response.status_code, response.json()["code"]), (401, "not_authenticated"))

    def test_me_for_guests(self):
        self.assertEqual(self.client.get("/api/auth/me/").json(),
                         {"authenticated": False, "customer": None, "cart_count": 0, "badges": None})

    def test_me_ignores_staff_sessions(self):
        staff = get_user_model().objects.create_user("ops", "ops@example.com", PASSWORD, is_staff=True)
        self.client.force_login(staff)
        self.assertFalse(self.client.get("/api/auth/me/").json()["authenticated"])

    def test_login_ip_throttle(self):
        statuses = [self.login(password="Wrong!pass1", email=f"u{i}@example.com").status_code for i in range(11)]
        self.assertEqual(statuses[-1], 429)
        self.assertEqual(statuses[:10], [400] * 10)

    def test_login_email_throttle_across_ips(self):
        statuses = [self.login(password="Wrong!pass1", client=APIClient(REMOTE_ADDR=f"10.1.0.{i}")).status_code
                    for i in range(6)]
        self.assertEqual(statuses, [400] * 5 + [429])
