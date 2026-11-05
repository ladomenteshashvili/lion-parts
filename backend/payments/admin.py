"""Payment admin (G§7.10, LP-BE-705): read-only; operators change payments only through the actions,
which go through mark_payment."""

import logging

from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _, ngettext

from orders.admin import OperatorActionsMixin, ReadOnlyAdminMixin, confirm_received

from .models import Payment, Refund
from .providers import get_provider
from .services import MANUAL_PROVIDERS, mark_payment, recheck_payment, refund_whole_payment

logger = logging.getLogger(__name__)
RECHECK_NOT_ELIGIBLE = _("%(payment)s: only pending or unknown card payments can be re-checked.")
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
    "error": (_("%(payment)s: the provider could not confirm the status; nothing changed. Try again later or follow "
                "the manual escalation process."), messages.ERROR),
}


class RefundInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = Refund
    extra = 0
    fields = ("amount", "reason", "created_by", "created_at")
    readonly_fields = fields


@admin.register(Payment)
class PaymentAdmin(OperatorActionsMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "order", "kind", "provider", "amount", "currency", "status_display", "paid_at",
                    "confirmed_by", "created_at")
    list_filter = ("status", "provider", "kind", "currency")
    search_fields = ("order__order_number", "provider_order_id", "provider_payment_id")
    list_select_related = ("order", "confirmed_by")
    inlines = [RefundInline]
    actions = ["payment_received", "cancel_invoice", "mark_refunded", "recheck_with_provider"]

    @admin.display(description=_("Status"), ordering="status")
    def status_display(self, obj):
        """`unknown` (reconciliation gave up, G§7.10) stands out for the operator."""
        if obj.status == Payment.STATUS_UNKNOWN:
            return format_html('<strong style="color:#ba2121">{}</strong>', obj.status)
        return obj.status

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
        """Only card payments still pending or unknown, and only with their own provider. Results come from the
        provider through mark_payment, never from the operator. Admin actions run outside any transaction."""
        provider = get_provider()
        for payment in queryset:
            if payment.provider in MANUAL_PROVIDERS or payment.status not in (Payment.STATUS_PENDING,
                                                                              Payment.STATUS_UNKNOWN):
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
