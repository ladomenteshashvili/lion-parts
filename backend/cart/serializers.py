"""Cart input validation (G§8.5). There are deliberately no price fields: unknown keys are ignored."""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from parts.serializers import CurrencyField, VinField


class QuantityField(serializers.IntegerField):
    """1..99; anything else is `invalid_quantity` (G§7.1)."""

    def to_internal_value(self, data):
        try:
            value = super().to_internal_value(data)
        except serializers.ValidationError:
            value = None
        if value is None or not 1 <= value <= 99:
            raise serializers.ValidationError(_("The quantity must be between 1 and 99."), code="invalid_quantity")
        return value


class AddItemSerializer(serializers.Serializer):
    source = serializers.ChoiceField(choices=["search", "prepared_quote"])
    search_id = serializers.IntegerField(required=False)
    offer_id = serializers.CharField(required=False, max_length=120)
    quote_request_id = serializers.IntegerField(required=False)
    quantity = QuantityField(required=False, default=1)

    def validate(self, data):
        required = ("search_id", "offer_id") if data["source"] == "search" else ("quote_request_id",)
        missing = [f for f in required if data.get(f) in (None, "")]
        if missing:
            message = str(serializers.Field.default_error_messages["required"])
            raise serializers.ValidationError({f: [serializers.ErrorDetail(message, "required")] for f in missing})
        return data


class UpdateItemSerializer(serializers.Serializer):
    quantity = QuantityField(required=False)
    vin = VinField(default=serializers.empty)  # absent = unchanged, "" clears


class VinSerializer(serializers.Serializer):
    vin = VinField()


class CartUpdateSerializer(serializers.Serializer):
    currency = CurrencyField()
