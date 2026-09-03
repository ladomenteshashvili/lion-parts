from django.contrib import admin

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
        "session_id",
        "found_count",
        "status",
        "created_at",
    )
    list_filter = ("provider", "status", "created_at")
    search_fields = ("part_number", "vin", "session_id")
    readonly_fields = (
        "session_id",
        "provider",
        "part_number",
        "vin",
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
        "part_number",
        "customer_phone",
        "customer_name",
        "status",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = ("part_number", "vin", "customer_phone", "customer_name")
    readonly_fields = ("created_at", "updated_at")