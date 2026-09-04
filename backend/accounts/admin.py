from django.contrib import admin

from .models import Customer, CustomerTariff, PhoneVerificationCode


@admin.register(CustomerTariff)
class CustomerTariffAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "markup_percent",
        "can_request_quote",
        "can_enter_weight",
        "is_default",
        "updated_at",
    )
    list_filter = ("can_request_quote", "can_enter_weight", "is_default")
    search_fields = ("name",)
    list_editable = (
        "markup_percent",
        "can_request_quote",
        "can_enter_weight",
        "is_default",
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "phone",
        "session_id",
        "tariff",
        "is_phone_verified",
        "can_request_quote",
        "updated_at",
    )
    list_filter = ("tariff", "is_phone_verified", "can_request_quote")
    search_fields = ("name", "phone", "session_id")
    list_editable = ("tariff", "is_phone_verified", "can_request_quote")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PhoneVerificationCode)
class PhoneVerificationCodeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "phone",
        "session_id",
        "purpose",
        "status",
        "attempts",
        "max_attempts",
        "expires_at",
        "verified_at",
        "created_at",
    )
    list_filter = ("purpose", "status", "created_at", "verified_at")
    search_fields = ("phone", "session_id", "sent_message_id")
    readonly_fields = (
        "session_id",
        "phone",
        "purpose",
        "code_hash",
        "status",
        "attempts",
        "max_attempts",
        "sent_message_id",
        "provider_response",
        "expires_at",
        "verified_at",
        "created_at",
    )