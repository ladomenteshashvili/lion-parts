from datetime import timedelta

from django.contrib import admin, messages
from django.db import transaction
from django.utils import timezone

from .models import Order, OrderItem, OrderItemEvent, OrderSupportMessage, Payment
from .views import confirm_order_payment, get_or_create_order_payment, recalculate_order_total


class OperatorOrderTaskFilter(admin.SimpleListFilter):
    title = "ოპერატორის საქმე"
    parameter_name = "operator_task"

    def lookups(self, request, model_admin):
        return [
            ("new_paid", "ახალი გადახდილი — შესამოწმებელი"),
            ("action_required", "Customer პასუხს ელოდება"),
            ("customer_message", "Customer-ის ახალი შეტყობინება"),
            ("in_transit", "გზაში / ლოგისტიკა"),
            ("ready_pickup", "მზადაა გასაცემად"),
        ]

    def queryset(self, request, queryset):
        value = self.value()

        if value == "new_paid":
            return queryset.filter(
                status=Order.STATUS_PROCESSING,
                items__item_status=OrderItem.ITEM_STATUS_PAYMENT_CONFIRMED,
            ).distinct()

        if value == "action_required":
            return queryset.filter(
                status=Order.STATUS_ACTION_REQUIRED,
                items__action_required=True,
            ).distinct()

        if value == "customer_message":
            return queryset.filter(
                support_messages__sender_type=OrderSupportMessage.SENDER_CUSTOMER,
                support_messages__is_read_by_operator=False,
            ).distinct()

        if value == "in_transit":
            return queryset.filter(
                items__item_status__in=[
                    OrderItem.ITEM_STATUS_PURCHASED,
                    OrderItem.ITEM_STATUS_RECEIVED_USA,
                    OrderItem.ITEM_STATUS_SHIPPED_TO_GEORGIA,
                    OrderItem.ITEM_STATUS_RECEIVED_GEORGIA,
                ],
            ).distinct()

        if value == "ready_pickup":
            return queryset.filter(
                items__item_status=OrderItem.ITEM_STATUS_READY_FOR_PICKUP,
            ).distinct()

        return queryset


class OperatorItemTaskFilter(admin.SimpleListFilter):
    title = "ოპერატორის საქმე"
    parameter_name = "operator_item_task"

    def lookups(self, request, model_admin):
        return [
            ("needs_checking", "გადახდილია — შესამოწმებელი"),
            ("action_required", "Customer პასუხს ელოდება"),
            ("purchased", "შეძენილია"),
            ("received_usa", "მიღებულია აშშ-ში"),
            ("shipped", "გამოგზავნილია საქართველოში"),
            ("received_georgia", "მიღებულია საქართველოში"),
            ("ready_pickup", "მზადაა გასაცემად"),
        ]

    def queryset(self, request, queryset):
        value = self.value()

        if value == "needs_checking":
            return queryset.filter(
                order__status=Order.STATUS_PROCESSING,
                item_status=OrderItem.ITEM_STATUS_PAYMENT_CONFIRMED,
            )

        if value == "action_required":
            return queryset.filter(action_required=True)

        if value == "purchased":
            return queryset.filter(item_status=OrderItem.ITEM_STATUS_PURCHASED)

        if value == "received_usa":
            return queryset.filter(item_status=OrderItem.ITEM_STATUS_RECEIVED_USA)

        if value == "shipped":
            return queryset.filter(item_status=OrderItem.ITEM_STATUS_SHIPPED_TO_GEORGIA)

        if value == "received_georgia":
            return queryset.filter(item_status=OrderItem.ITEM_STATUS_RECEIVED_GEORGIA)

        if value == "ready_pickup":
            return queryset.filter(item_status=OrderItem.ITEM_STATUS_READY_FOR_PICKUP)

        return queryset


