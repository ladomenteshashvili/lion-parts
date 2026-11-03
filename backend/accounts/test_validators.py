"""LP-BE-202: validators & normalizers (G§7.1)."""

from django.contrib.auth import get_user_model
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from accounts.validators import (
    ComplexityValidator, normalize_part_number, normalize_phone, normalize_vin,
    validate_company_id, validate_personal_id,
)


def codes(callable_, *args, **kwargs):
    try:
        callable_(*args, **kwargs)
    except ValidationError as exc:
        return sorted(e.code for e in exc.error_list)
    return []


class PhoneTests(SimpleTestCase):
    def test_accepted_formats(self):
        for value in ("+995 555 12 34 56", "995555123456", "555123456", "555-12-34-56", " (555) 123 456 "):
            with self.subTest(value=value):
                self.assertEqual(normalize_phone(value), "555123456")

    def test_rejected_formats(self):
        for value in ("0555123456", "455123456", "55512345", "5551234567", "", None, "abc"):
            with self.subTest(value=value):
                self.assertEqual(codes(normalize_phone, value), ["invalid_phone"])

    def test_non_strict_only_cleans(self):
        self.assertEqual(normalize_phone("+995 455", strict=False), "455")


class IdTests(SimpleTestCase):
    def test_personal_id(self):
        self.assertEqual(codes(validate_personal_id, "01234567890"), [])
        for value in ("0123456789", "012345678901", "0123456789a", ""):
            self.assertEqual(codes(validate_personal_id, value), ["invalid_personal_id"])

    def test_company_id(self):
        self.assertEqual(codes(validate_company_id, "404123456"), [])
        for value in ("40412345", "4041234567", "40412345x", ""):
            self.assertEqual(codes(validate_company_id, value), ["invalid_company_id"])


class VinAndPartNumberTests(SimpleTestCase):
    def test_vin(self):
        self.assertEqual(normalize_vin(" wbaxg5c51dd123456 "), "WBAXG5C51DD123456")
        self.assertEqual(normalize_vin(""), "")
        self.assertEqual(normalize_vin(None), "")
        for value in ("WBAXG5C51DD12345", "WBAXG5C51DD1234567", "WBAXG5C51DD12345O", "WBAXG5C51DD12345I"):
            self.assertEqual(codes(normalize_vin, value), ["invalid_vin"])

    def test_part_number(self):
        self.assertEqual(normalize_part_number(" 51-11.8 070 648a "), "51118070648A")
        self.assertEqual(normalize_part_number(None), "")


class PasswordTests(SimpleTestCase):
    def test_complexity_codes(self):
        self.assertEqual(codes(ComplexityValidator().validate, "S3cure!pass"), [])
        self.assertEqual(codes(ComplexityValidator().validate, "abc"),
                         ["password_no_digit", "password_no_symbol", "password_no_upper"])
        self.assertEqual(codes(ComplexityValidator().validate, "ABC"),
                         ["password_no_digit", "password_no_lower", "password_no_symbol"])

    def test_validate_password_returns_every_relevant_code(self):
        self.assertEqual(codes(password_validation.validate_password, "abc"), [
            "password_no_digit", "password_no_symbol", "password_no_upper", "password_too_common",
            "password_too_short",
        ])
        self.assertEqual(codes(password_validation.validate_password, "12345678"), [
            "password_entirely_numeric", "password_no_lower", "password_no_symbol", "password_no_upper",
            "password_too_common",
        ])

    def test_similarity_uses_the_unsaved_user(self):
        user = get_user_model()(username="nino.beridze@example.com", email="nino.beridze@example.com")
        self.assertIn("password_too_similar",
                      codes(password_validation.validate_password, "Nino.Beridze1", user))

    def test_help_text_mentions_the_rules(self):
        self.assertTrue(ComplexityValidator().get_help_text())
