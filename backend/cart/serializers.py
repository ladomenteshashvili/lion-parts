from rest_framework import serializers
from .models import Cart, CartItem


class CartItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = CartItem
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
            "final_price_gel",
            "currency",
            "note",
            "quantity",
        ]


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    total_gel = serializers.SerializerMethodField()

    class Meta:
        model = Cart
        fields = [
            "id",
            "session_id",
            "items",
            "total_gel",
            "created_at",
            "updated_at",
        ]

    def get_total_gel(self, obj):
        total = sum(item.final_price_gel * item.quantity for item in obj.items.all())
        return total