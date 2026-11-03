"""LP-BE-1202: OTP codes, passwords, reset tokens, API keys, message texts and public link tokens never reach the
logs, whatever the logger (G§3, D23, D24)."""

import logging
import uuid
from unittest import mock

import requests
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.test_password_reset import link_params
from accounts.test_register import PHYSICAL
from accounts.testing import PASSWORD, make_customer
from orders.models import NotificationChannelSetting
from orders.notifications import notify
from orders.testing import make_order

API_KEY = "SENDER-API-KEY-7f3a9c"


class Capture(logging.Handler):
    """Every record of every logger, rendered as a handler would (message + traceback)."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(logging.Formatter("%(name)s %(message)s").format(record))


@override_settings(FRONTEND_BASE_URL="https://lionparts.ge")
class LogSecretsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.capture = Capture()
        self.loggers = [logging.getLogger(), logging.getLogger("django")]  # "django" doesn't propagate to root
        self.levels = [lg.level for lg in self.loggers]
        for lg in self.loggers:
            lg.addHandler(self.capture)
            lg.setLevel(logging.DEBUG)

    def tearDown(self):
        for lg, level in zip(self.loggers, self.levels):
            lg.removeHandler(self.capture)
            lg.setLevel(level)

    def assert_not_logged(self, *secrets):
        logged = "\n".join(self.capture.lines)
        for secret in secrets:
            self.assertNotIn(str(secret), logged)

    @override_settings(SMS_DEMO_MODE=True, ENABLE_DEMO_ENDPOINTS=True, SENDER_GE_ENABLED=False)
    def test_registration_code_and_password(self):
        client = APIClient()
        code = client.post("/api/auth/register/", PHYSICAL, format="json").json()["demo_code"]
        client.post("/api/auth/register/verify-phone/", {"code": "000000" if code != "000000" else "111111"},
                    format="json")
        self.assertTrue(self.capture.lines)  # the SMS-disabled warning and friends were captured
        self.assert_not_logged(code, PHYSICAL["password"])

    def test_wrong_password_and_reset_token(self):
        make_customer(is_phone_verified=True, is_email_verified=True)
        client = APIClient()
        client.post("/api/auth/login/", {"email": "nino@example.com", "password": "Wrong!pass1"}, format="json")
        client.post("/api/auth/password-reset/", {"email": "nino@example.com"}, format="json")
        token = link_params()["token"]
        client.post("/api/auth/password-reset/confirm/", {"uid": "bad", "token": token, "password": "N3w!pass"},
                    format="json")
        self.assert_not_logged("Wrong!pass1", PASSWORD, token, "N3w!pass")

    @override_settings(SENDER_GE_ENABLED=True, SENDER_GE_API_KEY=API_KEY)
    def test_sms_provider_failure_keeps_the_api_key_and_text_out(self):
        customer = make_customer()
        NotificationChannelSetting.objects.filter(event_type="status_update").update(channel="sms")
        with mock.patch("accounts.sms.requests.post", side_effect=requests.ConnectionError(f"apikey={API_KEY}")):
            n = notify(customer, "status_update", order=make_order(customer))
        self.assertEqual(n.deliveries.get().status, "failed")
        self.assert_not_logged(API_KEY, n.token, "Lion Parts: the status")

    def test_public_link_tokens_are_masked_in_request_logs(self):
        tokens = [uuid.uuid4() for _ in range(3)]
        for token in tokens:
            APIClient().get(f"/api/public/n/{token}/")
            APIClient().get(f"/api/public/q/{token}/")
        self.assertTrue(any("<token>" in line for line in self.capture.lines), self.capture.lines)
        self.assert_not_logged(*tokens)

    def test_email_texts_with_links_are_not_logged(self):
        customer = make_customer(preferred_language="en")
        NotificationChannelSetting.objects.filter(event_type="status_update").update(channel="email")
        with mock.patch("django.core.mail.send_mail", side_effect=OSError("smtp down")):
            n = notify(customer, "status_update", order=make_order(customer))
        self.assertEqual(n.deliveries.get().status, "failed")
        self.assert_not_logged(n.token, "https://lionparts.ge/n/")
        self.assertEqual(len(mail.outbox), 0)


class EmailLanguageTests(TestCase):
    """LP-BE-1202 finding: the email layout always said lang="ka"; it now follows the email's language."""

    def test_html_lang_follows_the_email_language(self):
        from api.mail import send_templated_email

        for language in ("ka", "en", "ru"):
            send_templated_email("x@y.ge", "verify_email", {"name": "N", "link": "https://x/verify"}, language)
            self.assertIn(f'<html lang="{language}">', mail.outbox[-1].alternatives[0][0])


class SecurityHeaderTests(TestCase):
    """LP-BE-1202: headers on API responses behind the HTTPS proxy (X-Forwarded-Proto)."""

    @override_settings(SECURE_HSTS_SECONDS=31536000, SECURE_SSL_REDIRECT=False)
    def test_api_responses_carry_the_security_headers(self):
        response = APIClient().get("/api/meta/", headers={"x-forwarded-proto": "https"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertEqual(response["Strict-Transport-Security"], "max-age=31536000")
        self.assertEqual(response["Content-Type"], "application/json")

    def test_browsable_api_is_off(self):
        response = APIClient().get("/api/meta/", headers={"accept": "text/html"})
        self.assertEqual(response["Content-Type"], "application/json")
