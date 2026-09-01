from django.shortcuts import render

# Create your views here.
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from cart.models import Cart
from .models import Order, OrderItem
from .serializers import OrderSerializer
from django.shortcuts import get_object_or_404


def generate_order_number():
    timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
    return f"LP-{timestamp}"


@api_view(["GET"])
def list_orders(request):
    session_id = request.query_params.get("session_id", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    orders = Order.objects.filter(session_id=session_id).prefetch_related("items")
    serializer = OrderSerializer(orders, many=True)
    return Response(serializer.data)


@api_view(["GET"])
def get_order_detail(request, order_number):
    session_id = request.query_params.get("session_id", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    order = get_object_or_404(
        Order.objects.prefetch_related("items"),
        order_number=order_number,
        session_id=session_id,
    )

    serializer = OrderSerializer(order)
    return Response(serializer.data)
    

@api_view(["POST"])
def checkout(request):
    session_id = request.data.get("session_id", "").strip()
    customer_name = request.data.get("customer_name", "").strip()
    customer_phone = request.data.get("customer_phone", "").strip()
    vin = request.data.get("vin", "").strip()
    note = request.data.get("note", "").strip()

    if not session_id:
        return Response({"detail": "session_id is required"}, status=status.HTTP_400_BAD_REQUEST)

    if not customer_name:
        return Response({"detail": "customer_name is required"}, status=status.HTTP_400_BAD_REQUEST)

    if not customer_phone:
        return Response({"detail": "customer_phone is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        cart = Cart.objects.prefetch_related("items").get(session_id=session_id)
    except Cart.DoesNotExist:
        return Response({"detail": "cart not found"}, status=status.HTTP_404_NOT_FOUND)

    cart_items = list(cart.items.all())

    if not cart_items:
        return Response({"detail": "cart is empty"}, status=status.HTTP_400_BAD_REQUEST)

    total_gel = sum(
        item.final_price_gel * Decimal(item.quantity)
        for item in cart_items
    )

    with transaction.atomic():
        order = Order.objects.create(
            order_number=generate_order_number(),
            session_id=session_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
            vin=vin,
            note=note,
            payment_type=Order.PAYMENT_FULL,
            status=Order.STATUS_PAYMENT_PENDING,
            total_gel=total_gel,
        )

        for item in cart_items:
            OrderItem.objects.create(
                order=order,
                cart_item_id=item.cart_item_id,
                quote_id=item.quote_id,
                part_option_id=item.part_option_id,
                part_number=item.part_number,
                name=item.name,
                condition=item.condition,
                brand=item.brand,
                availability=item.availability,
                eta_days=item.eta_days,
                final_price_gel=item.final_price_gel,
                currency=item.currency,
                note=item.note,
                quantity=item.quantity,
            )

        cart.items.all().delete()

    serializer = OrderSerializer(order)
    return Response(serializer.data, status=status.HTTP_201_CREATED)