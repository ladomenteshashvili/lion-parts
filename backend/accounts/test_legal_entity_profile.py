from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Customer, CustomerSession, LegalEntityProfile


class LegalEntityProfileRegressionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "legal-entity-session"
        self.customer = Customer.objects.create(
            session_id=self.session_id,
            name="Legal Entity Customer",
            phone="555123456",
            is_phone_verified=True,
        )
        CustomerSession.objects.create(
            customer=self.customer,
            session_id=self.session_id,
        )

    def test_customer_profile_without_legal_entity_returns_null(self):
        response = self.client.get(
            f"/api/accounts/profile/?session_id={self.session_id}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("legal_entity", response.data)
        self.assertIsNone(response.data["legal_entity"])

    def test_customer_profile_includes_legal_entity_data(self):
        LegalEntityProfile.objects.create(
            customer=self.customer,
            company_identification_code="405834094",
            company_official_name="EZShop LLC",
            legal_address="Tbilisi, Georgia",
            contact_first_name="Lado",
            contact_last_name="Menteshashvili",
            email="legal@example.com",
            mobile_phone="555123456",
            is_mobile_verified=False,
            is_active=True,
        )

        response = self.client.get(
            f"/api/accounts/profile/?session_id={self.session_id}",
        )

        self.assertEqual(response.status_code, 200)

        legal_entity = response.data["legal_entity"]

        self.assertEqual(legal_entity["company_identification_code"], "405834094")
        self.assertEqual(legal_entity["company_official_name"], "EZShop LLC")
        self.assertEqual(legal_entity["legal_address"], "Tbilisi, Georgia")
        self.assertEqual(legal_entity["contact_first_name"], "Lado")
        self.assertEqual(legal_entity["contact_last_name"], "Menteshashvili")
        self.assertEqual(legal_entity["email"], "legal@example.com")
        self.assertEqual(legal_entity["mobile_phone"], "555123456")
        self.assertEqual(legal_entity["is_mobile_verified"], False)
        self.assertEqual(legal_entity["is_active"], True)

    def test_customer_can_have_only_one_legal_entity_profile(self):
        LegalEntityProfile.objects.create(
            customer=self.customer,
            company_identification_code="405834094",
            company_official_name="EZShop LLC",
            legal_address="Tbilisi, Georgia",
            contact_first_name="Lado",
            contact_last_name="Menteshashvili",
            email="legal@example.com",
            mobile_phone="555123456",
        )

        with self.assertRaises(Exception):
            LegalEntityProfile.objects.create(
                customer=self.customer,
                company_identification_code="405834095",
                company_official_name="Second Company",
                legal_address="Tbilisi, Georgia",
                contact_first_name="Lado",
                contact_last_name="Menteshashvili",
                email="legal2@example.com",
                mobile_phone="555123456",
            )
