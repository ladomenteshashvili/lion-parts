"""LP-BE-211: email change (G§8.3, G§5.7)."""

import re
import time
from unittest import mock
from urllib.parse import unquote

from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.testing import PASSWORD, make_customer


def login_status(email, password=PASSWORD):
    return APIClient().post("/api/auth/login/", {"email": email, "password": password}, format="json").status_code


@override_settings(FRONTEND_BASE_URL="https://lionparts.ge")
class EmailChangeTests(TestCase):
    def setUp(self):
        self.customer = make_customer(preferred_language="en")
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def request_change(self, new_email="New@Example.com", password=PASSWORD):
        return self.client.post("/api/profile/email/", {"new_email": new_email, "current_password": password},
                                format="json")

    def token(self):
        return unquote(re.search(r"token=([^&\s]+)", mail.outbox[-1].body).group(1))

    def confirm(self, token):
        return APIClient().post("/api/profile/email/confirm/", {"token": token}, format="json")

    def test_full_flow(self):
        response = self.request_change()
        self.assertEqual(response.json(), {"sent_to": "new@example.com", "resend_in": 60})
        message = mail.outbox[-1]
        self.assertEqual(message.to, ["new@example.com"])
        self.assertIn("https://lionparts.ge/profile/email/confirm?token=", message.body)
        self.assertIn("lang=en", message.body)

        # Nothing changes until the new address confirms.
        self.assertEqual((login_status("nino@example.com"), login_status("new@example.com")), (200, 400))

        response = self.confirm(self.token())
        self.assertEqual(response.json(), {"email": "new@example.com"})
        self.assertEqual((login_status("nino@example.com"), login_status("new@example.com")), (400, 200))
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_email_verified)
        self.assertEqual(self.customer.user.username, "new@example.com")

        notice = mail.outbox[-1]
        self.assertEqual(notice.to, ["nino@example.com"])
        self.assertIn("new@example.com", notice.body)

    def test_confirming_twice_is_harmless(self):
        self.request_change()
        token = self.token()
        self.confirm(token)
        sent = len(mail.outbox)
        self.assertEqual(self.confirm(token).json(), {"email": "new@example.com"})
        self.assertEqual(len(mail.outbox), sent)  # no second notice

    def test_validation(self):
        make_customer(email="taken@example.com", phone="599000000")
        response = self.request_change("TAKEN@example.com", "Wrong!pass1")
        fields = {k: [e["code"] for e in v] for k, v in response.json()["fields"].items()}
        self.assertEqual(fields, {"new_email": ["taken"], "current_password": ["incorrect"]})
        self.assertEqual(self.request_change("not-an-email").json()["fields"]["new_email"][0]["code"], "invalid")
        self.assertEqual(mail.outbox, [])

    def test_address_taken_before_confirmation(self):
        self.request_change()
        make_customer(email="new@example.com", phone="599000000")
        response = self.confirm(self.token())
        self.assertEqual((response.status_code, response.json()["code"]), (409, "email_taken"))

    def test_invalid_tokens(self):
        self.request_change()
        token = self.token()
        self.assertEqual(self.confirm(token + "x").json()["code"], "token_invalid")
        with mock.patch("django.core.signing.time.time", return_value=time.time() + 48 * 3600 + 1):
            self.assertEqual(self.confirm(token).json()["code"], "token_invalid")

    def test_link_dies_when_the_email_changed_another_way(self):
        self.request_change("first@example.com")
        stale = self.token()
        self.request_change("second@example.com")
        self.confirm(self.token())
        self.assertEqual(self.confirm(stale).json()["code"], "token_invalid")

    def test_email_verification_link_dies_after_the_change(self):
        from accounts.services import send_email_verification
        send_email_verification(self.customer)
        verify_token = self.token()
        self.request_change()
        self.confirm(self.token())
        response = APIClient().post("/api/auth/email/verify/", {"token": verify_token}, format="json")
        self.assertEqual(response.json()["code"], "token_invalid")

    def test_requires_login(self):
        response = APIClient().post("/api/profile/email/", {"new_email": "x@example.com", "current_password": "x"},
                                    format="json")
        self.assertEqual(response.status_code, 401)
