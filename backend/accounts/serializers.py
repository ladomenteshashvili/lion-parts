from rest_framework import serializers

from .models import Customer


class CustomerSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="name")
    customer_phone = serializers.CharField(source="phone")

    customer_tariff_id = serializers.SerializerMethodField()
    customer_tariff_name = serializers.SerializerMethodField()
    markup_percent = serializers.SerializerMethodField()
    can_request_quote = serializers.SerializerMethodField()
    can_enter_weight = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = [
            "id",
            "session_id",
            "customer_name",
            "customer_phone",
            "customer_tariff_id",
            "customer_tariff_name",
            "markup_percent",
            "is_phone_verified",
            "can_request_quote",
            "can_enter_weight",
            "created_at",
            "updated_at",
        ]

    def get_customer_tariff_id(self, obj):
        tariff = obj.get_tariff()
        return tariff.id if tariff else None

    def get_customer_tariff_name(self, obj):
        tariff = obj.get_tariff()
        return tariff.name if tariff else None

    def get_markup_percent(self, obj):
        return str(obj.get_markup_percent())

    def get_can_request_quote(self, obj):
        return obj.has_quote_request_permission()

    def get_can_enter_weight(self, obj):
        return obj.has_weight_entry_permission()