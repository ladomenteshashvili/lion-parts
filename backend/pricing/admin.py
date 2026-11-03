from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from api.admin_i18n import TranslatedFieldsAdminMixin

from .models import CurrencySettings, Discount, ExchangeRate, PickupPoint, SiteSettings
from .rates import RateUnavailable, fetch_nbg_usd


@admin.register(SiteSettings)
class SiteSettingsAdmin(TranslatedFieldsAdminMixin, admin.ModelAdmin):
    """Singleton: one row, created by the seed migration; it can't be added twice or deleted."""

    textarea_i18n_fields = ("invoice_seller_address_i18n", "invoice_bank_details_i18n",
                            "invoice_payment_due_text_i18n", "invoice_footer_text_i18n")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (_("Prices"), {"fields": ("price_valid_minutes", "default_shipping_usd_per_kg", "rate_max_age_days")}),
        (_("Delivery areas"), {
            "description": _('Cities: [{"code": "tbilisi", "name": {"ka": "...", "en": "...", "ru": "..."}}]. '
                             'Districts also need "city" with an existing city code.'),
            "fields": ("delivery_cities", "delivery_districts"),
        }),
        (_("Invoice"), {"fields": ("invoice_seller_name_i18n", "invoice_seller_code", "invoice_seller_address_i18n",
                                   "invoice_seller_phone", "invoice_seller_email", "invoice_bank_details_i18n",
                                   "invoice_payment_due_text_i18n", "invoice_footer_text_i18n")}),
        (None, {"fields": ("created_at", "updated_at")}),
    )

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CurrencySettings)
class CurrencySettingsAdmin(admin.ModelAdmin):
    list_display = ("currency", "vat_percent", "is_active", "updated_at")
    list_editable = ("vat_percent", "is_active")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    """Read-only list; rates come from NBG only."""

    list_display = ("date", "currency", "rate", "created_at")
    list_filter = ("currency",)
    actions = ("fetch_now",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description=_("Fetch today's NBG rate now"))
    def fetch_now(self, request, queryset):
        try:
            rate = fetch_nbg_usd()
        except RateUnavailable:
            self.message_user(request, _("The NBG rate is unavailable. Try again later."), messages.ERROR)
            return
        self.message_user(request, _("USD rate %(rate)s stored for %(date)s.") % {"rate": rate.rate, "date": rate.date},
                          messages.SUCCESS)

    def changelist_view(self, request, extra_context=None):
        # Django runs actions only on selected rows; "fetch now" needs none.
        if request.method == "POST" and request.POST.get("action") == "fetch_now" and not request.POST.get("_selected_action"):
            post = request.POST.copy()
            post.setlist("_selected_action", list(ExchangeRate.objects.values_list("pk", flat=True)[:1]) or ["0"])
            request.POST = post
        return super().changelist_view(request, extra_context)


@admin.register(Discount)
class DiscountAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "value", "customer", "tariff", "part_number", "brand", "valid_from", "valid_to",
                    "is_active")
    list_filter = ("is_active", "kind", "tariff")
    search_fields = ("name", "part_number", "brand", "customer__first_name", "customer__last_name", "customer__phone",
                     "customer__user__email")
    raw_id_fields = ("customer",)
    list_select_related = ("customer", "tariff")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PickupPoint)
class PickupPointAdmin(TranslatedFieldsAdminMixin, admin.ModelAdmin):
    textarea_i18n_fields = ("address_i18n", "working_hours_i18n")
    list_display = ("__str__", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    readonly_fields = ("created_at", "updated_at")
