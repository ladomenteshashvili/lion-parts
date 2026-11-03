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


class QuoteRequestSerializer(serializers.Serializer):
    """weight_price needs search_id + offer_id; manual_search needs part_number (G§8.4)."""

    request_type = serializers.ChoiceField(choices=["weight_price", "manual_search"])
    search_id = serializers.IntegerField(required=False)
    offer_id = serializers.CharField(required=False, max_length=120)
    part_number = serializers.CharField(required=False, max_length=120)
    vin = VinField()
    comment = serializers.CharField(required=False, allow_blank=True, max_length=2000, default="")

    def validate(self, data):
        required = (("search_id", "offer_id") if data["request_type"] == "weight_price" else ("part_number",))
        missing = {f: serializers.ErrorDetail(str(serializers.Field.default_error_messages["required"]), "required")
                   for f in required if not data.get(f)}
        if missing:
            raise serializers.ValidationError({f: [e] for f, e in missing.items()})
        return data
