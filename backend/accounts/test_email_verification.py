"""LP-BE-208: email verification, send & verify (G§8.2, G§5.7)."""

import re
import time
from urllib.parse import unquote
from unittest import mock

from django.core import mail, signing
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.services import EMAIL_VERIFY_SALT, send_email_verification
from accounts.testing import make_customer


def token_from_outbox():
    """The link is URL-encoded; the frontend decodes the query parameter before posting it."""
    return unquote(re.search(r"token=([^&\s]+)", mail.outbox[-1].body).group(1))


class SendVerificationTests(TestCase):
    def setUp(self):
        self.customer = make_customer()
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def send(self):
        return self.client.post("/api/auth/email/send-verification/", {}, format="json")

    def test_sends_a_link(self):
        response = self.send()
        self.assertEqual(response.json(), {"sent_to": "nino@example.com", "resend_in": 60})
        self.assertIn("/verify-email?token=", mail.outbox[-1].body)

    def test_already_verified(self):
        self.customer.is_email_verified = True
        self.customer.save()
        response = self.send()
        self.assertEqual((response.status_code, response.json()["code"]), (409, "already_verified"))
        self.assertEqual(mail.outbox, [])

    def test_requires_a_customer(self):
        self.assertEqual(APIClient().post("/api/auth/email/send-verification/", {}, format="json").status_code, 401)

    def test_email_send_throttle(self):
        self.assertEqual([self.send().status_code for _ in range(6)], [200] * 5 + [429])


class VerifyEmailTests(TestCase):
    def setUp(self):
        self.customer = make_customer()
        send_email_verification(self.customer)
        self.token = token_from_outbox()

    def verify(self, token, client=None):
        return (client or APIClient()).post("/api/auth/email/verify/", {"token": token}, format="json")

    def test_verifies_and_is_idempotent(self):
        self.assertEqual(self.verify(self.token).json(), {"verified": True, "customer": None})
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_email_verified and self.customer.email_verified_at)
        first_at = self.customer.email_verified_at
        self.assertEqual(self.verify(self.token).json()["verified"], True)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.email_verified_at, first_at)

    def test_customer_is_returned_only_to_its_own_session(self):
        own = APIClient()
        own.force_login(self.customer.user)
        body = self.verify(self.token, own).json()
        self.assertEqual((body["customer"]["id"], body["customer"]["is_email_verified"]), (self.customer.pk, True))
        other = APIClient()
        other.force_login(make_customer(email="o@example.com", phone="599000000").user)
        self.assertIsNone(self.verify(self.token, other).json()["customer"])

    def invalid(self, token):
        response = self.verify(token)
        self.assertEqual((response.status_code, response.json()["code"]), (400, "token_invalid"))
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_email_verified)

    def test_tampered_token(self):
        self.invalid(self.token[:-2] + ("aa" if not self.token.endswith("aa") else "bb"))
        self.invalid("garbage")
        self.invalid(signing.dumps({"u": self.customer.user.pk, "e": "nino@example.com"}, salt="other-salt"))

    def test_token_older_than_48_hours(self):
        with mock.patch("django.core.signing.time.time", return_value=time.time() + 48 * 3600 + 1):
            self.invalid(self.token)

    def test_token_issued_before_an_email_change(self):
        user = self.customer.user
        user.email = user.username = "new@example.com"
        user.save()
        self.invalid(self.token)

    def test_inactive_or_anonymized_user(self):
        self.customer.anonymized_at = timezone.now()
        self.customer.save()
        self.invalid(self.token)
        self.customer.anonymized_at = None
        self.customer.save()
        self.customer.user.is_active = False
        self.customer.user.save()
        self.invalid(self.token)

    def test_salt_is_the_documented_one(self):
        self.assertEqual(signing.loads(self.token, salt=EMAIL_VERIFY_SALT)["e"], "nino@example.com")
