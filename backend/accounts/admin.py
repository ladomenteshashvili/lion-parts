from django.contrib import admin

from .models import Customer, CustomerTariff, LegalEntityProfile, PhoneVerificationCode, TariffMarkup


class TariffMarkupInline(admin.TabularInline):
    model = TariffMarkup
    extra = 0


@admin.register(CustomerTariff)
class CustomerTariffAdmin(admin.ModelAdmin):
    list_display = ("name", "is_default", "payment_terms", "split_upfront_percent",
                    "can_enter_weight", "can_request_quote", "updated_at")
    list_filter = ("is_default", "payment_terms", "can_enter_weight", "can_request_quote")
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [TariffMarkupInline]

    # Customer.get_tariff() needs a default tariff: it can't be deleted, not even in bulk.
    def has_delete_permission(self, request, obj=None):
        return not (obj and obj.is_default) and super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


class LegalEntityProfileInline(admin.StackedInline):
    model = LegalEntityProfile
    extra = 0
    readonly_fields = ("created_at", "updated_at")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "email", "phone", "account_type", "tariff",
                    "is_phone_verified", "is_email_verified", "preferred_language", "created_at")
    list_filter = ("account_type", "tariff", "is_phone_verified", "is_email_verified", "preferred_language")
    search_fields = ("first_name", "last_name", "user__email", "phone", "personal_id",
                     "company__company_official_name", "company__company_identification_code")
    list_select_related = ("user", "tariff")
    raw_id_fields = ("user",)
    readonly_fields = ("phone_verified_at", "email_verified_at", "terms_accepted_at",
                       "anonymized_at", "created_at", "updated_at")
    inlines = [LegalEntityProfileInline]

    @admin.display(description="Email", ordering="user__email")
    def email(self, obj):
        return obj.user.email


@admin.register(PhoneVerificationCode)
class PhoneVerificationCodeAdmin(admin.ModelAdmin):
    """Read-only audit view; the code hash is never shown."""

    list_display = ("id", "phone", "purpose", "status", "attempts", "max_attempts", "expires_at", "created_at")
    list_filter = ("purpose", "status")
    search_fields = ("phone", "provider_message_id")
    fields = ("session_key", "customer", "phone", "purpose", "status", "attempts", "max_attempts",
              "provider_message_id", "expires_at", "verified_at", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request):
        return False
