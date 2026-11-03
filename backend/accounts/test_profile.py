"""LP-BE-210: profile read/update/password (G§8.3, G§8.0) and LP-BE-1303's PATCH criterion.
Restores the legacy get_profile_* and profile_password_* scenarios."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import CustomerTariff, LegalEntityProfile
from accounts.test_login import CUSTOMER_KEYS
from accounts.testing import PASSWORD, make_customer

TARIFF_KEYS = {"name", "payment_terms", "split_upfront_percent", "can_enter_weight", "can_request_quote",
               "benefits_text"}


class ProfileReadTests(TestCase):
    def setUp(self):
        self.customer = make_customer(personal_id="01234567890")
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def test_contract_shape(self):
        data = self.client.get("/api/profile/").json()
        self.assertEqual(set(data), CUSTOMER_KEYS)
        self.assertEqual(set(data["tariff"]), TARIFF_KEYS)
        self.assertEqual(data["tariff"]["split_upfront_percent"], "70.00")
        self.assertEqual((data["company"], data["email"], data["phone"], data["personal_id"]),
                         (None, "nino@example.com", "555123456", "01234567890"))
        self.assertIs(data["is_fully_verified"], False)
        self.assertRegex(data["created_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")

    def test_legal_company_object(self):
        legal = make_customer(email="info@example.ge", phone="599000000", account_type="legal")
        LegalEntityProfile.objects.create(customer=legal, company_official_name="Example LLC",
                                          company_identification_code="404123456", legal_address="Tbilisi")
        self.client.force_login(legal.user)
        self.assertEqual(self.client.get("/api/profile/").json()["company"],
                         {"official_name": "Example LLC", "identification_code": "404123456", "legal_address": "Tbilisi"})

    def test_benefits_text_follows_the_request_language(self):
        tariff = CustomerTariff.objects.get(is_default=True)
        tariff.benefits_text_i18n = {"ka": "ka text", "en": "Free delivery"}
        tariff.save()
        get = lambda lang: self.client.get("/api/profile/", HTTP_ACCEPT_LANGUAGE=lang).json()["tariff"]["benefits_text"]
        self.assertEqual((get("en"), get("ka"), get("ru")), ("Free delivery", "ka text", "ka text"))

    def test_anonymous_and_staff_are_rejected(self):
        self.assertEqual(APIClient().get("/api/profile/").status_code, 401)
        staff = APIClient()
        staff.force_login(get_user_model().objects.create_user("ops", "ops@example.com", "x", is_staff=True))
        self.assertIn(staff.get("/api/profile/").status_code, (401, 403))


class ProfileUpdateTests(TestCase):
    def setUp(self):
        self.customer = make_customer()
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def patch(self, data):
        return self.client.patch("/api/profile/", data, format="json")

    def test_updates_only_the_writable_fields(self):
        response = self.patch({"marketing_opt_in": True, "preferred_language": "en", "first_name": "Hacker",
                               "phone": "599999999", "is_email_verified": True, "tariff": 99})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["marketing_opt_in"], body["preferred_language"]), (True, "en"))
        self.customer.refresh_from_db()
        self.assertEqual((self.customer.first_name, self.customer.phone, self.customer.is_email_verified),
                         ("Nino", "555123456", False))

    def test_unknown_language_is_rejected(self):
        response = self.patch({"preferred_language": "xx"})
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertEqual(response.json()["fields"]["preferred_language"][0]["code"], "invalid")


class PasswordChangeTests(TestCase):
    def setUp(self):
        self.customer = make_customer()
        self.client = APIClient()
        self.client.post("/api/auth/login/", {"email": "nino@example.com", "password": PASSWORD}, format="json")

    def change(self, current, new):
        return self.client.post("/api/profile/password/", {"current_password": current, "new_password": new},
                                format="json")

    def test_wrong_current_password(self):
        response = self.change("Wrong!pass1", "N3w!passw0rd")
        self.assertEqual([e["code"] for e in response.json()["fields"]["current_password"]], ["incorrect"])

    def test_weak_new_password(self):
        response = self.change(PASSWORD, "nino")
        self.assertIn("password_too_short", {e["code"] for e in response.json()["fields"]["new_password"]})

    def test_change_keeps_this_session_and_ends_the_others(self):
        other = APIClient()
        other.post("/api/auth/login/", {"email": "nino@example.com", "password": PASSWORD}, format="json")
        response = self.change(PASSWORD, "N3w!passw0rd")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(set(response.json()), {"customer", "csrf_token"})
        self.assertTrue(self.client.get("/api/auth/me/").json()["authenticated"])
        self.assertFalse(other.get("/api/auth/me/").json()["authenticated"])
        self.customer.user.refresh_from_db()
        self.assertTrue(self.customer.user.check_password("N3w!passw0rd"))
