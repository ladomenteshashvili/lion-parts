from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Customer, CustomerSession, LegalEntityProfile


def create_customer(session_id, phone="555123456", verified=True):
    customer = Customer.objects.create(
        session_id=session_id,
        name=f"Customer {phone}",
        phone=phone,
        is_phone_verified=verified,
    )
    CustomerSession.objects.create(customer=customer, session_id=session_id)
    return customer


def legal_entity_payload(session_id, **overrides):
    payload = {
        "session_id": session_id,
        "company_identification_code": "405834094",
        "company_official_name": "EZShop LLC",
        "legal_address": "Tbilisi, Georgia",
        "contact_first_name": "Lado",
        "contact_last_name": "Menteshashvili",
        "email": "legal@example.com",
        "mobile_phone": "+995555123456",
    }
    payload.update(overrides)
    return payload


class LegalEntityProfileApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "legal-api-session"
        self.customer = create_customer(self.session_id)

    def test_verified_customer_can_create_legal_entity_profile(self):
        response = self.client.post(
            "/api/accounts/profile/legal-entity/",
            legal_entity_payload(self.session_id),
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        profile = LegalEntityProfile.objects.get(customer=self.customer)

        self.assertEqual(profile.company_identification_code, "405834094")
        self.assertEqual(profile.company_official_name, "EZShop LLC")
        self.assertEqual(profile.legal_address, "Tbilisi, Georgia")
        self.assertEqual(profile.contact_first_name, "Lado")
        self.assertEqual(profile.contact_last_name, "Menteshashvili")
        self.assertEqual(profile.email, "legal@example.com")
        self.assertEqual(profile.mobile_phone, "555123456")
        self.assertTrue(profile.is_mobile_verified)
        self.assertIsNotNone(profile.mobile_verified_at)
        self.assertTrue(profile.is_active)

        legal_entity = response.data["legal_entity"]

        self.assertEqual(legal_entity["company_identification_code"], "405834094")
        self.assertEqual(legal_entity["company_official_name"], "EZShop LLC")
        self.assertEqual(legal_entity["mobile_phone"], "555123456")
        self.assertEqual(legal_entity["is_mobile_verified"], True)

    def test_verified_customer_can_update_same_legal_entity_profile(self):
        self.client.post(
            "/api/accounts/profile/legal-entity/",
            legal_entity_payload(self.session_id),
            format="json",
        )

        response = self.client.put(
            "/api/accounts/profile/legal-entity/",
            legal_entity_payload(
                self.session_id,
                company_official_name="EZShop Updated LLC",
                legal_address="Updated legal address",
                email="updated@example.com",
            ),
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(LegalEntityProfile.objects.count(), 1)

        profile = LegalEntityProfile.objects.get(customer=self.customer)

        self.assertEqual(profile.company_official_name, "EZShop Updated LLC")
        self.assertEqual(profile.legal_address, "Updated legal address")
        self.assertEqual(profile.email, "updated@example.com")

        self.assertEqual(
            response.data["legal_entity"]["company_official_name"],
            "EZShop Updated LLC",
        )

    def test_unverified_customer_cannot_create_legal_entity_profile(self):
        session_id = "unverified-legal-session"
        create_customer(session_id, phone="555654321", verified=False)

        response = self.client.post(
            "/api/accounts/profile/legal-entity/",
            legal_entity_payload(session_id, mobile_phone="+995555654321"),
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["detail"], "phone verification required")
        self.assertEqual(LegalEntityProfile.objects.count(), 0)

    def test_duplicate_company_identification_code_is_rejected(self):
        other_customer = create_customer(
            "other-legal-session",
            phone="555111222",
            verified=True,
        )

        LegalEntityProfile.objects.create(
            customer=other_customer,
            company_identification_code="405834094",
            company_official_name="Other LLC",
            legal_address="Tbilisi",
            contact_first_name="Other",
            contact_last_name="Customer",
            email="other@example.com",
            mobile_phone="555111222",
        )

        response = self.client.post(
            "/api/accounts/profile/legal-entity/",
            legal_entity_payload(self.session_id),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "company identification code already exists",
        )
        self.assertFalse(
            LegalEntityProfile.objects.filter(customer=self.customer).exists()
        )

    def test_duplicate_email_is_rejected(self):
        other_customer = create_customer(
            "other-email-session",
            phone="555111333",
            verified=True,
        )

        LegalEntityProfile.objects.create(
            customer=other_customer,
            company_identification_code="OTHER405",
            company_official_name="Other LLC",
            legal_address="Tbilisi",
            contact_first_name="Other",
            contact_last_name="Customer",
            email="legal@example.com",
            mobile_phone="555111333",
        )

        response = self.client.post(
            "/api/accounts/profile/legal-entity/",
            legal_entity_payload(self.session_id),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "email already exists")
        self.assertFalse(
            LegalEntityProfile.objects.filter(customer=self.customer).exists()
        )

    def test_required_fields_are_validated(self):
        response = self.client.post(
            "/api/accounts/profile/legal-entity/",
            legal_entity_payload(self.session_id, company_official_name=""),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "company_official_name is required",
        )

    def test_invalid_email_is_rejected(self):
        response = self.client.post(
            "/api/accounts/profile/legal-entity/",
            legal_entity_payload(self.session_id, email="not-an-email"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "invalid email")

    def test_different_mobile_phone_is_saved_but_not_verified_yet(self):
        response = self.client.post(
            "/api/accounts/profile/legal-entity/",
            legal_entity_payload(self.session_id, mobile_phone="+995599777777"),
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        profile = LegalEntityProfile.objects.get(customer=self.customer)

        self.assertEqual(profile.mobile_phone, "599777777")
        self.assertFalse(profile.is_mobile_verified)
        self.assertIsNone(profile.mobile_verified_at)
