from django.conf import settings
from django.contrib import admin, messages
from django.db.models import Count, Q
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from api.errors import ApiError
from orders.services import OperatorError

from .models import (CarrierService, PartImage, PartMaster, PartQuoteRequest, PartSearchLog, PartWeight,
                     PartWeightEvent, WeightTask, WeightTaskRequest)
from .registry import is_plausible, record_weight, review_weight
from .services import mark_quote_ready
from .weight_tasks import approve_task, cancel_task


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
                                 supplier_price_usd=None if qr.offer_id else qr.supplier_price_usd, actor=request.user)
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


# Parts Master Registry (LPK-238 to LPK-240)

class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class PartWeightInline(ReadOnlyInline):
    model = PartWeight
    fields = ("weight_kg", "source", "status", "conflict", "implausible", "observed_at", "seen_count", "reviewed_by")


class WeightKnownFilter(admin.SimpleListFilter):
    title = _("weight")
    parameter_name = "weight"

    def lookups(self, request, model_admin):
        return (("known", _("approved weight known")), ("missing", _("no approved weight")))

    def queryset(self, request, queryset):
        if self.value() == "known":
            return queryset.filter(current_weight__isnull=False)
        if self.value() == "missing":
            return queryset.filter(current_weight__isnull=True)
        return queryset


@admin.register(PartMaster)
class PartMasterAdmin(admin.ModelAdmin):
    """Shared knowledge about a part: no customer appears here. Parts come from searches (and the backfill)."""

    list_display = ("part_number", "brand", "name", "weight", "search_count", "vin_search_count",
                    "last_supplier_price_usd", "last_searched_at", "open_task")
    list_filter = (WeightKnownFilter, "current_weight__source", "last_searched_at")
    search_fields = ("part_number", "part_number_key", "brand", "brand_key", "name")
    list_select_related = ("current_weight",)
    inlines = (PartWeightInline,)
    readonly_fields = ("part_number", "brand", "part_number_key", "brand_key", "weight", "search_count",
                       "vin_search_count", "last_supplier_price_usd", "first_seen_at", "last_searched_at", "open_task",
                       "updated_at")
    fields = ("part_number", "brand", "name", "part_number_key", "brand_key", "weight", "search_count",
              "vin_search_count", "last_supplier_price_usd", "first_seen_at", "last_searched_at", "open_task",
              "updated_at")

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            open_tasks=Count("weight_tasks", filter=Q(weight_tasks__status__in=WeightTask.OPEN_STATUSES)))

    @admin.display(description=_("current weight"))
    def weight(self, obj):
        w = obj.current_weight
        return f"{w.weight_kg} kg · {w.get_source_display()}" if w else "—"

    @admin.display(description=_("awaiting weight"), boolean=True)
    def open_task(self, obj):
        return bool(getattr(obj, "open_tasks", 0))


class PartWeightEventInline(ReadOnlyInline):
    model = PartWeightEvent
    fields = ("created_at", "action", "actor", "note")
    readonly_fields = fields
    show_change_link = False


@admin.register(PartWeight)
class PartWeightAdmin(admin.ModelAdmin):
    """Weight observations. A correction is a new observation: an operator or physically measured weight is approved
    by the operator who enters it; an AI estimate waits for approval. Nothing is edited in place."""

    list_display = ("part", "weight_kg", "source", "status", "conflict", "implausible", "observed_at", "seen_count",
                    "reviewed_by", "is_current")
    list_filter = ("status", "source", "conflict", "implausible", "observed_at")
    search_fields = ("part__part_number", "part__part_number_key", "part__brand")
    list_select_related = ("part", "reviewed_by")
    raw_id_fields = ("part",)
    inlines = (PartWeightEventInline,)
    actions = ("approve", "reject")

    def get_inlines(self, request, obj):
        return self.inlines if obj else ()

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("part", "weight_kg", "source", "evidence")
        return ("part", "weight_kg", "source", "status", "is_current", "conflict", "implausible", "observed_at",
                "last_seen_at", "seen_count", "search_log", "evidence", "reviewed_by", "reviewed_at")

    def get_readonly_fields(self, request, obj=None):
        return () if obj is None else self.get_fields(request, obj)

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == "source":  # supplier weights come from AMT only
            kwargs["choices"] = [c for c in PartWeight.SOURCES if c[0] != PartWeight.SOURCE_AMT]
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if change:
            return
        approved = obj.source != PartWeight.SOURCE_AI and is_plausible(obj.weight_kg)
        saved = record_weight(obj.part, obj.weight_kg, obj.source, actor=request.user, evidence=obj.evidence,
                              status=PartWeight.STATUS_APPROVED if approved else PartWeight.STATUS_PENDING,
                              note="Admin")
        obj.pk = saved.pk
        if not approved:
            self.message_user(request, _("Saved for review: AI estimates and weights outside 0.01 to 500 kg are used "
                                         "only after approval."), messages.WARNING)
        elif saved.conflict:
            self.message_user(request, _("This weight conflicts with the part's current weight; check both."),
                              messages.WARNING)

    def has_delete_permission(self, request, obj=None):
        return False  # the audit trail stays complete; reject a wrong weight instead

    @admin.display(description=_("in use"), boolean=True)
    def is_current(self, obj):
        return obj.part.current_weight_id == obj.pk

    def _review(self, request, queryset, status):
        for weight in queryset:
            if review_weight(weight.pk, status, request.user):
                weight.refresh_from_db()
                text = _("%(weight)s: done; the part's current weight is now %(current)s.") % {
                    "weight": weight, "current": weight.part.current_weight or "—"}
                self.message_user(request, text, messages.WARNING if weight.conflict else messages.SUCCESS)
            else:
                self.message_user(request, _("%(weight)s: already so; nothing changed.") % {"weight": weight},
                                  messages.INFO)

    @admin.action(description=_("Approve the selected weights"))
    def approve(self, request, queryset):
        self._review(request, queryset, PartWeight.STATUS_APPROVED)

    @admin.action(description=_("Reject the selected weights"))
    def reject(self, request, queryset):
        self._review(request, queryset, PartWeight.STATUS_REJECTED)


