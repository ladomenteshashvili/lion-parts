from rest_framework import serializers

from .models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = [
            "id",
            "cart_item_id",
            "quote_id",
            "part_option_id",
            "part_number",
            "name",
            "condition",
            "brand",
            "availability",
            "eta_days",
            "expected_arrival_date",
            "final_price_gel",
            "proposed_final_price_gel",
            "currency",
            "note",
            "quantity",
            "proposed_eta_days",
            "proposed_expected_arrival_date",
            "item_status",
            "action_required",
            "action_type",
            "action_message",
            "created_at",
            "updated_at",
        ]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
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
            "status",
            "status_label",
            "total_gel",
            "items",
            "created_at",
            "updated_at",
        ]