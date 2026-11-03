"""Order admin. Owner decision D17 (M3): orders are read-only here until LP-BE-1103 rebuilds the operator
tools (item status, change requests, courier fee) on orders.services; M3 adds the payment actions
(LP-BE-705) and the invoice (LP-BE-604)."""

from django.contrib import admin

from .models import Order, OrderItem, OrderItemEvent, OrderSupportMessage


class ReadOnlyAdminMixin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
class OrderAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("order_number", "customer", "status", "currency", "total", "amount_paid", "balance_due",
                    "payment_method", "payment_option", "created_at")
    list_filter = ("status", "currency", "payment_method", "payment_option", "delivery_method")
    search_fields = ("order_number", "customer__first_name", "customer__last_name", "customer__phone",
                     "customer__user__email")
    list_select_related = ("customer",)
    inlines = [OrderItemInline, OrderSupportMessageInline]


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
