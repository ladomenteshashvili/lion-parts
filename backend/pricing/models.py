"""Pricing configuration (G§6.2): site settings, currencies, NBG rates, discounts, pickup points."""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from accounts.validators import normalize_part_number

CURRENCIES = [("GEL", "GEL"), ("USD", "USD")]


class SiteSettings(models.Model):
    """Singleton (pk=1). Cities: [{"code", "name": {lang: text}}]; districts add "city"."""

    price_valid_minutes = models.PositiveIntegerField(default=60)
    default_shipping_usd_per_kg = models.DecimalField(max_digits=8, decimal_places=2, default=8)
    rate_max_age_days = models.PositiveSmallIntegerField(default=3)
    delivery_cities = models.JSONField(default=list, blank=True)
    delivery_districts = models.JSONField(default=list, blank=True)
    invoice_seller_name_i18n = models.JSONField(default=dict)
    invoice_seller_code = models.CharField(max_length=40, blank=True)
    invoice_seller_address_i18n = models.JSONField(default=dict, blank=True)
    invoice_seller_phone = models.CharField(max_length=40, blank=True)
    invoice_seller_email = models.CharField(max_length=255, blank=True)
    invoice_bank_details_i18n = models.JSONField(default=dict, blank=True)
    invoice_payment_due_text_i18n = models.JSONField(default=dict, blank=True)
    invoice_footer_text_i18n = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = verbose_name_plural = "Site settings"

    def __str__(self):
        return "Site settings"

    def clean(self):
        """Cities: [{"code", "name": {lang: text}}]; districts also need "city" (an existing city code).
        Codes are unique and the default-language name is required (G§5.8)."""
        from django.conf import settings

        def check(rows, field, needs_city):
            if not isinstance(rows, list):
                return _("Enter a list of objects.")
            seen = set()
            for row in rows:
                if not isinstance(row, dict) or not str(row.get("code") or "").strip():
                    return _("Every row needs a code.")
                if row["code"] in seen:
                    return _("Duplicate code: %(code)s") % {"code": row["code"]}
                seen.add(row["code"])
                names = row.get("name")
                if not isinstance(names, dict) or not str(names.get(settings.LANGUAGE_CODE) or "").strip():
                    return _("%(code)s: the default-language name is required.") % {"code": row["code"]}
                if needs_city and row.get("city") not in city_codes:
                    return _("%(code)s: unknown city.") % {"code": row["code"]}
            return None

        city_codes = {c.get("code") for c in self.delivery_cities if isinstance(c, dict)} \
            if isinstance(self.delivery_cities, list) else set()
        errors = {field: message for field, message in (
            ("delivery_cities", check(self.delivery_cities, "delivery_cities", False)),
            ("delivery_districts", check(self.delivery_districts, "delivery_districts", True)),
        ) if message}
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self._state.adding and SiteSettings.objects.exists():
            raise ValidationError(_("Site settings already exist; edit the existing row."))
        self.pk = 1  # singleton
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        return cls.objects.get_or_create(pk=1)[0]


class CurrencySettings(models.Model):
    currency = models.CharField(max_length=3, unique=True, choices=CURRENCIES)
    vat_percent = models.DecimalField(max_digits=5, decimal_places=2, default=18)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["currency"]
        verbose_name_plural = "Currency settings"

    def __str__(self):
        return f"{self.currency} (VAT {self.vat_percent}%)"

    def clean(self):
        from accounts.models import TariffMarkup

        if self.is_active and not TariffMarkup.objects.filter(tariff__is_default=True, currency=self.currency).exists():
            raise ValidationError({"is_active": _(
                "Add a %(currency)s markup to the default tariff before activating %(currency)s.")
                % {"currency": self.currency}})

    @classmethod
    def active_codes(cls):
        return list(cls.objects.filter(is_active=True).values_list("currency", flat=True))


class ExchangeRate(models.Model):
    date = models.DateField()
    currency = models.CharField(max_length=3, default="USD")
    rate = models.DecimalField(max_digits=10, decimal_places=4)  # GEL per 1 unit
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("date", "currency")]
        ordering = ["-date"]

    def __str__(self):
        return f"{self.date} {self.currency} {self.rate}"


class Discount(models.Model):
    KIND_PERCENT = "percent"
    KIND_FIXED = "fixed"

    name = models.CharField(max_length=120)
    customer = models.ForeignKey("accounts.Customer", on_delete=models.CASCADE, null=True, blank=True)
    tariff = models.ForeignKey("accounts.CustomerTariff", on_delete=models.CASCADE, null=True, blank=True)
    part_number = models.CharField(max_length=120, blank=True)  # normalized (G§7.1)
    brand = models.CharField(max_length=120, blank=True)
    kind = models.CharField(max_length=10, choices=[(KIND_PERCENT, "Percent"), (KIND_FIXED, "Fixed GEL per unit")])
    value = models.DecimalField(max_digits=10, decimal_places=2)  # percent 0-100 or fixed GEL per unit
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_to = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "name"]

    def __str__(self):
        return self.name

    def clean(self):
        errors = {}
        if self.value is not None and self.value < 0:
            errors["value"] = _("The value can't be negative.")
        elif self.kind == self.KIND_PERCENT and self.value is not None and self.value > 100:
            errors["value"] = _("A percentage can't exceed 100.")
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            errors["valid_to"] = _("The end must be after the start.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.part_number = normalize_part_number(self.part_number)
        super().save(*args, **kwargs)


class PickupPoint(models.Model):
    name_i18n = models.JSONField(default=dict)
    address_i18n = models.JSONField(default=dict, blank=True)
    working_hours_i18n = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        from api.i18n import tr
        return tr(self.name_i18n) or f"Pickup point {self.pk}"
