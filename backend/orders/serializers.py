from rest_framework import serializers

from .models import Order, OrderItem, OrderItemEvent, Payment


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


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    payment = PaymentSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "session_id",
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
            "created_at",
            "updated_at",
        ]