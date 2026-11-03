"""Customer accounts (G§6.1). A customer is a Django `User` (username = email) plus a `Customer` row."""

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

CURRENCY_CHOICES = [("GEL", "GEL"), ("USD", "USD")]


class CustomerTariff(models.Model):
    PAYMENT_FULL = "full"
    PAYMENT_SPLIT = "split_70_30"

    name = models.CharField(max_length=120, unique=True)
    is_default = models.BooleanField(default=False)  # exactly one (save/clean)
    payment_terms = models.CharField(
        max_length=20, choices=[(PAYMENT_FULL, "100%"), (PAYMENT_SPLIT, "70/30")], default=PAYMENT_FULL,
    )
    split_upfront_percent = models.DecimalField(max_digits=5, decimal_places=2, default=70)
    can_enter_weight = models.BooleanField(default=False)
    can_request_quote = models.BooleanField(default=False)
    benefits_text_i18n = models.JSONField(default=dict, blank=True)  # translated (G§5.8)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "name"]

    def __str__(self):
        return self.name

    def clean(self):
        if not self.is_default and self.pk and CustomerTariff.objects.filter(pk=self.pk, is_default=True).exists():
            raise ValidationError({"is_default": _("Make another tariff the default first.")})

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.is_default:
                CustomerTariff.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
            super().save(*args, **kwargs)


class TariffMarkup(models.Model):
    tariff = models.ForeignKey(CustomerTariff, on_delete=models.CASCADE, related_name="markups")
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES)
    markup_percent = models.DecimalField(max_digits=6, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("tariff", "currency")]

    def __str__(self):
        return f"{self.tariff} · {self.currency} {self.markup_percent}%"


class Customer(models.Model):
    TYPE_PHYSICAL = "physical"
    TYPE_LEGAL = "legal"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="customer")
    account_type = models.CharField(
        max_length=10, choices=[(TYPE_PHYSICAL, "Physical"), (TYPE_LEGAL, "Legal")],
    )  # immutable after registration
    first_name = models.CharField(max_length=120)  # legal: the representative
    last_name = models.CharField(max_length=120)
    personal_id = models.CharField(max_length=11, unique=True, null=True, blank=True)  # physical only
    phone = models.CharField(max_length=9, unique=True, null=True, blank=True)  # null after anonymize
    is_phone_verified = models.BooleanField(default=False)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    is_email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    tariff = models.ForeignKey(
        CustomerTariff, on_delete=models.SET_NULL, null=True, blank=True, related_name="customers",
    )  # null -> default tariff
    terms_accepted_at = models.DateTimeField()
    marketing_opt_in = models.BooleanField(default=False)
    preferred_language = models.CharField(max_length=8, choices=settings.LANGUAGES, default=settings.LANGUAGE_CODE)
    anonymized_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} · {self.phone or '-'}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_fully_verified(self):
        return self.is_phone_verified and self.is_email_verified

    def get_tariff(self):
        return self.tariff or CustomerTariff.objects.get(is_default=True)


class LegalEntityProfile(models.Model):
    customer = models.OneToOneField(Customer, on_delete=models.CASCADE, related_name="company")
    company_official_name = models.CharField(max_length=255)
    company_identification_code = models.CharField(max_length=9, unique=True)
    legal_address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.company_official_name} · {self.company_identification_code}"


class PhoneVerificationCode(models.Model):
    PURPOSE_REGISTRATION = "registration"
    PURPOSE_PHONE_CHANGE = "phone_change"

    STATUS_PENDING = "pending"
    STATUS_VERIFIED = "verified"
    STATUS_EXPIRED = "expired"
    STATUS_FAILED = "failed"

    session_key = models.CharField(max_length=64, db_index=True)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, null=True, blank=True)  # phone_change
    phone = models.CharField(max_length=9, db_index=True)
    purpose = models.CharField(
        max_length=20,
        choices=[(PURPOSE_REGISTRATION, "Registration"), (PURPOSE_PHONE_CHANGE, "Phone change")],
    )
    code_hash = models.CharField(max_length=256)
    status = models.CharField(
        max_length=10,
        choices=[(STATUS_PENDING, "Pending"), (STATUS_VERIFIED, "Verified"),
                 (STATUS_EXPIRED, "Expired"), (STATUS_FAILED, "Failed")],
        default=STATUS_PENDING,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    provider_message_id = models.CharField(max_length=120, blank=True)
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.phone} · {self.purpose} · {self.status}"

    def set_code(self, code):
        self.code_hash = make_password(code)

    def check_code(self, code):
        return check_password(code, self.code_hash)
