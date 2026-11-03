"""Order admin. Owner decision D17 (M3): orders are read-only here until LP-BE-1103 rebuilds the operator
tools (item status, change requests, courier fee) on orders.services; M3 adds the payment actions
(LP-BE-705) and the invoice (LP-BE-604)."""

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _, ngettext

from api.errors import ApiError
from payments.models import Payment
from payments.services import mark_payment, request_payment

from .models import Order, OrderItem, OrderItemEvent, OrderSupportMessage


class ReadOnlyAdminMixin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class OperatorActionsMixin:
    def has_operate_permission(self, request):
        return request.user.has_perm("payments.change_payment")


def confirm_received(modeladmin, request, payments):
    """"Payment received": only pending invoice/manual payments (card payments are confirmed by the provider)."""
    done = sum(mark_payment(p, Payment.STATUS_PAID, confirmed_by=request.user).status == Payment.STATUS_PAID
               for p in payments.filter(provider__in=("invoice", "manual"), status=Payment.STATUS_PENDING))
    modeladmin.message_user(request, ngettext("%(n)d payment confirmed.", "%(n)d payments confirmed.", done)
                            % {"n": done}, messages.SUCCESS if done else messages.WARNING)


class PaymentInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = Payment
    extra = 0
    fields = ("kind", "provider", "amount", "currency", "status", "paid_at", "confirmed_by", "created_at")
    readonly_fields = fields


class OrderItemInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ("part_number", "name", "brand", "quantity", "unit_price_usd", "unit_price", "item_status",
              "action_required", "expected_arrival_from", "expected_arrival_to")
    readonly_fields = fields
    show_change_link = True


class OrderSupportMessageInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = OrderSupportMessage
    extra = 0
    fields = ("created_at", "sender_type", "sender_name", "message", "text_code", "is_read_by_operator")
    readonly_fields = fields


@admin.register(Order)
class OrderAdmin(OperatorActionsMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("order_number", "customer", "status", "currency", "total", "amount_paid", "balance_due",
                    "payment_method", "payment_option", "created_at")
    list_filter = ("status", "currency", "payment_method", "payment_option", "delivery_method")
    search_fields = ("order_number", "customer__first_name", "customer__last_name", "customer__phone",
                     "customer__user__email")
    list_select_related = ("customer",)
    inlines = [OrderItemInline, PaymentInline, OrderSupportMessageInline]
    actions = ["payment_received", "create_balance_invoice"]

    @admin.action(description=_("Payment received"), permissions=["operate"])
    def payment_received(self, request, queryset):
        confirm_received(self, request, Payment.objects.filter(order__in=queryset))

    @admin.action(description=_("Create balance invoice"), permissions=["operate"])
    def create_balance_invoice(self, request, queryset):
        """Same guards as POST /api/orders/<n>/payments/ (nothing_to_pay, payment_in_progress)."""
        for order in queryset:
            try:
                request_payment(order.pk, "invoice")
            except ApiError as error:
                self.message_user(request, f"{order}: {error.detail}", messages.WARNING)
            else:
                self.message_user(request, _("%(order)s: invoice created.") % {"order": order}, messages.SUCCESS)


@admin.register(OrderItem)
class OrderItemAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "order", "part_number", "name", "quantity", "unit_price", "item_status", "action_required")
    list_filter = ("item_status", "action_required")
    search_fields = ("order__order_number", "part_number", "name")
    list_select_related = ("order",)


@admin.register(OrderItemEvent)
class OrderItemEventAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "item", "event_type", "text_code", "actor_type", "visible_to_customer", "created_at")
    list_filter = ("event_type", "actor_type", "visible_to_customer")
    search_fields = ("item__order__order_number", "item__part_number")


@admin.register(OrderSupportMessage)
class OrderSupportMessageAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("id", "order", "sender_type", "is_read_by_operator", "created_at")
    list_filter = ("sender_type", "is_read_by_operator")
    search_fields = ("order__order_number", "message")