class SupportMessageTaskFilter(admin.SimpleListFilter):
    title = "Support საქმე"
    parameter_name = "support_task"

    def lookups(self, request, model_admin):
        return [
            ("customer_unread", "Customer-ის ახალი შეტყობინება"),
            ("operator_unread_customer", "Customer-ს ჯერ არ უნახავს operator პასუხი"),
            ("visible_customer", "Customer-visible"),
        ]

    def queryset(self, request, queryset):
        value = self.value()

        if value == "customer_unread":
            return queryset.filter(
                sender_type=OrderSupportMessage.SENDER_CUSTOMER,
                is_read_by_operator=False,
            )

        if value == "operator_unread_customer":
            return queryset.filter(
                sender_type=OrderSupportMessage.SENDER_OPERATOR,
                visible_to_customer=True,
                is_read_by_customer=False,
            )

        if value == "visible_customer":
            return queryset.filter(visible_to_customer=True)

        return queryset



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



ACTION_REQUIRED_CUSTOMER_MESSAGES = {
    OrderItem.ACTION_TYPE_PRICE_CHANGE: (
        "ფასის ცვლილების დადასტურება საჭიროა",
        "ნაწილის საბოლოო ფასი შეიცვალა. გასაგრძელებლად დაადასტურეთ ცვლილება.",
    ),
    OrderItem.ACTION_TYPE_ETA_CHANGE: (
        "მიწოდების ვადის დადასტურება საჭიროა",
        "ნაწილის მიწოდების ვადა შეიცვალა. გასაგრძელებლად დაადასტურეთ ცვლილება.",
    ),
    OrderItem.ACTION_TYPE_WEIGHT_CHANGE: (
        "წონის/ზომის გამო ფასი შეიცვალა",
        "ნაწილის რეალური წონა ან ზომა განსხვავებულია და საბოლოო ფასი დაკორექტირდა.",
    ),
    OrderItem.ACTION_TYPE_FITMENT_ISSUE: (
        "თავსებადობის შემოწმება საჭიროა",
        "VIN-თან თავსებადობაზე საჭიროა დამატებითი დადასტურება.",
    ),
    OrderItem.ACTION_TYPE_ALTERNATIVE_REQUIRED: (
        "ალტერნატიული ნაწილი შემოთავაზებულია",
        "შეკვეთილი ნაწილის ნაცვლად შემოთავაზებულია ალტერნატიული ნაწილი.",
    ),
    OrderItem.ACTION_TYPE_OTHER: (
        "დადასტურება საჭიროა",
        "შეკვეთის გასაგრძელებლად საჭიროა მომხმარებლის დადასტურება.",
    ),
}

PURCHASED_OR_LATER_ITEM_STATUSES = {
    OrderItem.ITEM_STATUS_PURCHASED,
    OrderItem.ITEM_STATUS_RECEIVED_USA,
    OrderItem.ITEM_STATUS_SHIPPED_TO_GEORGIA,
    OrderItem.ITEM_STATUS_RECEIVED_GEORGIA,
    OrderItem.ITEM_STATUS_READY_FOR_PICKUP,
    OrderItem.ITEM_STATUS_COMPLETED,
}


def is_customer_weight_source(weight_source):
    value = str(weight_source or "").strip().lower()
    return (
        value in {"customer", "customer_estimate", "customer_entered", "manual", "user"}
        or "customer" in value
        or "manual" in value
        or "user" in value
    )


def should_use_weight_notice_only(item):
    return (
        item.action_type == OrderItem.ACTION_TYPE_WEIGHT_CHANGE
        or True
    ) and is_customer_weight_source(item.weight_source) and item.item_status in PURCHASED_OR_LATER_ITEM_STATUSES


