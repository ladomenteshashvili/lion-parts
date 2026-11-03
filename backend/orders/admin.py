"""Order admin (G§10, LP-BE-1103). Every model is read-only; operators act only through actions that call
the same services as the API (orders.services, payments.services): item decisions and statuses, courier
fee proposals, cancellation (D19), payments and refunds (D20). Action permissions: the model's change
permission (orders), payments.change_payment for payment/refund actions."""

import csv

from django import forms
from django.contrib import admin, messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _, ngettext

from api.errors import ApiError
from payments.models import Payment, Refund
from payments.services import MANUAL_PROVIDERS, confirm_manual_payment, record_refund, request_payment

from . import services
from .admin_actions import OperatePermissionMixin, form_action
from .invoices import invoice_language, render_invoice
from .models import (CourierFeeProposal, Notification, NotificationChannelSetting, NotificationTemplate, Order,
                     OrderItem, OrderItemEvent, OrderSupportMessage)


class ReadOnlyAdminMixin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class OperatorActionsMixin:
    """Payment actions: payments.change_payment."""

    def has_operate_permission(self, request):
        return request.user.has_perm("payments.change_payment")


CONFIRM_REFUSED = {
    "not_manual": _("%(payment)s: card payments are confirmed by the payment provider."),
    "not_pending": _("%(payment)s: the payment is not pending."),
    "overpayment": _("%(payment)s: not confirmed, the order no longer owes this amount (already paid or cancelled)."),
}


def confirm_received(modeladmin, request, payments):
    """"Payment received" for exactly the selected payments, each through confirm_manual_payment."""
    for payment in payments:
        refused = confirm_manual_payment(payment, request.user)
        if refused:
            modeladmin.message_user(request, CONFIRM_REFUSED[refused] % {"payment": payment}, messages.WARNING)
        else:
            modeladmin.message_user(request, _("%(payment)s: payment confirmed.") % {"payment": payment},
                                    messages.SUCCESS)


def actor(request):
    return request.user.get_username()


def csv_cell(value):
    """Customer-entered text never runs as a spreadsheet formula (CSV injection)."""
    return f"'{value}" if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


class ReasonForm(forms.Form):
    reason = forms.CharField(label=_("Reason (shown to the customer)"), widget=forms.Textarea, max_length=1000)


class CourierFeeForm(forms.Form):
    fee_gel = forms.DecimalField(label=_("Courier fee (GEL)"), min_value=0, max_digits=10, decimal_places=2)
    message = forms.CharField(label=_("Message to the customer"), widget=forms.Textarea, required=False)


class ReplyForm(forms.Form):
    message = forms.CharField(label=_("Reply to the customer"), widget=forms.Textarea, max_length=2000)


class ItemActionForm(forms.Form):
    action_type = forms.ChoiceField(label=_("Decision type"), choices=[(t, t) for t in services.ACTION_TYPES])
    message = forms.CharField(label=_("Message to the customer"), widget=forms.Textarea, required=False)
    proposed_unit_price = forms.DecimalField(label=_("Proposed unit price (order currency)"), required=False,
                                             max_digits=12, decimal_places=2)
    proposed_eta_min_days = forms.IntegerField(label=_("Proposed delivery days, from"), required=False, min_value=1)
    proposed_eta_max_days = forms.IntegerField(label=_("Proposed delivery days, to"), required=False, min_value=1)
    proposed_part_number = forms.CharField(label=_("Alternative part number"), required=False, max_length=120)
    proposed_name = forms.CharField(label=_("Alternative part name"), required=False, max_length=255)
    proposed_weight_kg = forms.DecimalField(label=_("Proposed weight (kg)"), required=False, max_digits=8,
                                            decimal_places=2)


class ItemStatusForm(forms.Form):
    item_status = forms.ChoiceField(label=_("New status"), choices=[(s, s) for s in services.OPERATOR_STATUSES])
    reason = forms.CharField(label=_("Reason (required to cancel; shown to the customer)"), widget=forms.Textarea,
                             required=False,
                             max_length=1000)


class PaymentInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = Payment
    extra = 0
    fields = ("kind", "provider", "amount", "currency", "status", "paid_at", "confirmed_by", "created_at")
    readonly_fields = fields


class CourierFeeProposalInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = CourierFeeProposal
    extra = 0
    fields = ("proposed_at", "fee_gel", "exchange_rate", "amount", "currency", "status", "proposed_by", "answered_at",
              "message")
    readonly_fields = fields


class OrderItemInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ("part_number", "name", "brand", "quantity", "supplier_price_usd", "unit_price_usd", "unit_price",
              "item_status", "action_required", "action_type", "purchased_at", "expected_arrival_from",
              "expected_arrival_to")
    readonly_fields = fields
    show_change_link = True


class OrderSupportMessageInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = OrderSupportMessage
    extra = 0
    fields = ("created_at", "sender_type", "sender_name", "item", "message", "text_code", "is_read_by_operator")
    readonly_fields = fields


@admin.register(Order)
class OrderAdmin(OperatePermissionMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("order_number", "customer", "status", "currency", "total", "amount_paid", "balance_due",
                    "courier_fee_action_required", "payment_method", "payment_option", "created_at")
    list_filter = ("status", "currency", "payment_method", "payment_option", "delivery_method",
                   "courier_fee_action_required")
    search_fields = ("order_number", "customer__first_name", "customer__last_name", "customer__phone",
                     "customer__user__email")
    list_select_related = ("customer",)
    readonly_fields = ("invoice_link", "refunds")
    fieldsets = [
        (None, {"fields": ("order_number", "customer", "status", "invoice_link", "created_at", "cancelled_at",
                           "cancel_reason")}),
        (_("Money (order currency, read-only)"), {"fields": (
            "currency", "exchange_rate", "payment_method", "payment_option", "split_upfront_percent", "items_total",
            "discount_total", "courier_fee", "total", "amount_due_now", "amount_paid", "balance_due", "refunds")}),
        (_("Delivery"), {"fields": (
            "delivery_method", "pickup_point", "delivery_city", "delivery_district", "delivery_address",
            "delivery_comment", "courier_fee_action_required", "proposed_courier_fee_gel", "courier_fee_message",
            "courier_fee_confirmed_at", "courier_fee_declined_at")}),
        (_("Billing snapshot"), {"fields": ("billing_snapshot", "terms_accepted_at")}),
    ]
    inlines = [OrderItemInline, PaymentInline, CourierFeeProposalInline, OrderSupportMessageInline]
    actions = ["payment_received", "create_balance_invoice", "refund_owed", "propose_courier_fee", "cancel_order",
               "reply_to_customer", "export_invoice_csv"]

    def has_payments_permission(self, request):
        return request.user.has_perm("payments.change_payment")

    def get_urls(self):
        return [path("<int:order_id>/invoice/", self.admin_site.admin_view(self.invoice_view),
                     name="orders_order_invoice")] + super().get_urls()

    def invoice_view(self, request, order_id):
        if not self.has_view_permission(request):
            return HttpResponse(status=403)
        order = get_object_or_404(Order, pk=order_id)
        language = invoice_language(request.GET.get("lang"), "ka")
        return HttpResponse(render_invoice(order, language), content_type="text/html; charset=utf-8")

    @admin.display(description=_("Invoice"))
    def invoice_link(self, obj):
        return format_html('<a href="{}" target="_blank">{}</a>', reverse("admin:orders_order_invoice", args=[obj.pk]),
                           _("Print invoice"))

    @admin.display(description=_("Refunds"))
    def refunds(self, obj):
        rows = Refund.objects.filter(payment__order=obj).select_related("created_by")
        return "; ".join(f"-{r.amount} {r.payment.currency} ({r.created_at:%Y-%m-%d}, {r.created_by}) {r.reason}"
                         for r in rows) or "-"

    @admin.action(description=_("Payment received"), permissions=["payments"])
    def payment_received(self, request, queryset):
        """Only for an order with exactly one pending invoice/manual payment; otherwise the operator
        picks the payment in the payments admin."""
        for order in queryset:
            pending = list(order.payments.filter(provider__in=MANUAL_PROVIDERS, status=Payment.STATUS_PENDING))
            if len(pending) == 1:
                confirm_received(self, request, pending)
            else:
                self.message_user(request, ngettext(
                    "%(order)s: %(n)d pending invoice payment; choose it in Payments.",
                    "%(order)s: %(n)d pending invoice payments; choose one in Payments.", len(pending))
                    % {"order": order, "n": len(pending)}, messages.WARNING)

    @admin.action(description=_("Create balance invoice"), permissions=["payments"])
    def create_balance_invoice(self, request, queryset):
        """Same guards as POST /api/orders/<n>/payments/ (nothing_to_pay, payment_in_progress)."""
        for order in queryset:
            try:
                request_payment(order.pk, "invoice")
            except ApiError as error:
                self.message_user(request, f"{order}: {error.detail}", messages.WARNING)
            else:
                self.message_user(request, _("%(order)s: invoice created.") % {"order": order}, messages.SUCCESS)

    @admin.action(description=_("Record refund of the amount owed"), permissions=["payments"])
    def refund_owed(self, request, queryset):
        """D20: records exactly what the order owes back (negative balance), never more than was collected."""
        return form_action(self, request, queryset, ReasonForm, _("Record refund of the amount owed"),
                           lambda order, data: _("refunded %(amount)s %(currency)s") % {
                               "amount": record_refund(order.pk, request.user, data["reason"]),
                               "currency": order.currency})

    @admin.action(description=_("Propose courier fee"), permissions=["operate"])
    def propose_courier_fee(self, request, queryset):
        def run(order, data):
            proposal = services.propose_courier_fee(order.pk, data["fee_gel"], data["message"], actor=request.user)
            return _("proposed %(amount)s %(currency)s") % {"amount": proposal.amount, "currency": proposal.currency}
        return form_action(self, request, queryset, CourierFeeForm, _("Propose courier fee"), run)

    @admin.action(description=_("Cancel order"), permissions=["operate"])
    def cancel_order(self, request, queryset):
        """D19: any open order, reason required; collected money stays and shows as refund_pending."""
        def run(order, data):
            services.cancel_order(order.pk, data["reason"], actor=OrderItemEvent.ACTOR_OPERATOR,
                                  actor_name=actor(request), operator=True)
            return _("cancelled")
        return form_action(self, request, queryset, ReasonForm, _("Cancel order"), run)

    @admin.action(description=_("Reply to the customer"), permissions=["operate"])
    def reply_to_customer(self, request, queryset):
        def run(order, data):
            services.reply_to_order(order, data["message"], actor(request))
            return _("reply sent")
        return form_action(self, request, queryset, ReplyForm, _("Reply to the customer"), run)

    @admin.action(description=_("Invoice CSV export"))
    def export_invoice_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="orders_invoice_export.csv"'
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(["Order number", "Buyer type", "Buyer name", "Buyer identification code", "Buyer phone",
                         "Buyer email", "VIN", "Line type", "Part number", "Description", "Quantity", "Currency",
                         "Unit price", "Line total", "Courier fee", "Order total", "Amount paid", "Balance due",
                         "Order status", "Created at"])
        for order in queryset.prefetch_related("items").order_by("created_at", "id"):
            b = order.billing_snapshot
            legal = b.get("account_type") == "legal"
            buyer = [("Legal entity" if legal else "Personal"),
                     b.get("company_official_name") if legal else f"{b.get('first_name', '')} {b.get('last_name', '')}".strip(),
                     (b.get("company_identification_code") if legal else b.get("personal_id")) or "",
                     b.get("phone") or "", b.get("email") or ""]
            tail = [order.courier_fee, order.total, order.amount_paid, order.balance_due, order.status,
                    order.created_at.isoformat()]
            for item in order.items.all():
                if item.item_status == OrderItem.STATUS_CANCELLED:
                    continue
                writer.writerow(map(csv_cell, [order.order_number, *buyer, item.vin, "part", item.part_number,
                                               item.name, item.quantity, order.currency, item.unit_price,
                                               item.line_total, *tail]))
            if order.courier_fee:
                writer.writerow(map(csv_cell, [order.order_number, *buyer, "", "courier", "", "Courier", 1,
                                               order.currency, order.courier_fee, order.courier_fee, *tail]))
        return response


@admin.register(OrderItem)
class OrderItemAdmin(OperatePermissionMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "order", "part_number", "name", "quantity", "unit_price", "item_status", "action_required",
                    "action_type")
    list_filter = ("item_status", "action_required", "action_type")
    search_fields = ("order__order_number", "part_number", "name")
    list_select_related = ("order",)
    actions = ["request_decision", "change_status"]

    @admin.action(description=_("Request a customer decision"), permissions=["operate"])
    def request_decision(self, request, queryset):
        def run(item, data):
            services.request_item_action(item.pk, actor_name=actor(request), **data)
            return _("decision requested")
        return form_action(self, request, queryset, ItemActionForm, _("Request a customer decision"), run)

    @admin.action(description=_("Change status"), permissions=["operate"])
    def change_status(self, request, queryset):
        def run(item, data):
            changed = services.set_item_status(item.pk, data["item_status"], actor_name=actor(request),
                                               reason=data["reason"])
            return _("status changed") if changed else _("already in this status")
        return form_action(self, request, queryset, ItemStatusForm, _("Change status"), run)


@admin.register(OrderItemEvent)
class OrderItemEventAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "item", "event_type", "text_code", "actor_type", "actor_name", "visible_to_customer",
                    "created_at")
    list_filter = ("event_type", "actor_type", "visible_to_customer")
    search_fields = ("item__order__order_number", "item__part_number", "actor_name")


