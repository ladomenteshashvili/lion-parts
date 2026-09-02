from rest_framework import serializers
from .models import Customer


class CustomerSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="name")
    customer_phone = serializers.CharField(source="phone")

    class Meta:
        model = Customer
        fields = [
            "id",
            "session_id",
            "customer_name",
            "customer_phone",
            "is_phone_verified",
            "can_request_quote",
            "created_at",
            "updated_at",
        ]