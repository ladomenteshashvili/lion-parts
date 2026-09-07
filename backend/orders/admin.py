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

ITEM_STATUS_CUSTOMER_MESSAGES = {
    OrderItem.ITEM_STATUS_CHECKING: (
        "შემოწმება დაიწყო",
        "ოპერატორი ამოწმებს ნაწილის ხელმისაწვდომობას, ფასს, ვადას და თავსებადობას.",
    ),
    OrderItem.ITEM_STATUS_PURCHASED: (
        "ნაწილი შეძენილია",
        "ნაწილი შეძენილია და ველოდებით აშშ-ის საწყობში მიღებას.",
    ),
    OrderItem.ITEM_STATUS_RECEIVED_USA: (
        "ნაწილი მიღებულია აშშ-ში",
        "ნაწილი მიღებულია აშშ-ის საწყობში.",
    ),
    OrderItem.ITEM_STATUS_SHIPPED_TO_GEORGIA: (
        "ნაწილი გამოიგზავნა საქართველოში",
        "ნაწილი გზაშია საქართველოსკენ.",
    ),
    OrderItem.ITEM_STATUS_RECEIVED_GEORGIA: (
        "ნაწილი მიღებულია საქართველოში",
        "ნაწილი მიღებულია საქართველოში და მზადდება გაცემისთვის.",
    ),
    OrderItem.ITEM_STATUS_READY_FOR_PICKUP: (
        "ნაწილი მზად არის გასაცემად",
        "ნაწილი მზად არის მისაღებად.",
    ),
    OrderItem.ITEM_STATUS_COMPLETED: (
        "შეკვეთა დასრულებულია",
        "ნაწილის პროცესი დასრულებულია.",
    ),
    OrderItem.ITEM_STATUS_CANCELLED: (
        "ნაწილი გაუქმებულია",
        "ამ ნაწილის პროცესი გაუქმდა.",
    ),
}


def sync_order_status_after_item_change(order):
    if order.items.filter(action_required=True).exists():
        new_order_status = Order.STATUS_ACTION_REQUIRED
    elif order.items.exists() and not order.items.exclude(
        item_status=OrderItem.ITEM_STATUS_COMPLETED
    ).exists():
        new_order_status = Order.STATUS_COMPLETED
    elif order.items.exists() and not order.items.exclude(
        item_status=OrderItem.ITEM_STATUS_CANCELLED
    ).exists():
        new_order_status = Order.STATUS_CANCELLED
    elif order.status not in [
        Order.STATUS_PAYMENT_PENDING,
        Order.STATUS_CANCELLED,
    ]:
        new_order_status = Order.STATUS_PROCESSING
    else:
        return

    if order.status != new_order_status:
        order.status = new_order_status
        order.save(update_fields=["status", "updated_at"])