@admin.register(OrderSupportMessage)
class OrderSupportMessageAdmin(OperatePermissionMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "order", "sender_type", "sender_name", "is_read_by_operator", "created_at")
    list_filter = ("sender_type", "is_read_by_operator")
    search_fields = ("order__order_number", "message")
    actions = ["mark_read"]

    @admin.action(description=_("Mark read"), permissions=["operate"])
    def mark_read(self, request, queryset):
        queryset.update(is_read_by_operator=True)


class NotificationTemplateForm(forms.ModelForm):
    class Meta:
        model = NotificationTemplate
        fields = ("language", "sms_template", "email_subject", "email_template")

    def clean(self):
        """A template that doesn't compile is refused here, not at sending time."""
        from django.template import Template, TemplateSyntaxError

        data = super().clean()
        for field in ("sms_template", "email_subject", "email_template"):
            try:
                Template(data.get(field) or "")
            except TemplateSyntaxError as error:
                self.add_error(field, str(error))
        return data


class NotificationTemplateInline(admin.StackedInline):
    """One row per language (LP-BE-1305); a missing language falls back to the default one."""

    model = NotificationTemplate
    form = NotificationTemplateForm
    extra = 0


@admin.register(NotificationChannelSetting)
class NotificationChannelSettingAdmin(admin.ModelAdmin):
    """One row per event (seeded); the channel and the event's templates are editable."""

    list_display = ("event_type", "channel")
    list_editable = ("channel",)
    readonly_fields = ("event_type",)
    inlines = [NotificationTemplateInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "event_type", "customer", "order", "read_at", "created_at")
    list_filter = ("event_type",)
    search_fields = ("order__order_number", "customer__user__email")
    list_select_related = ("customer", "order")
    exclude = ("token",)  # the public link token is never shown
