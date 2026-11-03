from django.conf import settings
from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from pricing.rates import RateUnavailable

from .models import CarrierService, PartImage, PartQuoteRequest, PartSearchLog
from .services import mark_quote_price_ready


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
        ready, missing, failed = 0, [], []
        for quote_request in queryset.select_related("customer"):
            weight, price = quote_request.prepared_weight_kg, quote_request.supplier_price_usd
            if not weight or weight <= 0 or price is None or price <= 0:
                missing.append(quote_request.id)
                continue
            try:
                mark_quote_price_ready(quote_request)
            except RateUnavailable:
                failed.append(quote_request.id)
                continue
            ready += 1
        if ready:
            self.message_user(request, ngettext("%(n)d request priced; its customer link is active.",
                                                "%(n)d requests priced; their customer links are active.",
                                                ready) % {"n": ready}, messages.SUCCESS)
        if missing:
            self.message_user(request, _("Skipped (enter a positive weight and supplier price): %(ids)s")
                              % {"ids": ", ".join(map(str, missing))}, messages.WARNING)
        if failed:
            self.message_user(request, _("Not priced, the exchange rate is unavailable: %(ids)s")
                              % {"ids": ", ".join(map(str, failed))}, messages.ERROR)


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
