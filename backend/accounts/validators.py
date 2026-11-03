"""Normalizers and validators (G§7.1). Codes are the field error codes of G§9."""

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

PHONE_RE = re.compile(r"^5\d{8}$")


def normalize_phone(value):
    """Any Georgian mobile format -> 9 digits ("+995 555 12 34 56" -> "555123456")."""
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("995"):
        digits = digits[3:]
    if not PHONE_RE.match(digits):
        raise ValidationError(_("Enter a valid Georgian mobile number."), code="invalid_phone")
    return digits
