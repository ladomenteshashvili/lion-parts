from django.contrib import admin, messages
from django.db import transaction

from .models import Order, OrderItem, OrderItemEvent, Payment
from .views import confirm_order_payment, get_or_create_order_payment


def mark_order_paid_manually(order):
    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(pk=order.pk)

        if locked_order.status != Order.STATUS_PAYMENT_PENDING:
            return False

        payment = get_or_create_order_payment(locked_order)

        if payment.status == Payment.STATUS_PAID:
            return False

        confirm_order_payment(
            order=locked_order,
            payment=payment,
            source="manual_admin_confirmation",
        )

    return True


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
        "payment_status",
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
    actions = ["mark_selected_orders_paid"]

    @admin.display(description="Payment")
    def payment_status(self, obj):
        try:
            return obj.payment.status
        except Payment.DoesNotExist:
            return "missing"

    @admin.action(description="თანხა მიღებულია — შეკვეთის დადასტურება")
    def mark_selected_orders_paid(self, request, queryset):
        paid_count = 0
        skipped_count = 0

        for order in queryset:
            if mark_order_paid_manually(order):
                paid_count += 1
            else:
                skipped_count += 1

        if paid_count:
            self.message_user(
                request,
                f"{paid_count} შეკვეთაზე გადახდა დადასტურდა.",
                messages.SUCCESS,
            )

        if skipped_count:
            self.message_user(
                request,
                f"{skipped_count} შეკვეთა გამოტოვებულია — სავარაუდოდ აღარ იყო Payment pending.",
                messages.WARNING,
            )


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
    actions = ["mark_selected_payments_paid"]

    @admin.action(description="თანხა მიღებულია — payment-ის დადასტურება")
    def mark_selected_payments_paid(self, request, queryset):
        paid_count = 0
        skipped_count = 0

        for payment in queryset.select_related("order"):
            if mark_order_paid_manually(payment.order):
                paid_count += 1
            else:
                skipped_count += 1

        if paid_count:
            self.message_user(
                request,
                f"{paid_count} payment დადასტურდა.",
                messages.SUCCESS,
            )

        if skipped_count:
            self.message_user(
                request,
                f"{skipped_count} payment გამოტოვებულია.",
                messages.WARNING,
            )


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