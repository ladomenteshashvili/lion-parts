from django.shortcuts import render

# Create your views here.
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from .models import Cart, CartItem
from .serializers import CartSerializer


def get_or_create_cart(session_id: str):
    cart, _created = Cart.objects.get_or_create(session_id=session_id)
    return cart


@api_view(["GET"])
def get_cart(request):
    session_id = request.query_params.get("session_id", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    cart = get_or_create_cart(session_id)
    serializer = CartSerializer(cart)
    return Response(serializer.data)


@api_view(["POST"])
def add_cart_item(request):
    session_id = request.data.get("session_id", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    required_fields = [
        "cart_item_id",
        "quote_id",
        "part_option_id",
        "part_number",
        "name",
        "final_price_gel",
    ]

    for field in required_fields:
        if request.data.get(field) in [None, ""]:
            return Response(
                {"detail": f"{field} is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    cart = get_or_create_cart(session_id)

    cart_item_id = request.data["cart_item_id"]

    item, created = CartItem.objects.get_or_create(
        cart=cart,
        cart_item_id=cart_item_id,
        defaults={
            "quote_id": request.data["quote_id"],
            "part_option_id": request.data["part_option_id"],
            "part_number": request.data["part_number"],
            "name": request.data["name"],
            "condition": request.data.get("condition", ""),
            "brand": request.data.get("brand", ""),
            "availability": request.data.get("availability", ""),
            "eta_days": request.data.get("eta_days"),
            "final_price_gel": request.data["final_price_gel"],
            "currency": request.data.get("currency", "GEL"),
            "note": request.data.get("note", ""),
            "quantity": request.data.get("quantity", 1),
        },
    )

    if not created:
        item.quantity += int(request.data.get("quantity", 1))
        item.save(update_fields=["quantity", "updated_at"])

    serializer = CartSerializer(cart)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
def remove_cart_item(request, cart_item_id):
    session_id = request.query_params.get("session_id", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    cart = get_object_or_404(Cart, session_id=session_id)
    item = get_object_or_404(CartItem, cart=cart, cart_item_id=cart_item_id)
    item.delete()

    serializer = CartSerializer(cart)
    return Response(serializer.data)