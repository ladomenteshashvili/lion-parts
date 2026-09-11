from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from accounts.customer_sessions import get_customer_for_session
from accounts.models import Customer

from .models import CarrierService, PartQuoteRequest, PartSearchLog
from .providers import PartsProviderError, calculate_part_price_provider

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
            "description": (
                "წონის ფასის მოთხოვნაზე შეავსეთ მხოლოდ დაზუსტებული წონა — "
                "საბოლოო ფასს სისტემა action-ის გაშვებისას ავტომატურად დათვლის."
            ),
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

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))

        if obj and obj.request_type == PartQuoteRequest.REQUEST_TYPE_WEIGHT_PRICE:
            readonly_fields.append("final_price_gel")

        return readonly_fields

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
        missing_data_ids = []
        calculation_failed_ids = []

        for quote_request in queryset:
            if (
                quote_request.prepared_weight_kg is None
                or quote_request.prepared_weight_kg <= 0
            ):
                missing_data_ids.append(quote_request.id)
                continue

            if quote_request.request_type == PartQuoteRequest.REQUEST_TYPE_WEIGHT_PRICE:
                customer = get_customer_for_session(quote_request.session_id)

                if customer is None:
                    customer = Customer.objects.filter(
                        phone=quote_request.customer_phone,
                    ).first()

                if customer is None:
                    calculation_failed_ids.append(quote_request.id)
                    continue

                try:
                    option = calculate_part_price_provider(
                        part_number=quote_request.part_number,
                        part_option_id=quote_request.part_option_id,
                        weight_kg=quote_request.prepared_weight_kg,
                        customer=customer,
                    )
                except PartsProviderError:
                    calculation_failed_ids.append(quote_request.id)
                    continue

                try:
                    final_price_gel = Decimal(str(option.get("final_price_gel")))
                except (InvalidOperation, TypeError):
                    final_price_gel = None

                if (
                    final_price_gel is None
                    or not final_price_gel.is_finite()
                    or final_price_gel <= 0
                ):
                    calculation_failed_ids.append(quote_request.id)
                    continue

                quote_request.final_price_gel = final_price_gel
                quote_request.currency = option.get("currency") or "GEL"
                quote_request.name = option.get("name") or quote_request.name
                quote_request.condition = (
                    option.get("condition") or quote_request.condition
                )
                quote_request.brand = option.get("brand") or quote_request.brand
                quote_request.availability = (
                    option.get("availability") or quote_request.availability
                )
                quote_request.eta_days = (
                    option.get("eta_days") or quote_request.eta_days
                )
            elif (
                quote_request.final_price_gel is None
                or quote_request.final_price_gel <= 0
            ):
                missing_data_ids.append(quote_request.id)
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

        if missing_data_ids:
            self.message_user(
                request,
                (
                    f"{len(missing_data_ids)} მოთხოვნა გამოტოვებულია "
                    f"(ID: {', '.join(map(str, missing_data_ids))}) — "
                    "წონის მოთხოვნაზე შეავსეთ წონა; სხვა მოთხოვნაზე — წონა და ფასი."
                ),
                level=messages.WARNING,
            )

        if calculation_failed_ids:
            self.message_user(
                request,
                (
                    f"{len(calculation_failed_ids)} მოთხოვნაზე ფასი ვერ დაითვალა "
                    f"(ID: {', '.join(map(str, calculation_failed_ids))}). "
                    "შეამოწმეთ მომხმარებელი, ნაწილის შეთავაზება და provider-ის კავშირი."
                ),
                level=messages.ERROR,
            )
