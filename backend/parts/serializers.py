"""Input validation for the parts endpoints (G§8.4)."""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from accounts.validators import normalize_vin
from pricing.models import CurrencySettings


class CurrencyField(serializers.CharField):
    """An active currency code (CurrencySettings.is_active); unknown or inactive -> `invalid`."""

    def to_internal_value(self, data):
        value = super().to_internal_value(data).upper()
        if value not in CurrencySettings.active_codes():
            raise serializers.ValidationError(_("Choose one of the available currencies."), code="invalid")
        return value


class VinField(serializers.CharField):
    def __init__(self, **kwargs):
        super().__init__(required=False, allow_blank=True, default="", max_length=40, **kwargs)

    def to_internal_value(self, data):
        return normalize_vin(super().to_internal_value(data))  # Django ValidationError(code=invalid_vin)


class SearchSerializer(serializers.Serializer):
    part_number = serializers.CharField(max_length=120)
    vin = VinField()
    currency = CurrencyField(required=False, default="GEL")
