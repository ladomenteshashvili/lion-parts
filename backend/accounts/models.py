from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone


class CustomerTariff(models.Model):
    name = models.CharField(max_length=120, unique=True)
    markup_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("20.00"),
    )

    can_request_quote = models.BooleanField(default=False)
    can_enter_weight = models.BooleanField(default=False)

    is_default = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "name"]

    def __str__(self):
        return f"{self.name} · {self.markup_percent}%"

    @classmethod
    def get_default(cls):
        return cls.objects.filter(is_default=True).first()


class Customer(models.Model):
    session_id = models.CharField(max_length=120, db_index=True)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=40, db_index=True)

    tariff = models.ForeignKey(
        CustomerTariff,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customers",
    )

    is_phone_verified = models.BooleanField(default=False)

    # Direct customer override. Later most users should use tariff permissions.
    can_request_quote = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.name} · {self.phone}"

    def get_tariff(self):
        return self.tariff or CustomerTariff.get_default()

    def get_markup_percent(self):
        tariff = self.get_tariff()

        if tariff:
            return tariff.markup_percent

        return Decimal(str(settings.DEFAULT_CUSTOMER_MARKUP_PERCENT))

    def has_quote_request_permission(self):
        tariff = self.get_tariff()

        return self.can_request_quote or bool(
            tariff and tariff.can_request_quote
        )

    def has_weight_entry_permission(self):
        tariff = self.get_tariff()

        return bool(tariff and tariff.can_enter_weight)


class PhoneVerificationCode(models.Model):
    PURPOSE_LOGIN = "login"

    PURPOSE_CHOICES = [
        (PURPOSE_LOGIN, "Login"),
    ]

    STATUS_PENDING = "pending"
    STATUS_VERIFIED = "verified"
    STATUS_EXPIRED = "expired"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_VERIFIED, "Verified"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_FAILED, "Failed"),
    ]

    session_id = models.CharField(max_length=120, db_index=True)
    phone = models.CharField(max_length=40, db_index=True)
    purpose = models.CharField(
        max_length=40,
        choices=PURPOSE_CHOICES,
        default=PURPOSE_LOGIN,
    )

    code_hash = models.CharField(max_length=256)

    status = models.CharField(
        max_length=40,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)

    sent_message_id = models.CharField(max_length=120, blank=True)
    provider_response = models.JSONField(default=dict, blank=True)

    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.phone} · {self.status} · {self.created_at}"

    def set_code(self, code):
        self.code_hash = make_password(code)

    def check_code(self, code):
        return check_password(code, self.code_hash)

    def is_expired(self):
        return timezone.now() >= self.expires_at

    def mark_verified(self):
        self.status = self.STATUS_VERIFIED
        self.verified_at = timezone.now()
        self.save(update_fields=["status", "verified_at"])