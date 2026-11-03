"""LP-BE-209: password reset, request & confirm (G§8.2, G§5.7).
Restores the legacy password_reset_sets_new_password_and_logs_in_session scenario; the new contract
does not log in after a reset."""

import re
from urllib.parse import unquote

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import PASSWORD, make_customer

NEW_PASSWORD = "N3w!passw0rd"


def link_params():
    body = mail.outbox[-1].body
    return {k: unquote(v) for k, v in re.findall(r"[?&](uid|token|lang)=([^&\s]+)", body)}


@override_settings(FRONTEND_BASE_URL="https://lionparts.ge")
class PasswordResetTests(TestCase):
    def setUp(self):
        self.customer = make_customer(preferred_language="ru")
        self.client = APIClient()

    def request_reset(self, email):
        return self.client.post("/api/auth/password-reset/", {"email": email}, format="json")

    def confirm(self, uid, token, password=NEW_PASSWORD):
        return self.client.post("/api/auth/password-reset/confirm/",
                                {"uid": uid, "token": token, "new_password": password}, format="json")

    def test_identical_response_for_known_and_unknown_emails(self):
        known = self.request_reset("NINO@example.com")
        self.assertEqual(len(mail.outbox), 1)
        unknown = self.request_reset("nobody@example.com")
        self.assertEqual(len(mail.outbox), 1)  # nothing sent for an unknown email
        self.assertEqual((known.status_code, known.content), (unknown.status_code, unknown.content))
        self.assertEqual(set(known.json()), {"detail"})

    def test_link_is_in_the_customer_language(self):
        self.request_reset("nino@example.com")
        self.assertIn("https://lionparts.ge/reset-password?uid=", mail.outbox[-1].body)
        self.assertEqual(link_params()["lang"], "ru")

    def test_no_email_for_staff_inactive_or_anonymized(self):
        off = make_customer(email="off@example.com", phone="599000001")
        off.user.is_active = False
        off.user.save()
        make_customer(email="gone@example.com", phone="599000002", anonymized_at=timezone.now())
        for email in ("off@example.com", "gone@example.com"):
            self.request_reset(email)
        self.assertEqual(mail.outbox, [])

    def test_reset_changes_the_password_without_logging_in(self):
        self.request_reset("nino@example.com")
        params = link_params()
        response = self.confirm(params["uid"], params["token"])
        self.assertEqual((response.status_code, set(response.json())), (200, {"detail"}))
        self.assertFalse(self.client.get("/api/auth/me/").json()["authenticated"])
        self.customer.user.refresh_from_db()
        self.assertTrue(self.customer.user.check_password(NEW_PASSWORD))

    def test_token_is_single_use(self):
        self.request_reset("nino@example.com")
        params = link_params()
        self.assertEqual(self.confirm(params["uid"], params["token"]).status_code, 200)
        again = self.confirm(params["uid"], params["token"], "An0ther!pass")
        self.assertEqual((again.status_code, again.json()["code"]), (400, "token_invalid"))

    def test_existing_sessions_stop_working(self):
        other_device = APIClient()
        other_device.post("/api/auth/login/", {"email": "nino@example.com", "password": PASSWORD}, format="json")
        self.assertTrue(other_device.get("/api/auth/me/").json()["authenticated"])
        self.request_reset("nino@example.com")
        params = link_params()
        self.confirm(params["uid"], params["token"])
        self.assertFalse(other_device.get("/api/auth/me/").json()["authenticated"])

    def test_invalid_uid_or_token(self):
        self.request_reset("nino@example.com")
        params = link_params()
        for uid, token in ((params["uid"], "bad-token"), ("!!", params["token"]), ("OTk5OTk", params["token"]), ("", "")):
            with self.subTest(uid=uid, token=token):
                response = self.confirm(uid, token)
                if uid and token:
                    self.assertEqual(response.json()["code"], "token_invalid")
                else:
                    self.assertEqual(response.json()["code"], "validation_error")

    def test_weak_password_is_reported_on_new_password(self):
        self.request_reset("nino@example.com")
        params = link_params()
        response = self.confirm(params["uid"], params["token"], "short")
        self.assertEqual(response.json()["code"], "validation_error")
        codes = {e["code"] for e in response.json()["fields"]["new_password"]}
        self.assertIn("password_too_short", codes)
        self.assertEqual(self.confirm(params["uid"], params["token"]).status_code, 200)  # token still valid

    def test_throttle(self):
        statuses = [self.request_reset("nobody@example.com").status_code for _ in range(6)]
        self.assertEqual(statuses, [200] * 5 + [429])