def build_item_action_snapshot(item):
    return {
        "part_number": item.part_number,
        "proposed_part_number": item.proposed_part_number,
        "name": item.name,
        "proposed_name": item.proposed_name,
        "item_status": item.item_status,
        "action_required": item.action_required,
        "action_type": item.action_type,
        "action_message": item.action_message,
        "final_price_gel": str(item.final_price_gel),
        "proposed_final_price_gel": (
            str(item.proposed_final_price_gel)
            if item.proposed_final_price_gel is not None
            else None
        ),
        "eta_days": item.eta_days,
        "proposed_eta_days": item.proposed_eta_days,
        "expected_arrival_date": (
            item.expected_arrival_date.isoformat()
            if item.expected_arrival_date
            else None
        ),
        "proposed_expected_arrival_date": (
            item.proposed_expected_arrival_date.isoformat()
            if item.proposed_expected_arrival_date
            else None
        ),
    }


def request_order_item_action_from_admin(item, action_type, actor_name="Admin"):
    valid_action_types = {
        OrderItem.ACTION_TYPE_PRICE_CHANGE,
        OrderItem.ACTION_TYPE_ETA_CHANGE,
        OrderItem.ACTION_TYPE_WEIGHT_CHANGE,
        OrderItem.ACTION_TYPE_FITMENT_ISSUE,
        OrderItem.ACTION_TYPE_ALTERNATIVE_REQUIRED,
        OrderItem.ACTION_TYPE_OTHER,
    }

    if action_type not in valid_action_types:
        return "invalid_action_type"

    with transaction.atomic():
        locked_item = (
            OrderItem.objects.select_for_update()
            .select_related("order")
            .get(pk=item.pk)
        )
        order = Order.objects.select_for_update().get(pk=locked_item.order_id)

        if order.status == Order.STATUS_PAYMENT_PENDING:
            return "skipped_payment_pending"

        old_value = build_item_action_snapshot(locked_item)

        if action_type in [
            OrderItem.ACTION_TYPE_PRICE_CHANGE,
            OrderItem.ACTION_TYPE_WEIGHT_CHANGE,
        ]:
            if locked_item.proposed_final_price_gel is None:
                return "missing_proposed_price"

            if locked_item.proposed_final_price_gel == locked_item.final_price_gel:
                return "no_actual_change"

        if action_type == OrderItem.ACTION_TYPE_ETA_CHANGE:
            if locked_item.proposed_eta_days is None:
                return "missing_proposed_eta"

            if locked_item.proposed_eta_days == locked_item.eta_days:
                return "no_actual_change"

            locked_item.proposed_expected_arrival_date = (
                timezone.localdate() + timedelta(days=locked_item.proposed_eta_days)
            )

        if action_type == OrderItem.ACTION_TYPE_ALTERNATIVE_REQUIRED:
            if not locked_item.proposed_part_number.strip():
                return "missing_proposed_part_number"

            if (
                locked_item.proposed_part_number.strip().upper()
                == locked_item.part_number.strip().upper()
            ):
                return "no_actual_change"

            if locked_item.proposed_eta_days is not None:
                locked_item.proposed_expected_arrival_date = (
                    timezone.localdate() + timedelta(days=locked_item.proposed_eta_days)
                )

        title, default_message = ACTION_REQUIRED_CUSTOMER_MESSAGES[action_type]
        message = locked_item.action_message.strip() or default_message

        event_type = OrderItemEvent.EVENT_TYPE_CHANGE_REQUESTED

        if action_type == OrderItem.ACTION_TYPE_PRICE_CHANGE:
            event_type = OrderItemEvent.EVENT_TYPE_PRICE_CHANGE_REQUESTED

        if action_type == OrderItem.ACTION_TYPE_ETA_CHANGE:
            event_type = OrderItemEvent.EVENT_TYPE_ETA_CHANGE_REQUESTED

        locked_item.action_required = True
        locked_item.action_type = action_type
        locked_item.action_message = message

        # Weight correction after the part is already purchased:
        # customer entered/approved estimated weight earlier, so this is notice-only.
        # We apply price immediately and customer only clicks "გასაგებია".
        if (
            action_type == OrderItem.ACTION_TYPE_WEIGHT_CHANGE
            and should_use_weight_notice_only(locked_item)
        ):
            locked_item.final_price_gel = locked_item.proposed_final_price_gel
            locked_item.proposed_final_price_gel = None
            locked_item.save()

            recalculate_order_total(order)
            sync_order_status_after_item_change(order)

            OrderItemEvent.objects.create(
                item=locked_item,
                event_type=event_type,
                title=title,
                message=message,
                old_value=old_value,
                new_value=build_item_action_snapshot(locked_item),
                actor_type=OrderItemEvent.ACTOR_TYPE_ADMIN,
                actor_name=actor_name,
                visible_to_customer=True,
            )

            return "updated"

        locked_item.item_status = OrderItem.ITEM_STATUS_ACTION_REQUIRED
        locked_item.save()

        order.status = Order.STATUS_ACTION_REQUIRED
        order.save(update_fields=["status", "updated_at"])

        OrderItemEvent.objects.create(
            item=locked_item,
            event_type=event_type,
            title=title,
            message=message,
            old_value=old_value,
            new_value=build_item_action_snapshot(locked_item),
            actor_type=OrderItemEvent.ACTOR_TYPE_ADMIN,
            actor_name=actor_name,
            visible_to_customer=True,
        )

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


