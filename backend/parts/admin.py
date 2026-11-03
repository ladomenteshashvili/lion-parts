from django.conf import settings
from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from api.errors import ApiError
from orders.services import OperatorError

from .models import CarrierService, PartImage, PartQuoteRequest, PartSearchLog
from .services import mark_quote_ready


@admin.register(CarrierService)
class CarrierServiceAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "usd_per_kg", "min_eta_days", "max_eta_days", "is_active", "is_default", "updated_at")
    list_filter = ("is_active", "is_default")
    search_fields = ("name",)
    list_editable = ("usd_per_kg", "min_eta_days", "max_eta_days", "is_active", "is_default")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PartSearchLog)
class PartSearchLogAdmin(admin.ModelAdmin):
    """Read-only audit of searches; the offers in normalized_response are the price source."""

    list_display = ("id", "kind", "provider", "part_number", "vin", "customer", "currency", "found_count", "status",
                    "price_valid_until", "hidden_at", "created_at")
    list_filter = ("kind", "provider", "status", "currency", "created_at")
    search_fields = ("part_number", "vin", "session_key", "customer__phone", "customer__user__email")
    list_select_related = ("customer",)
    raw_id_fields = ("customer",)

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(PartQuoteRequest)
class PartQuoteRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "request_type", "part_number", "customer", "status", "prepared_weight_kg",
                    "supplier_price_usd", "unit_price_usd", "currency", "price_ready_at", "price_valid_until",
                    "customer_link", "created_at")
    list_filter = ("request_type", "status", "currency", "price_ready_at")
    search_fields = ("part_number", "vin", "customer__phone", "customer__user__email", "notification_token")
    list_select_related = ("customer",)
    raw_id_fields = ("customer", "search_log")
    actions = ("mark_price_ready",)
    readonly_fields = ("customer", "search_log", "request_type", "part_number", "vin", "comment", "offer_id",
                       "quote_id", "shipping_usd_per_kg", "markup_percent", "vat_percent", "list_price_usd",
                       "discount_usd", "unit_price_usd", "discount", "price_valid_until", "notification_token",
                       "customer_link", "price_ready_at", "seen_at", "created_at", "updated_at")
    fieldsets = (
        (_("Request"), {"fields": ("customer", "request_type", "part_number", "vin", "comment", "status")}),
        (_("Offer"), {"fields": ("search_log", "offer_id", "quote_id", "name", "brand", "condition",
                                  "eta_min_days", "eta_max_days")}),
        (_("Operator price"), {
            "description": _("Enter the weight (and the supplier price for a manual request), then run the "
                             "“price ready” action: the price is calculated for the customer automatically."),
            "fields": ("prepared_weight_kg", "supplier_price_usd", "currency", "operator_message"),
        }),
        (_("Price snapshot"), {
            "description": _("The approved price in USD (the base). Customers paying in GEL see it converted "
                             "with the current NBG rate. Run “price ready” again to re-approve an expired quote."),
            "fields": ("shipping_usd_per_kg", "markup_percent", "vat_percent", "list_price_usd", "discount_usd",
                       "unit_price_usd", "discount", "price_valid_until")}),
        (_("Customer link"), {"fields": ("notification_token", "customer_link", "price_ready_at", "seen_at",
                                         "created_at", "updated_at")}),
    )

    @admin.display(description=_("Customer link"))
    def customer_link(self, obj):
        if not obj or not obj.is_price_ready:
            return "—"
        url = f"{settings.FRONTEND_BASE_URL.rstrip('/')}/q/{obj.notification_token}"
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, url)

    @admin.action(description=_("Price ready: calculate the price and create the customer link"))
    def mark_price_ready(self, request, queryset):
        """Per row through parts.services.mark_quote_ready (the fields entered on the request: weight,
        operator message; the supplier price for requests without a search offer)."""
        for qr in queryset:
            try:
                mark_quote_ready(qr.pk, qr.prepared_weight_kg, qr.operator_message,
                                 supplier_price_usd=None if qr.offer_id else qr.supplier_price_usd)
            except OperatorError as error:
                self.message_user(request, f"#{qr.pk}: {error.message}", messages.WARNING)
            except ApiError as error:  # rate unavailable
                self.message_user(request, f"#{qr.pk}: {error.detail}", messages.ERROR)
            else:
                self.message_user(request, _("#%(id)s: priced; the customer link is active.") % {"id": qr.pk},
                                  messages.SUCCESS)


@admin.register(PartImage)
class PartImageAdmin(admin.ModelAdmin):
    list_display = ("thumbnail", "part_number", "brand", "sort_order", "updated_at")
    list_editable = ("sort_order",)
    search_fields = ("part_number", "brand")
    readonly_fields = ("thumbnail", "created_at", "updated_at")

    @admin.display(description=_("Preview"))
    def thumbnail(self, obj):
        if not obj or not obj.image:
            return "—"
        return format_html('<img src="{}" alt="" style="max-height:80px;max-width:120px">', obj.image.url)
