"""Normalizers and validators (G§7.1). Codes are the field error codes of G§9."""

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

PHONE_RE = re.compile(r"^5\d{8}$")
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def normalize_phone(value, strict=True):
    """Any Georgian mobile format -> 9 digits ("+995 555 12 34 56" -> "555123456").
    strict=False only cleans the value (for lookups); it never raises."""
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("995"):
        digits = digits[3:]
    if strict and not PHONE_RE.match(digits):
        raise ValidationError(_("Enter a valid Georgian mobile number."), code="invalid_phone")
    return digits


def validate_personal_id(value):
    if not re.fullmatch(r"\d{11}", str(value or "")):
        raise ValidationError(_("The personal ID must have 11 digits."), code="invalid_personal_id")


def validate_company_id(value):
    if not re.fullmatch(r"\d{9}", str(value or "")):
        raise ValidationError(_("The company ID must have 9 digits."), code="invalid_company_id")


def normalize_vin(value):
    """Optional VIN: "" stays "", anything else must be 17 valid characters (no I, O, Q)."""
    vin = str(value or "").strip().upper()
    if vin and not VIN_RE.match(vin):
        raise ValidationError(_("Enter a valid 17-character VIN."), code="invalid_vin")
    return vin


def normalize_part_number(value):
    """Matching key: upper-case without spaces, dashes and dots. Keep the original for display."""
    return re.sub(r"[\s.\-]", "", str(value or "")).upper()


class ComplexityValidator:
    """Django password validator: an upper- and lower-case letter, a digit and a symbol."""

    RULES = (
        ("password_no_upper", str.isupper, _("The password must contain an uppercase letter.")),
        ("password_no_lower", str.islower, _("The password must contain a lowercase letter.")),
        ("password_no_digit", str.isdigit, _("The password must contain a digit.")),
        ("password_no_symbol", lambda c: not c.isalnum() and not c.isspace(),
         _("The password must contain a symbol.")),
    )

    def validate(self, password, user=None):
        errors = [ValidationError(message, code=code)
                  for code, test, message in self.RULES if not any(test(c) for c in password)]
        if errors:
            raise ValidationError(errors)

    def get_help_text(self):
        return _("Use upper- and lower-case letters, a digit and a symbol.")
