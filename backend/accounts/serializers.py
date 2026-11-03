"""Input serializers (validation only) and the Customer output schema (G§8.0)."""

from django.conf import settings
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from api.i18n import tr

from .models import Customer, LegalEntityProfile
from .validators import normalize_phone, validate_company_id, validate_personal_id

TAKEN = {
    "email": _("This email is already linked to another account."),
    "phone": _("This phone number is already linked to another account."),
    "personal_id": _("An account with this personal ID already exists."),
    "company_identification_code": _("A company with this identification code is already registered."),
}


def language_codes():
    return {code for code, _name in settings.LANGUAGES}


def taken(field):
    return serializers.ValidationError(TAKEN[field], code="taken")


def email_is_taken(email):
    return get_user_model().objects.filter(email__iexact=email).exists() or \
        get_user_model().objects.filter(username__iexact=email).exists()


def phone_is_taken(phone, exclude_customer=None):
    return Customer.objects.filter(phone=phone).exclude(pk=getattr(exclude_customer, "pk", None)).exists()


def check_password_rules(password, email="", first_name="", last_name=""):
    """Django validators + ComplexityValidator against an unsaved user (for the similarity check)."""
    user = get_user_model()(username=email, email=email, first_name=first_name, last_name=last_name)
    try:
        password_validation.validate_password(password, user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(serializers.as_serializer_error(exc)["non_field_errors"]) from None


class PhoneField(serializers.CharField):
    def to_internal_value(self, data):
        return normalize_phone(super().to_internal_value(data))  # Django ValidationError(code=invalid_phone)


class RegisterSerializer(serializers.Serializer):
    account_type = serializers.ChoiceField(choices=[Customer.TYPE_PHYSICAL, Customer.TYPE_LEGAL])
    first_name = serializers.CharField(max_length=120)
    last_name = serializers.CharField(max_length=120)
    personal_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    company_official_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    company_identification_code = serializers.CharField(required=False, allow_blank=True)
    phone = PhoneField()
    email = serializers.EmailField(max_length=150)
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    terms_accepted = serializers.BooleanField()
    marketing_opt_in = serializers.BooleanField(default=False)
    preferred_language = serializers.CharField(required=False)

    TERMS_REQUIRED = _("You must accept the terms to register.")

    def validate_email(self, value):
        value = value.strip().lower()
        if email_is_taken(value):
            raise taken("email")
        return value

    def validate_phone(self, value):
        if phone_is_taken(value):
            raise taken("phone")
        return value

    def validate_terms_accepted(self, value):
        if value is not True:
            raise serializers.ValidationError(self.TERMS_REQUIRED, code="required")
        return value

    def validate_preferred_language(self, value):
        if value not in language_codes():
            raise serializers.ValidationError(_("Choose one of the supported languages."), code="invalid")
        return value

    def validate_password(self, value):
        data = self.initial_data if isinstance(self.initial_data, dict) else {}
        check_password_rules(value, str(data.get("email", "")).strip().lower(),
                             str(data.get("first_name", "")), str(data.get("last_name", "")))
        return value

    def to_internal_value(self, data):
        """Field errors and the account-type rules are reported together (G§8.2)."""
        try:
            values, errors = super().to_internal_value(data), {}
        except serializers.ValidationError as exc:
            values, errors = {}, dict(exc.detail)
        account_type = values.get("account_type") or (data.get("account_type") if isinstance(data, dict) else None)
        for field, error in self._account_type_errors(account_type, values, data).items():
            errors.setdefault(field, error)
        if errors:
            raise serializers.ValidationError(errors)
        values.setdefault("preferred_language", get_language())
        return values

    def _account_type_errors(self, account_type, values, data):
        raw = data if isinstance(data, dict) else {}

        def get(field):  # validated value, or the raw input when other fields failed validation
            return str(values.get(field, raw.get(field)) or "").strip()

        errors = {}
        required = serializers.ValidationError(serializers.Field.default_error_messages["required"], code="required")
        if account_type == Customer.TYPE_PHYSICAL:
            personal_id = get("personal_id")
            values.update(personal_id=personal_id, company_official_name="", company_identification_code="")
            try:
                if not personal_id:
                    raise required
                validate_personal_id(personal_id)
                if Customer.objects.filter(personal_id=personal_id).exists():
                    raise taken("personal_id")
            except (serializers.ValidationError, DjangoValidationError) as exc:
                errors["personal_id"] = serializers.as_serializer_error(exc)["non_field_errors"]
        elif account_type == Customer.TYPE_LEGAL:
            values["personal_id"] = None
            name, code = get("company_official_name"), get("company_identification_code")
            values.update(company_official_name=name, company_identification_code=code)
            if not name:
                errors["company_official_name"] = serializers.as_serializer_error(required)["non_field_errors"]
            try:
                if not code:
                    raise required
                validate_company_id(code)
                if LegalEntityProfile.objects.filter(company_identification_code=code).exists():
                    raise taken("company_identification_code")
            except (serializers.ValidationError, DjangoValidationError) as exc:
                errors["company_identification_code"] = serializers.as_serializer_error(exc)["non_field_errors"]
        return errors


class CustomerSerializer(serializers.ModelSerializer):
    """The `Customer` schema of G§8.0 (exact keys; nested objects or null)."""

    company = serializers.SerializerMethodField()
    email = serializers.EmailField(source="user.email")
    is_fully_verified = serializers.BooleanField()
    tariff = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = ["id", "account_type", "first_name", "last_name", "personal_id", "company", "phone",
                  "is_phone_verified", "email", "is_email_verified", "is_fully_verified", "tariff",
                  "marketing_opt_in", "preferred_language", "created_at"]
        read_only_fields = fields

    def get_company(self, customer):
        company = getattr(customer, "company", None)
        if company is None:
            return None
        return {"official_name": company.company_official_name,
                "identification_code": company.company_identification_code,
                "legal_address": company.legal_address}

    def get_tariff(self, customer):
        tariff = customer.get_tariff()
        return {"name": tariff.name, "payment_terms": tariff.payment_terms,
                "split_upfront_percent": f"{tariff.split_upfront_percent:.2f}",
                "can_enter_weight": tariff.can_enter_weight, "can_request_quote": tariff.can_request_quote,
                "benefits_text": tr(tariff.benefits_text_i18n)}


class LoginSerializer(serializers.Serializer):
    email = serializers.CharField(max_length=254)
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class CodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=6)


class TokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=1000)


class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField(max_length=64)
    token = serializers.CharField(max_length=128)
    new_password = serializers.CharField(max_length=128, trim_whitespace=False)


class ProfileUpdateSerializer(serializers.Serializer):
    """Only these two fields are writable through the API (G§8.3); other keys are ignored."""

    marketing_opt_in = serializers.BooleanField(required=False)
    preferred_language = serializers.CharField(required=False)

    def validate_preferred_language(self, value):
        if value not in language_codes():
            raise serializers.ValidationError(_("Choose one of the supported languages."), code="invalid")
        return value


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(max_length=128, trim_whitespace=False)
    new_password = serializers.CharField(max_length=128, trim_whitespace=False)

    def validate_current_password(self, value):
        if not self.context["user"].check_password(value):
            raise serializers.ValidationError(_("The current password is incorrect."), code="incorrect")
        return value

    def validate_new_password(self, value):
        user = self.context["user"]
        check_password_rules(value, user.email, user.first_name, user.last_name)
        return value
