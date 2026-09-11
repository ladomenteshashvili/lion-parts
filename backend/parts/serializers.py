from rest_framework import serializers

from .models import PartQuoteRequest


class PartQuoteRequestSerializer(serializers.ModelSerializer):
    is_price_ready = serializers.BooleanField(read_only=True)

    class Meta:
        model = PartQuoteRequest
        fields = [
            "id",
            "session_id",
            "request_type",
            "part_number",
            "vin",
            "customer_name",
            "customer_phone",
            "comment",
            "quote_id",
            "part_option_id",
            "name",
            "condition",
            "brand",
            "availability",
            "eta_days",
            "prepared_weight_kg",
            "final_price_gel",
            "currency",
            "operator_message",
            "status",
            "is_price_ready",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "prepared_weight_kg",
            "final_price_gel",
            "operator_message",
            "status",
            "is_price_ready",
            "created_at",
            "updated_at",
        ]


class PublicPreparedQuoteSerializer(serializers.ModelSerializer):
    token = serializers.UUIDField(source="notification_token", read_only=True)
    weight_kg = serializers.DecimalField(
        source="prepared_weight_kg",
        max_digits=8,
        decimal_places=2,
        read_only=True,
        allow_null=True,
    )
    is_acknowledged = serializers.SerializerMethodField()

    class Meta:
        model = PartQuoteRequest
        fields = [
            "id",
            "token",
            "part_number",
            "vin",
            "quote_id",
            "part_option_id",
            "name",
            "condition",
            "brand",
            "availability",
            "eta_days",
            "weight_kg",
            "final_price_gel",
            "currency",
            "operator_message",
            "price_ready_at",
            "is_acknowledged",
        ]

    def get_is_acknowledged(self, obj):
        return obj.notification_acknowledged_at is not None
