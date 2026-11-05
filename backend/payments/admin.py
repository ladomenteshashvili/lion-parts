"""Payment admin (G§7.10, LP-BE-705): read-only; operators change payments only through the actions,
which go through mark_payment."""

import logging

from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _, ngettext

from orders.admin import OperatorActionsMixin, ReadOnlyAdminMixin, confirm_received

from .models import Payment, Refund
from .providers import get_provider
from .services import (MANUAL_PROVIDERS, UNRESOLVED_SESSION, mark_payment, mark_reviewed, recheck_payment,
                       refund_whole_payment)

logger = logging.getLogger(__name__)
RECHECK_NOT_ELIGIBLE = _("%(payment)s: only card payments without a final provider result can be re-checked.")
RECHECK_OTHER_PROVIDER = _("%(payment)s: this payment belongs to another provider; follow the manual escalation "
                           "process.")
RECHECK_UPDATED = {
    Payment.STATUS_PAID: _("%(payment)s: the provider confirmed the payment."),
    Payment.STATUS_FAILED: _("%(payment)s: the provider reports that the payment failed."),
    Payment.STATUS_EXPIRED: _("%(payment)s: the provider reports that the payment expired."),
}
RECHECK_OTHER = {
    "pending": (_("%(payment)s: the provider has no final result yet; nothing changed."), messages.INFO),
    "superseded": (_("%(payment)s: the payment changed during the check; nothing was overwritten."), messages.WARNING),
    "conflict": (_("%(payment)s: the provider's answers conflict; the payment is now unknown and blocks new payments "
                   "until the provider confirms it."), messages.ERROR),
    "closed": (_("%(payment)s: the provider confirmed that the payment session is closed."), messages.SUCCESS),
    "unproven": (_("%(payment)s: the provider shows the session as closed, but this is not yet enough proof; it will be "
                   "checked again."), messages.INFO),
    "error": (_("%(payment)s: the provider could not confirm the status; nothing changed. Try again later or follow "
                "the manual escalation process."), messages.ERROR),
}


REVIEW_REASONS = {
    "late_paid": _("paid after it was closed here"),
    "paid_after_closure": _("paid after the provider showed it closed"),
    "cancelled_order": _("paid on a cancelled order"),
    "overpaid": _("more than the order owed"),
}


class ReviewFilter(admin.SimpleListFilter):
    title = _("Review")
    parameter_name = "review"

    def lookups(self, request, model_admin):
        return [("waiting", _("Waiting for review")), ("reviewed", _("Reviewed"))]

    def queryset(self, request, queryset):
        if self.value() == "waiting":
            return queryset.filter(review_required_at__isnull=False, reviewed_at__isnull=True)
        if self.value() == "reviewed":
            return queryset.filter(reviewed_at__isnull=False)
        return queryset


class ProviderSessionFilter(admin.SimpleListFilter):
    title = _("Provider session")
    parameter_name = "session"

    def lookups(self, request, model_admin):
        return [("unproven", _("Not proven closed"))]

    def queryset(self, request, queryset):
        return queryset.filter(UNRESOLVED_SESSION) if self.value() == "unproven" else queryset


class RefundInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = Refund
    extra = 0
    fields = ("amount", "reason", "created_by", "created_at")
    readonly_fields = fields