def set_order_item_status_from_admin(item, new_status, actor_name="Admin"):
    valid_statuses = dict(OrderItem.ITEM_STATUS_CHOICES)

    if new_status not in valid_statuses:
        return "invalid_status"

    with transaction.atomic():
        locked_item = (
            OrderItem.objects.select_for_update()
            .select_related("order")
            .get(pk=item.pk)
        )
        order = Order.objects.select_for_update().get(pk=locked_item.order_id)

        if locked_item.item_status == new_status:
            return "skipped_same_status"

        if (
            order.status == Order.STATUS_PAYMENT_PENDING
            and new_status != OrderItem.ITEM_STATUS_CANCELLED
        ):
            return "skipped_payment_pending"

        old_status = locked_item.item_status
        old_value = {
            "item_status": old_status,
            "action_required": locked_item.action_required,
            "action_type": locked_item.action_type,
            "action_message": locked_item.action_message,
        }

        locked_item.item_status = new_status

        if new_status != OrderItem.ITEM_STATUS_ACTION_REQUIRED:
            locked_item.action_required = False
            locked_item.action_type = OrderItem.ACTION_TYPE_NONE
            locked_item.action_message = ""
            locked_item.proposed_final_price_gel = None
            locked_item.proposed_eta_days = None
            locked_item.proposed_expected_arrival_date = None

        locked_item.save()

        title, message = ITEM_STATUS_CUSTOMER_MESSAGES.get(
            new_status,
            (
                "ნაწილის სტატუსი შეიცვალა",
                f"სტატუსი შეიცვალა: {old_status} → {new_status}",
            ),
        )

        OrderItemEvent.objects.create(
            item=locked_item,
            event_type=OrderItemEvent.EVENT_TYPE_STATUS_CHANGED,
            title=title,
            message=message,
            old_value=old_value,
            new_value={
                "item_status": locked_item.item_status,
                "action_required": locked_item.action_required,
                "action_type": locked_item.action_type,
                "action_message": locked_item.action_message,
            },
            actor_type=OrderItemEvent.ACTOR_TYPE_ADMIN,
            actor_name=actor_name,
            visible_to_customer=True,
        )

        sync_order_status_after_item_change(order)

    return "updated"



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
        "order_status",
        "part_number",
        "name",
        "item_status",
        "action_required",
        "final_price_gel",
        "quantity",
        "created_at",
    )
    list_filter = ("item_status", "action_required", "order__status", "created_at")
    search_fields = (
        "order__order_number",
        "order__customer_name",
        "order__customer_phone",
        "part_number",
        "name",
    )
    list_select_related = ("order",)
    actions = [
        "mark_items_checking",
        "mark_items_purchased",
        "mark_items_received_usa",
        "mark_items_shipped_to_georgia",
        "mark_items_received_georgia",
        "mark_items_ready_for_pickup",
        "mark_items_completed",
        "mark_items_cancelled",
    ]

    @admin.display(description="Order status")
    def order_status(self, obj):
        return obj.order.status

    def apply_status_action(self, request, queryset, new_status):
        result_counts = {
            "updated": 0,
            "skipped_same_status": 0,
            "skipped_payment_pending": 0,
            "invalid_status": 0,
        }

        for item in queryset.select_related("order"):
            result = set_order_item_status_from_admin(
                item=item,
                new_status=new_status,
                actor_name=request.user.get_username() or "Admin",
            )
            result_counts[result] = result_counts.get(result, 0) + 1

        if result_counts["updated"]:
            self.message_user(
                request,
                f"{result_counts['updated']} ნაწილი განახლდა.",
                messages.SUCCESS,
            )

        skipped = (
            result_counts["skipped_same_status"]
            + result_counts["skipped_payment_pending"]
            + result_counts["invalid_status"]
        )

        if skipped:
            self.message_user(
                request,
                (
                    f"{skipped} ნაწილი გამოტოვებულია. "
                    "შესაძლოა სტატუსი უკვე იგივე იყო ან შეკვეთა ჯერ Payment pending არის."
                ),
                messages.WARNING,
            )

    @admin.action(description="ოპერატორი: შემოწმება დაიწყო")
    def mark_items_checking(self, request, queryset):
        self.apply_status_action(
            request,
            queryset,
            OrderItem.ITEM_STATUS_CHECKING,
        )

    @admin.action(description="ოპერატორი: ნაწილი შეძენილია")
    def mark_items_purchased(self, request, queryset):
        self.apply_status_action(
            request,
            queryset,
            OrderItem.ITEM_STATUS_PURCHASED,
        )

    @admin.action(description="ოპერატორი: მიღებულია აშშ-ში")
    def mark_items_received_usa(self, request, queryset):
        self.apply_status_action(
            request,
            queryset,
            OrderItem.ITEM_STATUS_RECEIVED_USA,
        )

    @admin.action(description="ოპერატორი: გამოიგზავნა საქართველოში")
    def mark_items_shipped_to_georgia(self, request, queryset):
        self.apply_status_action(
            request,
            queryset,
            OrderItem.ITEM_STATUS_SHIPPED_TO_GEORGIA,
        )

    @admin.action(description="ოპერატორი: მიღებულია საქართველოში")
    def mark_items_received_georgia(self, request, queryset):
        self.apply_status_action(
            request,
            queryset,
            OrderItem.ITEM_STATUS_RECEIVED_GEORGIA,
        )

    @admin.action(description="ოპერატორი: მზად არის გასაცემად")
    def mark_items_ready_for_pickup(self, request, queryset):
        self.apply_status_action(
            request,
            queryset,
            OrderItem.ITEM_STATUS_READY_FOR_PICKUP,
        )

    @admin.action(description="ოპერატორი: დასრულებულია")
    def mark_items_completed(self, request, queryset):
        self.apply_status_action(
            request,
            queryset,
            OrderItem.ITEM_STATUS_COMPLETED,
        )

    @admin.action(description="ოპერატორი: გაუქმებულია")
    def mark_items_cancelled(self, request, queryset):
        self.apply_status_action(
            request,
            queryset,
            OrderItem.ITEM_STATUS_CANCELLED,
        )


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