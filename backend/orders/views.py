from decimal import Decimal

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from cart.models import Cart
from .models import Order, OrderItem
from .serializers import OrderSerializer


def generate_order_number():
    timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
    return f"LP-{timestamp}"


def recalculate_order_total(order):
    total_gel = sum(
        item.final_price_gel * Decimal(item.quantity)
        for item in order.items.all()
    )

    order.total_gel = total_gel
    order.save(update_fields=["total_gel", "updated_at"])
    return order


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
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not customer_name:
        return Response(
            {"detail": "customer_name is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not customer_phone:
        return Response(
            {"detail": "customer_phone is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        cart = Cart.objects.prefetch_related("items").get(session_id=session_id)
    except Cart.DoesNotExist:
        return Response(
            {"detail": "cart not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    cart_items = list(cart.items.all())

    if not cart_items:
        return Response(
            {"detail": "cart is empty"},
            status=status.HTTP_400_BAD_REQUEST,
        )

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
                item_status=OrderItem.ITEM_STATUS_CREATED,
                action_required=False,
                action_type=OrderItem.ACTION_TYPE_NONE,
                action_message="",
            )

        cart.items.all().delete()

    serializer = OrderSerializer(order)
    return Response(serializer.data, status=status.HTTP_201_CREATED)

@api_view(["POST"])
def demo_resolve_item_action(request, item_id):
    session_id = request.data.get("session_id", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        item = OrderItem.objects.select_related("order").get(
            id=item_id,
            order__session_id=session_id,
        )
    except OrderItem.DoesNotExist:
        return Response(
            {"detail": "order item not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    order = item.order

    if item.proposed_final_price_gel is not None:
        item.final_price_gel = item.proposed_final_price_gel

    if item.proposed_eta_days is not None:
        item.eta_days = item.proposed_eta_days

    item.proposed_final_price_gel = None
    item.proposed_eta_days = None

    item.action_required = False
    item.action_type = OrderItem.ACTION_TYPE_NONE
    item.action_message = ""
    item.item_status = OrderItem.ITEM_STATUS_CHECKING
    item.save()

    recalculate_order_total(order)

    has_other_action_items = order.items.filter(action_required=True).exists()

    if not has_other_action_items and order.status == Order.STATUS_ACTION_REQUIRED:
        order.status = Order.STATUS_PROCESSING
        order.save(update_fields=["status", "updated_at"])

    serializer = OrderSerializer(order)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
def demo_request_item_change(request, item_id):
    session_id = request.data.get("session_id", "").strip()
    action_type = request.data.get("action_type", "").strip()
    action_message = request.data.get("action_message", "").strip()
    proposed_final_price_gel = request.data.get("proposed_final_price_gel")
    proposed_eta_days = request.data.get("proposed_eta_days")

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        item = OrderItem.objects.select_related("order").get(
            id=item_id,
            order__session_id=session_id,
        )
    except OrderItem.DoesNotExist:
        return Response(
            {"detail": "order item not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if action_type not in [
        OrderItem.ACTION_TYPE_PRICE_CHANGE,
        OrderItem.ACTION_TYPE_ETA_CHANGE,
        OrderItem.ACTION_TYPE_WEIGHT_CHANGE,
        OrderItem.ACTION_TYPE_FITMENT_ISSUE,
        OrderItem.ACTION_TYPE_ALTERNATIVE_REQUIRED,
        OrderItem.ACTION_TYPE_OTHER,
    ]:
        return Response(
            {"detail": "invalid action_type"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if proposed_final_price_gel not in [None, ""]:
        try:
            item.proposed_final_price_gel = Decimal(str(proposed_final_price_gel))
        except Exception:
            return Response(
                {"detail": "invalid proposed_final_price_gel"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    if proposed_eta_days not in [None, ""]:
        try:
            item.proposed_eta_days = int(proposed_eta_days)
        except Exception:
            return Response(
                {"detail": "invalid proposed_eta_days"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    item.action_required = True
    item.action_type = action_type
    item.action_message = action_message
    item.item_status = OrderItem.ITEM_STATUS_ACTION_REQUIRED
    item.save()

    order = item.order
    order.status = Order.STATUS_ACTION_REQUIRED
    order.save(update_fields=["status", "updated_at"])

    serializer = OrderSerializer(order)
    return Response(serializer.data, status=status.HTTP_200_OK)