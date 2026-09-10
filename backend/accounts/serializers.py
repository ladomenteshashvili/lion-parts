from rest_framework import serializers

from .models import Customer, LegalEntityProfile



class LegalEntityProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = LegalEntityProfile
        fields = [
            "id",
            "company_identification_code",
            "company_official_name",
            "legal_address",
            "contact_first_name",
            "contact_last_name",
            "email",
            "mobile_phone",
            "is_mobile_verified",
            "mobile_verified_at",
            "is_active",
            "created_at",
            "updated_at",
        ]


class CustomerSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="name")
    customer_phone = serializers.CharField(source="phone")

    customer_tariff_id = serializers.SerializerMethodField()
    customer_tariff_name = serializers.SerializerMethodField()
    markup_percent = serializers.SerializerMethodField()
    can_request_quote = serializers.SerializerMethodField()
    can_enter_weight = serializers.SerializerMethodField()
    has_password = serializers.SerializerMethodField()
    legal_entity = serializers.SerializerMethodField()

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
            "has_password",
            "can_request_quote",
            "can_enter_weight",
            "legal_entity",
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

    def get_legal_entity(self, obj):
        try:
            legal_entity = obj.legal_entity_profile
        except LegalEntityProfile.DoesNotExist:
            return None

        return LegalEntityProfileSerializer(legal_entity).data

    def get_has_password(self, obj):
        return obj.has_password