class WeightTaskRequestInline(ReadOnlyInline):
    model = WeightTaskRequest
    fields = ("customer", "search_log", "offer_id", "outcome", "quote_request", "updated_at")
    readonly_fields = fields
    show_change_link = False


class OpenTaskFilter(admin.SimpleListFilter):
    title = _("queue")
    parameter_name = "queue"

    def lookups(self, request, model_admin):
        return (("open", _("open")), ("closed", _("resolved or cancelled")))

    def queryset(self, request, queryset):
        if self.value() == "open":
            return queryset.filter(status__in=WeightTask.OPEN_STATUSES)
        if self.value() == "closed":
            return queryset.exclude(status__in=WeightTask.OPEN_STATUSES)
        return queryset


@admin.register(WeightTask)
class WeightTaskAdmin(admin.ModelAdmin):
    """The missing-weight queue: one row per part the supplier prices without a usable weight."""

    list_display = ("id", "part", "status", "waiting_customers", "request_count", "supplier_price_usd",
                    "proposed_weight_kg", "approved_weight", "last_error", "created_at", "resolved_at")
    list_filter = (OpenTaskFilter, "status", "created_at")
    search_fields = ("part__part_number", "part__part_number_key", "part__brand")
    list_select_related = ("part", "approved_weight")
    inlines = (WeightTaskRequestInline,)
    actions = ("approve_and_price", "cancel")
    readonly_fields = ("part", "status", "supplier_price_usd", "last_search_log", "offer_id", "request_count",
                       "known_weight", "approved_weight", "last_error", "created_at", "resolved_at", "resolved_by")
    fieldsets = (
        (_("Part"), {"fields": ("part", "status", "known_weight", "supplier_price_usd", "last_search_log", "offer_id",
                                "request_count", "last_error")}),
        (_("Weight"), {
            "description": _("Enter the weight and its source, save, then run “Approve the weight and price the "
                             "waiting customers”. Every waiting customer is priced with their own tariff and the "
                             "supplier's current price, and notified once. Leave the weight empty to use the "
                             "part's approved weight shown above. If the supplier is unavailable, the weight stays "
                             "approved; run the action again later."),
            "fields": ("proposed_weight_kg", "proposed_source", "evidence")}),
        (_("Result"), {"fields": ("approved_weight", "resolved_at", "resolved_by")}),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            waiting=Count("requests", filter=Q(requests__outcome=WeightTaskRequest.OUTCOME_WAITING)))

    @admin.display(description=_("customers waiting"), ordering="waiting")
    def waiting_customers(self, obj):
        return getattr(obj, "waiting", 0)

    @admin.display(description=_("approved weight on record"))
    def known_weight(self, obj):
        w = obj.part.current_weight
        return f"{w.weight_kg} kg · {w.get_source_display()}" if w else "—"

    @admin.action(description=_("Approve the weight and price the waiting customers"))
    def approve_and_price(self, request, queryset):
        for task in queryset:
            try:
                done = approve_task(task.pk, request.user, task.proposed_weight_kg, task.proposed_source,
                                    task.evidence)
            except OperatorError as error:
                self.message_user(request, f"#{task.pk}: {error.message}", messages.WARNING)
                continue
            outcomes = dict(done.requests.order_by().values_list("outcome").annotate(n=Count("id")))
            self.message_user(request, _("#%(id)s: resolved; %(priced)s customers priced and notified, %(skipped)s "
                                         "not eligible, %(before)s already had a price.") % {
                "id": task.pk, "priced": outcomes.get(WeightTaskRequest.OUTCOME_PRICED, 0),
                "skipped": outcomes.get(WeightTaskRequest.OUTCOME_NOT_ELIGIBLE, 0),
                "before": outcomes.get(WeightTaskRequest.OUTCOME_ALREADY_PRICED, 0)}, messages.SUCCESS)

    @admin.action(description=_("Cancel the selected tasks"))
    def cancel(self, request, queryset):
        for task in queryset:
            if cancel_task(task.pk, request.user):
                self.message_user(request, _("#%(id)s: cancelled.") % {"id": task.pk}, messages.SUCCESS)
            else:
                self.message_user(request, _("#%(id)s: not open; nothing changed.") % {"id": task.pk}, messages.INFO)
