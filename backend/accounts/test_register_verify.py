"""LP-BE-205: registration step 2 (verify phone -> create account -> login), G§8.2 / G§5.7.
Restores the legacy verify_phone_code_* and legal-entity profile scenarios (docs/legacy-test-map.md);
also LP-BE-1303's registration criterion."""

import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Customer, LegalEntityProfile
from accounts.test_login import CUSTOMER_KEYS
from accounts.test_register import LEGAL, PHYSICAL
from accounts.testing import make_customer


@override_settings(SMS_DEMO_MODE=True, ENABLE_DEMO_ENDPOINTS=True, FRONTEND_BASE_URL="https://lionparts.ge")
class VerifyPhoneTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def start(self, payload=PHYSICAL, **headers):
        response = self.client.post("/api/auth/register/", payload, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["demo_code"]

    def verify(self, code):
        return self.client.post("/api/auth/register/verify-phone/", {"code": code}, format="json")

    def test_correct_code_creates_the_account_and_logs_in(self):
        code = self.start()
        guest_key = self.client.cookies["sessionid"].value
        response = self.verify(code)
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(set(body), {"customer", "email_verification_sent", "csrf_token"})
        self.assertEqual(set(body["customer"]), CUSTOMER_KEYS)
        self.assertTrue(body["email_verification_sent"])

        self.assertEqual(get_user_model().objects.count(), 1)
        customer = Customer.objects.get()
        user = customer.user
        self.assertEqual((user.username, user.email), ("nino@example.com", "nino@example.com"))
        self.assertTrue(user.check_password("S3cure!pass"))
        self.assertEqual((customer.phone, customer.personal_id, customer.account_type), ("555123456", "01234567890", "physical"))
        self.assertTrue(customer.is_phone_verified and customer.phone_verified_at and customer.terms_accepted_at)
        self.assertFalse(customer.is_email_verified)
        self.assertIsNone(body["customer"]["company"])

        self.assertNotEqual(self.client.cookies["sessionid"].value, guest_key)  # fixation protection
        self.assertTrue(self.client.get("/api/auth/me/").json()["authenticated"])
        self.assertNotIn("registration_draft", self.client.session)

        message = mail.outbox[-1]
        self.assertEqual(message.to, ["nino@example.com"])
        self.assertRegex(message.body, r"https://lionparts\.ge/verify-email\?token=[^&\s]+&lang=ka")

    def test_legal_account_gets_its_company(self):
        code = self.start(LEGAL)
        company = self.verify(code).json()["customer"]["company"]
        self.assertEqual(company, {"official_name": "Example LLC", "identification_code": "404123456",
                                   "legal_address": ""})
        customer = Customer.objects.get()
        self.assertEqual((customer.account_type, customer.personal_id), ("legal", None))
        self.assertEqual(LegalEntityProfile.objects.get().customer, customer)

    def test_wrong_code_keeps_the_draft_for_a_retry(self):
        code = self.start()
        wrong = "000000" if code != "000000" else "111111"
        response = self.verify(wrong)
        self.assertEqual(response.json(), {"code": "otp_invalid", "detail": response.json()["detail"], "attempts_left": 4})
        self.assertFalse(Customer.objects.exists())
        self.assertIn("registration_draft", self.client.session)
        self.assertEqual(self.verify(code).status_code, 201)

    def test_expired_code(self):
        code = self.start()
        from accounts.models import PhoneVerificationCode
        PhoneVerificationCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.verify(code).json()["code"], "otp_expired")

    def test_missing_or_expired_draft(self):
        self.assertEqual(self.verify("123456").json()["code"], "registration_expired")
        code = self.start()
        session = self.client.session
        session["registration_draft"]["expires_at"] = (timezone.now() - timedelta(seconds=1)).isoformat()
        session.save()
        self.assertEqual(self.verify(code).json()["code"], "registration_expired")

    def test_duplicate_created_after_step_one_returns_409_and_creates_nothing(self):
        code = self.start()
        make_customer(email="nino@example.com", phone="599000000")  # someone else won the race
        response = self.verify(code)
        self.assertEqual((response.status_code, response.json()["code"]), (409, "email_taken"))
        self.assertEqual(Customer.objects.count(), 1)

    def test_preferred_language_from_accept_language(self):
        payload = {**PHYSICAL}
        payload.pop("preferred_language")
        code = self.start(payload, HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(self.verify(code).json()["customer"]["preferred_language"], "en")
        self.assertEqual(Customer.objects.get().preferred_language, "en")
        self.assertIn("lang=en", mail.outbox[-1].body)

    def test_csrf_token_rotates(self):
        token = self.client.get("/api/auth/csrf/").json()["csrf_token"]
        new_token = self.verify(self.start()).json()["csrf_token"]
        self.assertNotEqual(token, new_token)
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9]{32,64}", new_token))
