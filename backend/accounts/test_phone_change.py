"""LP-BE-212: phone change (G§8.3)."""

from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import PhoneVerificationCode
from accounts.testing import make_customer


@override_settings(SMS_DEMO_MODE=True, ENABLE_DEMO_ENDPOINTS=True)
class PhoneChangeTests(TestCase):
    def setUp(self):
        self.customer = make_customer(is_phone_verified=True, preferred_language="ru")
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def start(self, phone="+995 599 11 22 33"):
        return self.client.post("/api/profile/phone/", {"phone": phone}, format="json")

    def verify(self, code):
        return self.client.post("/api/profile/phone/verify/", {"code": code}, format="json")

    def test_phone_changes_only_after_a_correct_code(self):
        with mock.patch("accounts.otp.send_sms", return_value={}) as sms:
            body = self.start().json()
        self.assertEqual(body["masked_phone"], "+995 599 •• •• 33")
        self.assertEqual(sms.call_args.args[0], "599112233")
        self.assertIn("Lion Parts", sms.call_args.args[1])
        self.assertTrue(sms.call_args.args[1].startswith("Код"))  # customer's language (ru)
        code = body["demo_code"]
        self.assertEqual(PhoneVerificationCode.objects.get().customer, self.customer)

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.phone, "555123456")
        wrong = "000000" if code != "000000" else "111111"
        self.assertEqual(self.verify(wrong).json()["code"], "otp_invalid")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.phone, "555123456")

        response = self.verify(code)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["phone"], response.json()["is_phone_verified"]), ("599112233", True))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.phone, "599112233")
        self.assertEqual(self.verify(code).json()["code"], "otp_expired")  # pending phone consumed

    def test_phone_of_another_customer(self):
        make_customer(email="o@example.com", phone="599112233")
        response = self.start()
        self.assertEqual(response.json()["fields"]["phone"][0]["code"], "taken")

    def test_invalid_phone(self):
        self.assertEqual(self.start("0599112233").json()["fields"]["phone"][0]["code"], "invalid_phone")

    def test_number_taken_after_the_code_was_sent(self):
        code = self.start().json()["demo_code"]
        make_customer(email="o@example.com", phone="599112233")
        response = self.verify(code)
        self.assertEqual(response.json()["fields"]["phone"][0]["code"], "taken")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.phone, "555123456")

    def test_verify_without_a_pending_change(self):
        self.assertEqual(self.verify("123456").json()["code"], "otp_expired")

    def test_requires_login(self):
        self.assertEqual(APIClient().post("/api/profile/phone/", {"phone": "599112233"}, format="json").status_code, 401)
