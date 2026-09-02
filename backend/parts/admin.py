from django.contrib import admin

from .models import PartQuoteRequest


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