@admin.register(Payment)
class PaymentAdmin(OperatorActionsMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "order", "kind", "provider", "amount", "currency", "status_display", "review_display", "paid_at",
                    "confirmed_by", "created_at")
    list_filter = ("status", ReviewFilter, ProviderSessionFilter, "provider", "kind", "currency")
    search_fields = ("order__order_number", "provider_order_id", "provider_payment_id")
    list_select_related = ("order", "confirmed_by")
    inlines = [RefundInline]
    actions = ["payment_received", "cancel_invoice", "mark_refunded", "recheck_with_provider", "mark_as_reviewed"]

    @admin.display(description=_("Status"), ordering="status")
    def status_display(self, obj):
        """`unknown` (reconciliation gave up, G§7.10) stands out for the operator."""
        if obj.status == Payment.STATUS_UNKNOWN:
            return format_html('<strong style="color:#ba2121">{}</strong>', obj.status)
        return obj.status

    @admin.display(description=_("Review"))
    def review_display(self, obj):
        if not obj.review_required_at or obj.reviewed_at:
            return ""
        return format_html('<strong style="color:#ba2121">{}</strong>',
                           ", ".join(str(REVIEW_REASONS.get(reason, reason)) for reason in obj.review_reasons))

    @admin.action(description=_("Mark as reviewed"), permissions=["operate"])
    def mark_as_reviewed(self, request, queryset):
        """Records who looked at a flagged payment. Money and status don't change."""
        for payment in queryset:
            if mark_reviewed(payment, request.user):
                logger.info("Payment %s marked reviewed by user %s", payment.pk, request.user.pk)
                self.message_user(request, _("%(payment)s: marked as reviewed.") % {"payment": payment}, messages.SUCCESS)
            else:
                self.message_user(request, _("%(payment)s: nothing to review.") % {"payment": payment}, messages.WARNING)

    @admin.action(description=_("Payment received"), permissions=["operate"])
    def payment_received(self, request, queryset):
        confirm_received(self, request, queryset)

    @admin.action(description=_("Cancel invoice"), permissions=["operate"])
    def cancel_invoice(self, request, queryset):
        """A pending invoice/manual payment the customer will not pay -> expired, so a new payment can be
        requested (a pending invoice otherwise blocks new payments)."""
        done = sum(mark_payment(p, Payment.STATUS_EXPIRED).status == Payment.STATUS_EXPIRED
                   for p in queryset.filter(provider__in=MANUAL_PROVIDERS, status=Payment.STATUS_PENDING))
        self.message_user(request, ngettext("%(n)d invoice cancelled.", "%(n)d invoices cancelled.", done)
                          % {"n": done}, messages.SUCCESS if done else messages.WARNING)

    @admin.action(description=_("Re-check with provider"), permissions=["operate"])
    def recheck_with_provider(self, request, queryset):
        """Only card payments still pending or unknown, or closed here without proof from the provider, and only with
        their own provider. Results come from the provider through mark_payment, never from the operator. Admin actions
        run outside any transaction."""
        provider = get_provider()
        for payment in queryset:
            if payment.provider in MANUAL_PROVIDERS or (
                    payment.status not in (Payment.STATUS_PENDING, Payment.STATUS_UNKNOWN)
                    and not Payment.objects.filter(UNRESOLVED_SESSION, pk=payment.pk).exists()):
                text, level = RECHECK_NOT_ELIGIBLE, messages.WARNING
            elif payment.provider != provider.name:
                text, level = RECHECK_OTHER_PROVIDER, messages.WARNING
            else:
                try:
                    outcome = recheck_payment(payment, provider, checked_by=request.user)
                except Exception as error:
                    logger.error("Re-checking payment %s failed: %s", payment.pk, type(error).__name__)
                    outcome = "error"
                logger.info("Payment %s re-checked by user %s: %s", payment.pk, request.user.pk, outcome)
                if outcome == "updated":
                    payment.refresh_from_db()  # it may have moved on again, e.g. paid and already refunded
                    text = RECHECK_UPDATED.get(payment.status, RECHECK_OTHER["superseded"][0])
                    level = messages.SUCCESS
                else:
                    text, level = RECHECK_OTHER[outcome]
            self.message_user(request, text % {"payment": payment}, level)

    @admin.action(description=_("Mark refunded"), permissions=["operate"])
    def mark_refunded(self, request, queryset):
        """D20: only when the whole remaining amount of the payment is owed back; otherwise use the order
        action "Record refund of the amount owed" (partial refund)."""
        for payment in queryset:
            refused = refund_whole_payment(payment, request.user)
            if refused == "not_paid":
                self.message_user(request, _("%(payment)s: only a paid payment can be refunded.")
                                  % {"payment": payment}, messages.WARNING)
            elif refused:
                self.message_user(request, _("%(payment)s: not refunded, the order owes back less than this payment;"
                                             " record the amount owed on the order instead.") % {"payment": payment},
                                  messages.WARNING)
            else:
                self.message_user(request, _("%(payment)s: marked refunded.") % {"payment": payment},
                                  messages.SUCCESS)
