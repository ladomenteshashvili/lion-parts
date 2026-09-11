from django.conf import settings
from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from .models import CarrierService, PartQuoteRequest, PartSearchLog

@admin.register(CarrierService)
class CarrierServiceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "usd_per_kg",
        "min_eta_days",
        "max_eta_days",
        "is_active",
        "is_default",
        "updated_at",
    )
    list_filter = ("is_active", "is_default")
    search_fields = ("name",)
    list_editable = (
        "usd_per_kg",
        "min_eta_days",
        "max_eta_days",
        "is_active",
        "is_default",
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(PartSearchLog)
class PartSearchLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "provider",
        "part_number",
        "vin",
        "customer_phone",
        "customer_name",
        "session_id",
        "found_count",
        "status",
        "created_at",
    )
    list_filter = ("provider", "status", "created_at")
    search_fields = ("part_number", "vin", "session_id", "customer_phone", "customer_name")
    readonly_fields = (
        "session_id",
        "provider",
        "part_number",
        "vin",
        "customer_phone",
        "customer_name",
        "found_count",
        "status",
        "raw_response",
        "normalized_response",
        "error_message",
        "created_at",
    )


@admin.register(PartQuoteRequest)
class PartQuoteRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "request_type",
        "part_number",
        "customer_phone",
        "customer_name",
        "status",
        "prepared_weight_kg",
        "final_price_gel",
        "price_ready_at",
        "customer_link",
        "created_at",
    )
    list_filter = ("request_type", "status", "price_ready_at", "created_at")
    search_fields = (
        "part_number",
        "vin",
        "customer_phone",
        "customer_name",
        "notification_token",
    )
    readonly_fields = (
        "notification_token",
        "customer_link",
        "price_ready_at",
        "notification_acknowledged_at",
        "created_at",
        "updated_at",
    )
    actions = ("mark_price_ready",)

    fieldsets = (
        ("მოთხოვნა", {
            "fields": (
                "part_number",
                "request_type",
                "vin",
                "customer_name",
                "customer_phone",
                "comment",
                "status",
            ),
        }),
        ("მოძებნილი შეთავაზება", {
            "fields": (
                "quote_id",
                "part_option_id",
                "name",
                "condition",
                "brand",
                "availability",
            ),
        }),
        ("ოპერატორის მიერ მომზადებული ფასი", {
            "fields": (
                "prepared_weight_kg",
                "final_price_gel",
                "currency",
                "eta_days",
                "operator_message",
            ),
        }),
        ("Customer notification", {
            "fields": (
                "notification_token",
                "customer_link",
                "price_ready_at",
                "notification_acknowledged_at",
                "created_at",
                "updated_at",
            ),
        }),
    )

    @admin.display(description="Customer magic link")
    def customer_link(self, obj):
        if not obj or not obj.is_price_ready:
            return "—"

        base_url = getattr(settings, "FRONTEND_BASE_URL", "").rstrip("/")
        path = f"/q/{obj.notification_token}"
        url = f"{base_url}{path}" if base_url else path
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)

    @admin.action(description="მომხმარებელს: ფასი მზადაა — magic link-ის შექმნა")
    def mark_price_ready(self, request, queryset):
        prepared_count = 0
        skipped_count = 0

        for quote_request in queryset:
            if (
                quote_request.final_price_gel is None
                or quote_request.final_price_gel <= 0
                or quote_request.prepared_weight_kg is None
                or quote_request.prepared_weight_kg <= 0
            ):
                skipped_count += 1
                continue

            quote_request.status = PartQuoteRequest.STATUS_RESOLVED
            quote_request.price_ready_at = timezone.now()
            quote_request.notification_acknowledged_at = None
            quote_request.name = quote_request.name or quote_request.part_number
            quote_request.availability = quote_request.availability or "ფასი მზადაა"
            quote_request.operator_message = (
                quote_request.operator_message
                or "ნაწილის წონა გადამოწმებულია და საბოლოო ფასი მზადაა."
            )
            quote_request.save()
            prepared_count += 1

        if prepared_count:
            self.message_user(
                request,
                f"{prepared_count} მოთხოვნაზე ფასი მომზადდა და magic link შეიქმნა.",
                level=messages.SUCCESS,
            )

        if skipped_count:
            self.message_user(
                request,
                (
                    f"{skipped_count} მოთხოვნა გამოტოვებულია — ჯერ შეავსეთ "
                    "წონა და საბოლოო ფასი."
                ),
                level=messages.WARNING,
            )