class OrderSupportMessageInline(admin.TabularInline):
    model = OrderSupportMessage
    extra = 1
    readonly_fields = ("created_at",)
    actions = ["mark_selected_messages_read_by_operator"]

    @admin.action(description="მონიშნე operator-ის მიერ წაკითხულად")
    def mark_selected_messages_read_by_operator(self, request, queryset):
        updated_count = queryset.update(is_read_by_operator=True)

        self.message_user(
            request,
            f"{updated_count} support შეტყობინება მონიშნულია წაკითხულად.",
            messages.SUCCESS,
        )
    fields = (
        "item",
        "sender_type",
        "sender_name",
        "message",
        "visible_to_customer",
        "is_read_by_customer",
        "is_read_by_operator",
        "created_at",
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
        "action_required_items",
        "unread_customer_messages",
        "total_gel",
        "created_at",
    )
    list_filter = (OperatorOrderTaskFilter, "status", "created_at")
    search_fields = (
        "order_number",
        "session_id",
        "customer_name",
        "customer_phone",
        "vin",
    )
    readonly_fields = ("created_at", "updated_at")
    inlines = [PaymentInline, OrderItemInline, OrderSupportMessageInline]
    actions = ["mark_selected_orders_paid"]


    @admin.display(description="Action items")
    def action_required_items(self, obj):
        return obj.items.filter(action_required=True).count()

    @admin.display(description="Unread customer messages")
    def unread_customer_messages(self, obj):
        return obj.support_messages.filter(
            sender_type=OrderSupportMessage.SENDER_CUSTOMER,
            is_read_by_operator=False,
        ).count()

    @admin.display(description="Payment")
    def payment_status(self, obj):
        try:
            return obj.payment.status
        except Payment.DoesNotExist:
            return "missing"


    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)

        for obj in formset.deleted_objects:
            obj.delete()

        for instance in instances:
            if isinstance(instance, OrderSupportMessage):
                if not instance.sender_name:
                    instance.sender_name = request.user.get_username() or "Operator"

                if instance.sender_type == OrderSupportMessage.SENDER_OPERATOR:
                    instance.visible_to_customer = True
                    instance.is_read_by_operator = True
                    instance.is_read_by_customer = False

            instance.save()

        formset.save_m2m()

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
        "action_type",
        "proposed_part_number",
        "proposed_final_price_gel",
        "proposed_eta_days",
        "final_price_gel",
        "quantity",
        "created_at",
    )
    list_filter = (
        OperatorItemTaskFilter,
        "item_status",
        "action_required",
        "action_type",
        "order__status",
        "created_at",
    )
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
        "request_price_change_confirmation",
        "request_eta_change_confirmation",
        "request_weight_change_confirmation",
        "request_fitment_confirmation",
        "request_alternative_confirmation",
        "request_other_confirmation",
    ]

    @admin.display(description="Order status")
    def order_status(self, obj):
        return obj.order.status

    def apply_action_required_action(self, request, queryset, action_type):
        result_counts = {
            "updated": 0,
            "skipped_payment_pending": 0,
            "missing_proposed_price": 0,
            "missing_proposed_eta": 0,
            "missing_proposed_part_number": 0,
            "no_actual_change": 0,
            "invalid_action_type": 0,
        }

        for item in queryset.select_related("order"):
            result = request_order_item_action_from_admin(
                item=item,
                action_type=action_type,
                actor_name=request.user.get_username() or "Admin",
            )
            result_counts[result] = result_counts.get(result, 0) + 1

        if result_counts["updated"]:
            self.message_user(
                request,
                f"{result_counts['updated']} ნაწილზე მომხმარებლის დადასტურება მოითხოვა.",
                messages.SUCCESS,
            )

        skipped = sum(count for key, count in result_counts.items() if key != "updated")

        if skipped:
            self.message_user(
                request,
                (
                    f"{skipped} ნაწილი გამოტოვებულია. "
                    "ფასის/წონის ცვლილებაზე შეავსეთ proposed_final_price_gel, "
                    "ETA ცვლილებაზე proposed_eta_days, ალტერნატივაზე proposed_part_number, "
                    "და Payment pending შეკვეთაზე ჯერ გადახდა დაადასტურეთ."
                ),
                messages.WARNING,
            )

    @admin.action(description="მომხმარებელს: ფასის ცვლილების დადასტურება")
    def request_price_change_confirmation(self, request, queryset):
        self.apply_action_required_action(
            request,
            queryset,
            OrderItem.ACTION_TYPE_PRICE_CHANGE,
        )

    @admin.action(description="მომხმარებელს: ETA ცვლილების დადასტურება")
    def request_eta_change_confirmation(self, request, queryset):
        self.apply_action_required_action(
            request,
            queryset,
            OrderItem.ACTION_TYPE_ETA_CHANGE,
        )

    @admin.action(description="მომხმარებელს: წონის/ზომის ცვლილების დადასტურება")
    def request_weight_change_confirmation(self, request, queryset):
        self.apply_action_required_action(
            request,
            queryset,
            OrderItem.ACTION_TYPE_WEIGHT_CHANGE,
        )

    @admin.action(description="მომხმარებელს: VIN fitment დადასტურება")
    def request_fitment_confirmation(self, request, queryset):
        self.apply_action_required_action(
            request,
            queryset,
            OrderItem.ACTION_TYPE_FITMENT_ISSUE,
        )

    @admin.action(description="მომხმარებელს: ალტერნატიული ნაწილის დადასტურება")
    def request_alternative_confirmation(self, request, queryset):
        self.apply_action_required_action(
            request,
            queryset,
            OrderItem.ACTION_TYPE_ALTERNATIVE_REQUIRED,
        )

    @admin.action(description="მომხმარებელს: სხვა საკითხის დადასტურება")
    def request_other_confirmation(self, request, queryset):
        self.apply_action_required_action(
            request,
            queryset,
            OrderItem.ACTION_TYPE_OTHER,
        )

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

@admin.register(OrderSupportMessage)
class OrderSupportMessageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "item",
        "sender_type",
        "sender_name",
        "visible_to_customer",
        "is_read_by_customer",
        "is_read_by_operator",
        "created_at",
    )
    list_filter = (
        SupportMessageTaskFilter,
        "sender_type",
        "visible_to_customer",
        "is_read_by_customer",
        "is_read_by_operator",
        "created_at",
    )
    search_fields = (
        "order__order_number",
        "order__customer_name",
        "order__customer_phone",
        "item__part_number",
        "sender_name",
        "message",
    )
    readonly_fields = ("created_at",)

