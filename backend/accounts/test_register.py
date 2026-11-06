"""LP-BE-204: registration step 1 (data + SMS) and resend (G§8.2, G§5.7).
Also restores the legacy scenarios send_phone_verification_code_* (docs/legacy-test-map.md)."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.test import TestCase, override_settings
from django.utils import timezone, translation
from rest_framework.test import APIClient

from accounts.models import Customer, LegalEntityProfile, PhoneVerificationCode
from accounts.serializers import RegisterSerializer
from accounts.sms import SenderGeDeliveryUnknown, SenderGeError
from accounts.testing import make_customer

PHYSICAL = {
    "account_type": "physical", "first_name": "Nino", "last_name": "Beridze", "personal_id": "01234567890",
    "phone": "+995 555 12 34 56", "email": "Nino@Example.com", "password": "S3cure!pass",
    "terms_accepted": True, "marketing_opt_in": False, "preferred_language": "ka",
}
LEGAL = {
    "account_type": "legal", "company_official_name": "Example LLC", "company_identification_code": "404123456",
    "first_name": "Giorgi", "last_name": "Maisuradze", "phone": "555123123", "email": "info@example.ge",
    "password": "S3cure!pass", "terms_accepted": True, "marketing_opt_in": True,
}


def field_codes(response):
    return {field: [e["code"] for e in errors] for field, errors in response.json()["fields"].items()}


@override_settings(SMS_DEMO_MODE=True, ENABLE_DEMO_ENDPOINTS=True)
class RegisterTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def register(self, payload, **headers):
        return self.client.post("/api/auth/register/", payload, format="json", **headers)

    def draft(self):
        return SessionStore(self.client.cookies["sessionid"].value).get("registration_draft")

    def test_physical_registration_sends_a_code_and_creates_nothing(self):
        response = self.register(PHYSICAL)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["masked_phone"], "+995 555 •• •• 56")
        self.assertEqual((body["expires_in"], body["resend_in"]), (300, 60))
        self.assertRegex(body["demo_code"], r"^\d{6}$")
        self.assertFalse(get_user_model().objects.exists())
        self.assertFalse(Customer.objects.exists())
        self.assertEqual(PhoneVerificationCode.objects.get().purpose, "registration")

        draft = self.draft()
        self.assertEqual((draft["email"], draft["phone"], draft["personal_id"]),
                         ("nino@example.com", "555123456", "01234567890"))
        self.assertNotIn("S3cure!pass", str(draft))  # only the hash is stored
        self.assertTrue(draft["password"].startswith(("md5$", "pbkdf2_", "argon2", "bcrypt", "scrypt")))

    def test_legal_registration(self):
        response = self.register(LEGAL)
        self.assertEqual(response.status_code, 200, response.content)
        draft = self.draft()
        self.assertEqual((draft["company_identification_code"], draft["personal_id"]), ("404123456", None))
        self.assertFalse(LegalEntityProfile.objects.exists())

    def test_duplicates_and_terms_come_back_together(self):
        make_customer(email="nino@example.com", phone="555123456", personal_id="01234567890")
        response = self.register({**PHYSICAL, "terms_accepted": False})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertEqual(field_codes(response), {
            "email": ["taken"], "phone": ["taken"], "personal_id": ["taken"], "terms_accepted": ["required"],
        })

    def test_email_uniqueness_ignores_case(self):
        make_customer(email="nino@example.com", phone="599000000")
        self.assertEqual(field_codes(self.register({**PHYSICAL, "email": "NINO@EXAMPLE.COM"})), {"email": ["taken"]})

    def test_duplicate_company_code(self):
        owner = make_customer(email="o@example.com", phone="599000000", account_type="legal")
        LegalEntityProfile.objects.create(customer=owner, company_official_name="X",
                                          company_identification_code="404123456")
        self.assertEqual(field_codes(self.register(LEGAL)), {"company_identification_code": ["taken"]})

    def test_account_type_rules(self):
        cases = [
            ({**PHYSICAL, "personal_id": ""}, {"personal_id": ["required"]}),
            ({**PHYSICAL, "personal_id": "123"}, {"personal_id": ["invalid_personal_id"]}),
            ({**LEGAL, "company_official_name": "", "company_identification_code": ""},
             {"company_official_name": ["required"], "company_identification_code": ["required"]}),
            ({**LEGAL, "company_identification_code": "12345"}, {"company_identification_code": ["invalid_company_id"]}),
        ]
        for payload, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(field_codes(self.register(payload)), expected)

    def test_field_codes_follow_the_contract(self):
        payload = {**PHYSICAL, "account_type": "robot", "first_name": "", "phone": "0555123456",
                   "email": "not-an-email", "preferred_language": "xx"}
        payload.pop("terms_accepted")
        self.assertEqual(field_codes(self.register(payload)), {
            "account_type": ["invalid"], "first_name": ["required"], "phone": ["invalid_phone"],
            "email": ["invalid"], "terms_accepted": ["required"], "preferred_language": ["invalid"],
        })

    def test_password_rules_use_the_entered_email_and_names(self):
        response = self.register({**PHYSICAL, "email": "beridze.nino@example.com", "password": "beridzenino"})
        self.assertEqual(sorted(field_codes(response)["password"]),
                         ["password_no_digit", "password_no_symbol", "password_no_upper", "password_too_similar"])

    def test_preferred_language_defaults_to_the_request_language(self):
        payload = {**PHYSICAL}
        payload.pop("preferred_language")
        self.register(payload, HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(self.draft()["preferred_language"], "en")

    def test_sms_is_sent_in_the_preferred_language(self):
        with mock.patch("accounts.otp.send_sms", return_value={}) as sms:
            code = self.register({**PHYSICAL, "preferred_language": "ru"}).json()["demo_code"]
        self.assertEqual(sms.call_args.args[1], f"Код Lion Parts: {code}")

    def test_validation_errors_are_localized(self):
        response = self.register({**PHYSICAL, "terms_accepted": False}, HTTP_ACCEPT_LANGUAGE="ka")
        with translation.override("ka"):
            expected = str(RegisterSerializer.TERMS_REQUIRED)
        self.assertNotEqual(expected, "You must accept the terms to register.")
        self.assertEqual(response.json()["fields"]["terms_accepted"][0]["message"], expected)

    def test_csrf_is_required(self):
        response = APIClient(enforce_csrf_checks=True).post("/api/auth/register/", PHYSICAL, format="json")
        self.assertEqual((response.status_code, response.json()["code"]), (403, "csrf_failed"))


@override_settings(SMS_DEMO_MODE=True, ENABLE_DEMO_ENDPOINTS=True)
class ResendCodeTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def resend(self):
        return self.client.post("/api/auth/register/resend-code/", {}, format="json")

    def test_without_a_draft(self):
        response = self.resend()
        self.assertEqual((response.status_code, response.json()["code"]), (400, "registration_expired"))

    def test_resend_cooldown_then_new_code(self):
        self.client.post("/api/auth/register/", PHYSICAL, format="json")
        response = self.resend()
        self.assertEqual((response.status_code, response.json()["code"]), (409, "otp_resend_too_soon"))
        self.assertIn("resend_in", response.json())
        PhoneVerificationCode.objects.update(created_at=timezone.now() - timedelta(seconds=61))
        response = self.resend()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["masked_phone"], "+995 555 •• •• 56")

    def test_a_failed_resend_keeps_the_api_contract(self):
        """LPK-222/LPK-193: a rejected SMS can be retried at once; one that may have left answers 409 with resend_in."""
        self.client.post("/api/auth/register/", PHYSICAL, format="json")
        PhoneVerificationCode.objects.update(created_at=timezone.now() - timedelta(seconds=61))
        with mock.patch("accounts.otp.send_sms", side_effect=SenderGeError("rejected")):
            response = self.resend()
        self.assertEqual((response.status_code, response.json()["code"]), (502, "sms_failed"))
        with mock.patch("accounts.otp.send_sms", side_effect=SenderGeDeliveryUnknown("timeout")):
            response = self.resend()
        self.assertEqual((response.status_code, response.json()["code"]), (502, "sms_failed"))
        with mock.patch("accounts.otp.send_sms") as sms:
            response = self.resend()
        sms.assert_not_called()
        self.assertEqual((response.status_code, response.json()["code"]), (409, "otp_resend_too_soon"))
        self.assertIsInstance(response.json()["resend_in"], int)

    def test_expired_draft(self):
        self.client.post("/api/auth/register/", PHYSICAL, format="json")
        session = self.client.session
        session["registration_draft"]["expires_at"] = (timezone.now() - timedelta(seconds=1)).isoformat()
        session.save()
        self.assertEqual(self.resend().json()["code"], "registration_expired")
        self.assertNotIn("registration_draft", self.client.session)  # personal data is not kept


@override_settings(SMS_DEMO_MODE=True, ENABLE_DEMO_ENDPOINTS=True)
class RegisterThrottleTests(TestCase):
    def test_sixth_code_request_for_one_phone_is_throttled_across_sessions(self):
        statuses = []
        for i in range(6):
            client = APIClient(REMOTE_ADDR=f"10.0.0.{i}")
            statuses.append(client.post("/api/auth/register/", PHYSICAL, format="json").status_code)
        self.assertEqual(statuses, [200] * 5 + [429])
