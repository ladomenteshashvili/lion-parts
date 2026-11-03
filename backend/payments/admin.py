"""Payment admin (G§7.10, LP-BE-705): read-only; operators change payments only through the actions,
which go through mark_payment."""

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _, ngettext

from orders.admin import OperatorActionsMixin, ReadOnlyAdminMixin, confirm_received

from .models import Payment
from .services import mark_payment


@admin.register(Payment)
class PaymentAdmin(OperatorActionsMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "order", "kind", "provider", "amount", "currency", "status", "paid_at", "confirmed_by",
                    "created_at")
    list_filter = ("status", "provider", "kind", "currency")
    search_fields = ("order__order_number", "provider_order_id", "provider_payment_id")
    list_select_related = ("order", "confirmed_by")
    actions = ["payment_received", "mark_refunded"]

    @admin.action(description=_("Payment received"), permissions=["operate"])
    def payment_received(self, request, queryset):
        confirm_received(self, request, queryset)

    @admin.action(description=_("Mark refunded"), permissions=["operate"])
    def mark_refunded(self, request, queryset):
        done = sum(mark_payment(p, Payment.STATUS_REFUNDED).status == Payment.STATUS_REFUNDED
                   for p in queryset.filter(status=Payment.STATUS_PAID))
        self.message_user(request, ngettext("%(n)d payment marked refunded.", "%(n)d payments marked refunded.",
                                            done) % {"n": done}, messages.SUCCESS if done else messages.WARNING)
