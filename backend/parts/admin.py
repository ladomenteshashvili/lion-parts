from django.contrib import admin

from .models import CarrierService, PartQuoteRequest


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