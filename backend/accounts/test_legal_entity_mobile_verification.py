from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Customer, CustomerSession, LegalEntityProfile, PhoneVerificationCode


def create_verified_customer(session_id="legal-mobile-session", phone="555123456"):
    customer = Customer.objects.create(
        session_id=session_id,
        name="Legal Mobile Customer",
        phone=phone,
        is_phone_verified=True,
    )
    CustomerSession.objects.create(customer=customer, session_id=session_id)
    return customer


def create_legal_entity(
    customer,
    mobile_phone="599777777",
    verified=False,
    company_identification_code="405834094",
    email="legal@example.com",
):
    return LegalEntityProfile.objects.create(
        customer=customer,
        company_identification_code=company_identification_code,
        company_official_name="EZShop LLC",
        legal_address="Tbilisi, Georgia",
        contact_first_name="Lado",
        contact_last_name="Menteshashvili",
        email=email,
        mobile_phone=mobile_phone,
        is_mobile_verified=verified,
    )


@override_settings(SENDER_GE_ENABLED=False)
class LegalEntityMobileVerificationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "legal-mobile-session"
        self.customer = create_verified_customer(self.session_id)
        self.legal_entity = create_legal_entity(self.customer)

    def test_send_code_creates_pending_legal_entity_mobile_verification(self):
        response = self.client.post(
            "/api/accounts/profile/legal-entity/send-code/",
            {"session_id": self.session_id},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["phone"], "599777777")
        self.assertIn("demo_code", response.data)

        verification = PhoneVerificationCode.objects.get(
            session_id=self.session_id,
            phone="599777777",
            purpose=PhoneVerificationCode.PURPOSE_LEGAL_ENTITY_MOBILE,
        )

        self.assertEqual(verification.status, PhoneVerificationCode.STATUS_PENDING)

    def test_verify_code_marks_legal_entity_mobile_verified(self):
        send_response = self.client.post(
            "/api/accounts/profile/legal-entity/send-code/",
            {"session_id": self.session_id},
            format="json",
        )

        code = send_response.data["demo_code"]

        response = self.client.post(
            "/api/accounts/profile/legal-entity/verify-code/",
            {
                "session_id": self.session_id,
                "code": code,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        self.legal_entity.refresh_from_db()

        self.assertTrue(self.legal_entity.is_mobile_verified)
        self.assertIsNotNone(self.legal_entity.mobile_verified_at)
        self.assertTrue(response.data["legal_entity"]["is_mobile_verified"])

        verification = PhoneVerificationCode.objects.get(
            session_id=self.session_id,
            phone="599777777",
            purpose=PhoneVerificationCode.PURPOSE_LEGAL_ENTITY_MOBILE,
        )

        self.assertEqual(verification.status, PhoneVerificationCode.STATUS_VERIFIED)
        self.assertEqual(verification.attempts, 1)

    def test_wrong_code_does_not_verify_mobile(self):
        self.client.post(
            "/api/accounts/profile/legal-entity/send-code/",
            {"session_id": self.session_id},
            format="json",
        )

        response = self.client.post(
            "/api/accounts/profile/legal-entity/verify-code/",
            {
                "session_id": self.session_id,
                "code": "000000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "invalid verification code")

        self.legal_entity.refresh_from_db()
        self.assertFalse(self.legal_entity.is_mobile_verified)

    def test_unverified_customer_cannot_send_legal_entity_mobile_code(self):
        session_id = "unverified-legal-mobile"
        customer = Customer.objects.create(
            session_id=session_id,
            name="Unverified",
            phone="555888999",
            is_phone_verified=False,
        )
        CustomerSession.objects.create(customer=customer, session_id=session_id)
        create_legal_entity(
            customer,
            mobile_phone="599111222",
            company_identification_code="UNVERIFIED405",
            email="unverified-legal@example.com",
        )

        response = self.client.post(
            "/api/accounts/profile/legal-entity/send-code/",
            {"session_id": session_id},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["detail"], "phone verification required")

    def test_missing_legal_entity_profile_is_rejected(self):
        session_id = "no-legal-profile-session"
        create_verified_customer(session_id, phone="555222333")

        response = self.client.post(
            "/api/accounts/profile/legal-entity/send-code/",
            {"session_id": session_id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "legal entity profile not found")

    def test_already_verified_mobile_returns_without_sending_new_code(self):
        self.legal_entity.is_mobile_verified = True
        self.legal_entity.save(update_fields=["is_mobile_verified", "updated_at"])

        response = self.client.post(
            "/api/accounts/profile/legal-entity/send-code/",
            {"session_id": self.session_id},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["already_verified"])
        self.assertEqual(PhoneVerificationCode.objects.count(), 0)
