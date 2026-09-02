from rest_framework import serializers

from .models import PartQuoteRequest


class PartQuoteRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartQuoteRequest
        fields = [
            "id",
            "session_id",
            "part_number",
            "vin",
            "customer_name",
            "customer_phone",
            "comment",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "created_at",
            "updated_at",
        ]