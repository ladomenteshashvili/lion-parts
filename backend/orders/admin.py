from django.contrib import admin

from .models import Order, OrderItem, OrderItemEvent, Payment


class PaymentInline(admin.StackedInline):
    model = Payment
    extra = 0
    readonly_fields = (
        "payment_reference",
        "external_payment_id",
        "provider",
        "status",
        "amount_gel",
        "currency",
        "provider_payload",
        "paid_at",
        "created_at",
        "updated_at",
    )


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = (
        "cart_item_id",
        "quote_id",
        "part_option_id",
        "part_number",
        "name",
        "final_price_gel",
        "quantity",
        "item_status",
        "created_at",
        "updated_at",
    )


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order_number",
        "customer_name",
        "customer_phone",
        "status",
        "total_gel",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = (
        "order_number",
        "session_id",
        "customer_name",
        "customer_phone",
        "vin",
    )
    readonly_fields = ("created_at", "updated_at")
    inlines = [PaymentInline, OrderItemInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "payment_reference",
        "order",
        "provider",
        "status",
        "amount_gel",
        "currency",
        "paid_at",
        "created_at",
    )
    list_filter = ("provider", "status", "created_at", "paid_at")
    search_fields = (
        "payment_reference",
        "external_payment_id",
        "order__order_number",
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "part_number",
        "name",
        "item_status",
        "final_price_gel",
        "quantity",
        "created_at",
    )
    list_filter = ("item_status", "action_required", "created_at")
    search_fields = ("order__order_number", "part_number", "name")


@admin.register(OrderItemEvent)
class OrderItemEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "item",
        "event_type",
        "title",
        "actor_type",
        "visible_to_customer",
        "created_at",
    )
    list_filter = (
        "event_type",
        "actor_type",
        "visible_to_customer",
        "created_at",
    )
    search_fields = (
        "item__order__order_number",
        "item__part_number",
        "title",
        "message",
    )