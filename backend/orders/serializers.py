from rest_framework import serializers

from .models import Order, OrderCustomerNotification, OrderItem, OrderItemEvent, OrderSupportMessage, Payment


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "id",
            "payment_reference",
            "external_payment_id",
            "provider",
            "status",
            "amount_gel",
            "currency",
            "paid_at",
            "created_at",
            "updated_at",
        ]


class OrderItemEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItemEvent
        fields = [
            "id",
            "event_type",
            "title",
            "message",
            "old_value",
            "new_value",
            "actor_type",
            "actor_name",
            "visible_to_customer",
            "created_at",
        ]


class OrderItemSerializer(serializers.ModelSerializer):
    events = serializers.SerializerMethodField()

    def get_events(self, obj):
        events = obj.events.filter(visible_to_customer=True).order_by(
            "created_at",
            "id",
        )
        return OrderItemEventSerializer(events, many=True).data

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "cart_item_id",
            "quote_id",
            "part_option_id",
            "part_number",
            "proposed_part_number",
            "proposed_name",
            "name",
            "condition",
            "brand",
            "availability",
            "eta_days",
            "expected_arrival_date",
            "weight_kg",
            "final_price_gel",
            "proposed_final_price_gel",
            "currency",
            "note",
            "customer_notice",
            "weight_source",
            "quantity",
            "proposed_eta_days",
            "proposed_expected_arrival_date",
            "item_status",
            "action_required",
            "action_type",
            "action_message",
            "events",
            "created_at",
            "updated_at",
        ]


class OrderSupportMessageSerializer(serializers.ModelSerializer):
    item_id = serializers.IntegerField(source="item.id", read_only=True)
    item_part_number = serializers.CharField(source="item.part_number", read_only=True)

    class Meta:
        model = OrderSupportMessage
        fields = [
            "id",
            "item_id",
            "item_part_number",
            "sender_type",
            "sender_name",
            "message",
            "visible_to_customer",
            "is_read_by_customer",
            "is_read_by_operator",
            "created_at",
        ]


class OrderSerializer(serializers.ModelSerializer):
    current_customer_name = serializers.CharField(read_only=True)
    current_customer_phone = serializers.CharField(read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    payment = PaymentSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    support_messages = serializers.SerializerMethodField()
    support_unread_count = serializers.SerializerMethodField()

    def get_support_messages(self, obj):
        messages = obj.support_messages.filter(visible_to_customer=True).order_by(
            "created_at",
            "id",
        )
        return OrderSupportMessageSerializer(messages, many=True).data

    def get_support_unread_count(self, obj):
        return obj.support_messages.filter(
            visible_to_customer=True,
            is_read_by_customer=False,
        ).exclude(
            sender_type=OrderSupportMessage.SENDER_CUSTOMER,
        ).count()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "session_id",
            "current_customer_name",
            "current_customer_phone",
            "customer_name",
            "customer_phone",
            "vin",
            "note",
            "payment_type",
            "payment",
            "status",
            "status_label",
            "total_gel",
            "items",
            "support_messages",
            "support_unread_count",
            "created_at",
            "updated_at",
        ]

class PublicOrderSerializer(serializers.ModelSerializer):
    current_customer_name = serializers.CharField(read_only=True)
    current_customer_phone = serializers.CharField(read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    payment = PaymentSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    support_messages = serializers.SerializerMethodField()
    support_unread_count = serializers.SerializerMethodField()

    def get_support_messages(self, obj):
        messages = obj.support_messages.filter(visible_to_customer=True).order_by(
            "created_at",
            "id",
        )
        return OrderSupportMessageSerializer(messages, many=True).data

    def get_support_unread_count(self, obj):
        return obj.support_messages.filter(
            visible_to_customer=True,
            is_read_by_customer=False,
        ).exclude(
            sender_type=OrderSupportMessage.SENDER_CUSTOMER,
        ).count()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "current_customer_name",
            "current_customer_phone",
            "customer_name",
            "customer_phone",
            "vin",
            "note",
            "payment_type",
            "payment",
            "status",
            "status_label",
            "total_gel",
            "items",
            "support_messages",
            "support_unread_count",
            "created_at",
            "updated_at",
        ]


class OrderCustomerNotificationPublicSerializer(serializers.ModelSerializer):
    order = PublicOrderSerializer(read_only=True)
    item_id = serializers.IntegerField(source="item.id", read_only=True)
    item_part_number = serializers.CharField(source="item.part_number", read_only=True)
    support_message_id = serializers.IntegerField(source="support_message.id", read_only=True)
    event_id = serializers.IntegerField(source="event.id", read_only=True)

    class Meta:
        model = OrderCustomerNotification
        fields = [
            "id",
            "token",
            "notification_type",
            "title",
            "message",
            "item_id",
            "item_part_number",
            "support_message_id",
            "event_id",
            "is_read_by_customer",
            "acknowledged_at",
            "created_at",
            "order",
        ]

