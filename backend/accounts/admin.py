from django.contrib import admin

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "phone",
        "session_id",
        "is_phone_verified",
        "can_request_quote",
        "updated_at",
    )
    list_filter = ("is_phone_verified", "can_request_quote")
    search_fields = ("name", "phone", "session_id")
    list_editable = ("is_phone_verified", "can_request_quote")
    readonly_fields = ("created_at", "updated_at")