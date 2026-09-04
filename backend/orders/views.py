from datetime import timedelta
from decimal import Decimal
import uuid

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from cart.models import Cart
from .models import Order, OrderItem, OrderItemEvent, Payment
from .serializers import OrderSerializer
from accounts.models import Customer



def build_customer_order_access_filter(session_id, prefix=""):
    customer = Customer.objects.filter(
        session_id=session_id,
        is_phone_verified=True,
    ).first()

    if customer and customer.phone:
        return Q(**{f"{prefix}customer_phone": customer.phone})

    return Q(pk__isnull=True)


def normalize_checkout_phone(phone):
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())

    if digits.startswith("995"):
        digits = digits[3:]

    if len(digits) != 9 or not digits.startswith("5"):
        raise ValueError("customer_phone must be a Georgian mobile number")

    return digits


def generate_order_number():
    timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
    return f"LP-{timestamp}"


def generate_payment_reference():
    timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
    random_part = uuid.uuid4().hex[:8].upper()
    return f"PAY-{timestamp}-{random_part}"


def recalculate_order_total(order):
    total_gel = sum(
        item.final_price_gel * Decimal(item.quantity)
        for item in order.items.all()
    )

    order.total_gel = total_gel
    order.save(update_fields=["total_gel", "updated_at"])
    return order


def create_order_item_event(
    item,
    event_type,
    title,
    message="",
    old_value=None,
    new_value=None,
    actor_type=OrderItemEvent.ACTOR_TYPE_SYSTEM,
    actor_name="",
):
    return OrderItemEvent.objects.create(
        item=item,
        event_type=event_type,
        title=title,
        message=message,
        old_value=old_value,
        new_value=new_value,
        actor_type=actor_type,
        actor_name=actor_name,
        visible_to_customer=False,
    )


def get_or_create_order_payment(order):
    payment, _created = Payment.objects.select_for_update().get_or_create(
        order=order,
        defaults={
            "payment_reference": generate_payment_reference(),
            "provider": Payment.PROVIDER_DEMO,
            "status": Payment.STATUS_PENDING,
            "amount_gel": order.total_gel,
            "currency": "GEL",
        },
    )
    return payment


def confirm_order_payment(order, payment, source="demo_verification"):
    old_order_status = order.status
    old_payment_status = payment.status

    payment.status = Payment.STATUS_PAID
    payment.paid_at = payment.paid_at or timezone.now()
    payment.provider_payload = {
        "source": source,
        "payment_reference": payment.payment_reference,
    }
    payment.save(
        update_fields=[
            "status",
            "paid_at",
            "provider_payload",
            "updated_at",
        ]
    )

    order.status = Order.STATUS_PROCESSING
    order.save(update_fields=["status", "updated_at"])

    for item in order.items.select_for_update().all():
        old_item_status = item.item_status

        item.item_status = OrderItem.ITEM_STATUS_PAYMENT_CONFIRMED
        item.action_required = False
        item.action_type = OrderItem.ACTION_TYPE_NONE
        item.action_message = ""
        item.save(
            update_fields=[
                "item_status",
                "action_required",
                "action_type",
                "action_message",
                "updated_at",
            ]
        )

        create_order_item_event(
            item=item,
            event_type=OrderItemEvent.EVENT_TYPE_STATUS_CHANGED,
            title="გადახდა დადასტურებულია",
            message="შეკვეთის გადახდა დადასტურდა.",
            old_value={
                "order_status": old_order_status,
                "item_status": old_item_status,
                "payment_status": old_payment_status,
            },
            new_value={
                "order_status": order.status,
                "item_status": item.item_status,
                "payment_status": payment.status,
                "payment_reference": payment.payment_reference,
            },
            actor_type=OrderItemEvent.ACTOR_TYPE_SYSTEM,
            actor_name="System",
        )


@api_view(["GET"])
def list_orders(request):
    session_id = request.query_params.get("session_id", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    orders = (
        Order.objects.filter(build_customer_order_access_filter(session_id))
        .select_related("payment")
        .prefetch_related("items__events")
    )
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
        Order.objects.select_related("payment").prefetch_related("items__events"),
        build_customer_order_access_filter(session_id),
        order_number=order_number,
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
        normalized_customer_phone = normalize_checkout_phone(customer_phone)
    except ValueError:
        return Response(
            {
                "detail": (
                    "ტელეფონის ნომერი უნდა იყოს ქართული მობილური ნომერი, "
                    "მაგ: 555123456 ან +995555123456"
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer = Customer.objects.filter(session_id=session_id).first()

    if not customer or not customer.is_phone_verified:
        return Response(
            {"detail": "phone verification required"},
            status=status.HTTP_403_FORBIDDEN,
        )

    if customer.phone != normalized_customer_phone:
        return Response(
            {"detail": "checkout phone must match verified phone"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer_name = customer.name
    customer_phone = customer.phone        

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

        Payment.objects.create(
            order=order,
            payment_reference=generate_payment_reference(),
            provider=Payment.PROVIDER_DEMO,
            status=Payment.STATUS_PENDING,
            amount_gel=total_gel,
            currency="GEL",
        )

        for item in cart_items:
            order_item = OrderItem.objects.create(
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
                expected_arrival_date=(
                    timezone.localdate() + timedelta(days=item.eta_days)
                    if item.eta_days is not None
                    else None
                ),
                weight_kg=item.weight_kg,
                final_price_gel=item.final_price_gel,
                currency=item.currency,
                note=item.note,
                customer_notice=item.customer_notice,
                weight_source=item.weight_source,
                quantity=item.quantity,
                item_status=OrderItem.ITEM_STATUS_CREATED,
                action_required=False,
                action_type=OrderItem.ACTION_TYPE_NONE,
                action_message="",
            )

            create_order_item_event(
                item=order_item,
                event_type=OrderItemEvent.EVENT_TYPE_CREATED,
                title="ნაწილი შეკვეთაში დაემატა",
                message="ნაწილი დაფიქსირდა შეკვეთაში.",
                new_value={
                    "item_status": order_item.item_status,
                    "final_price_gel": str(order_item.final_price_gel),
                    "eta_days": order_item.eta_days,
                    "expected_arrival_date": (
                        order_item.expected_arrival_date.isoformat()
                        if order_item.expected_arrival_date
                        else None
                    ),
                    "weight_kg": (
                        str(order_item.weight_kg)
                        if order_item.weight_kg is not None
                        else None
                    ),
                    "weight_source": order_item.weight_source,
                    "customer_notice": order_item.customer_notice,
                },
            )

        cart.items.all().delete()

    updated_order = (
        Order.objects.select_related("payment")
        .prefetch_related("items__events")
        .get(id=order.id)
    )

    serializer = OrderSerializer(updated_order)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
def verify_payment(request, order_number):
    session_id = request.data.get("session_id", "").strip()
    payment_reference = request.data.get("payment_reference", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(
                order_number=order_number,
                session_id=session_id,
            )
            payment = Payment.objects.select_for_update().get(order=order)

            if payment_reference and payment_reference != payment.payment_reference:
                return Response(
                    {"detail": "payment_reference does not match this order"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if payment.status == Payment.STATUS_PAID:
                updated_order = (
                    Order.objects.select_related("payment")
                    .prefetch_related("items__events")
                    .get(id=order.id)
                )
                serializer = OrderSerializer(updated_order)
                return Response(serializer.data, status=status.HTTP_200_OK)

            if order.status != Order.STATUS_PAYMENT_PENDING:
                return Response(
                    {"detail": "order is not payment pending"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            confirm_order_payment(
                order=order,
                payment=payment,
                source="demo_verification",
            )

    except Order.DoesNotExist:
        return Response(
            {"detail": "order not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    except Payment.DoesNotExist:
        return Response(
            {"detail": "payment not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    updated_order = (
        Order.objects.select_related("payment")
        .prefetch_related("items__events")
        .get(
            order_number=order_number,
            session_id=session_id,
        )
    )

    serializer = OrderSerializer(updated_order)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
def demo_confirm_payment(request, order_number):
    session_id = request.data.get("session_id", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(
                order_number=order_number,
                session_id=session_id,
            )

            payment = get_or_create_order_payment(order)

            if payment.status == Payment.STATUS_PAID:
                updated_order = (
                    Order.objects.select_related("payment")
                    .prefetch_related("items__events")
                    .get(id=order.id)
                )
                serializer = OrderSerializer(updated_order)
                return Response(serializer.data, status=status.HTTP_200_OK)

            if order.status != Order.STATUS_PAYMENT_PENDING:
                return Response(
                    {"detail": "order is not payment pending"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            confirm_order_payment(
                order=order,
                payment=payment,
                source="legacy_demo_confirm_payment",
            )

    except Order.DoesNotExist:
        return Response(
            {"detail": "order not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    updated_order = (
        Order.objects.select_related("payment")
        .prefetch_related("items__events")
        .get(
            order_number=order_number,
            session_id=session_id,
        )
    )

    serializer = OrderSerializer(updated_order)
    return Response(serializer.data, status=status.HTTP_200_OK)


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
            build_customer_order_access_filter(session_id, prefix="order__"),
            id=item_id,
        )
    except OrderItem.DoesNotExist:
        return Response(
            {"detail": "order item not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    order = item.order

    old_value = {
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

    if item.proposed_final_price_gel is not None:
        item.final_price_gel = item.proposed_final_price_gel

    if item.proposed_eta_days is not None:
        item.eta_days = item.proposed_eta_days

    if item.proposed_expected_arrival_date is not None:
        item.expected_arrival_date = item.proposed_expected_arrival_date

    item.proposed_final_price_gel = None
    item.proposed_eta_days = None
    item.proposed_expected_arrival_date = None

    item.action_required = False
    item.action_type = OrderItem.ACTION_TYPE_NONE
    item.action_message = ""
    item.item_status = OrderItem.ITEM_STATUS_CHECKING
    item.save()

    recalculate_order_total(order)

    create_order_item_event(
        item=item,
        event_type=OrderItemEvent.EVENT_TYPE_ACTION_RESOLVED,
        title="მომხმარებლის მოქმედება დადასტურდა",
        message="ცვლილება დადასტურდა და პროცესი გაგრძელდა.",
        old_value=old_value,
        new_value={
            "item_status": item.item_status,
            "action_required": item.action_required,
            "action_type": item.action_type,
            "action_message": item.action_message,
            "final_price_gel": str(item.final_price_gel),
            "eta_days": item.eta_days,
            "expected_arrival_date": (
                item.expected_arrival_date.isoformat()
                if item.expected_arrival_date
                else None
            ),
        },
        actor_type=OrderItemEvent.ACTOR_TYPE_CUSTOMER,
        actor_name="Customer",
    )

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

    old_value = {
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

    has_actual_change = False

    if proposed_final_price_gel not in [None, ""]:
        try:
            parsed_price = Decimal(str(proposed_final_price_gel))
        except Exception:
            return Response(
                {"detail": "invalid proposed_final_price_gel"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if parsed_price != item.final_price_gel:
            item.proposed_final_price_gel = parsed_price
            has_actual_change = True

    if proposed_eta_days not in [None, ""]:
        try:
            parsed_eta_days = int(proposed_eta_days)
        except Exception:
            return Response(
                {"detail": "invalid proposed_eta_days"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if parsed_eta_days != item.eta_days:
            item.proposed_eta_days = parsed_eta_days
            item.proposed_expected_arrival_date = (
                timezone.localdate() + timedelta(days=parsed_eta_days)
            )
            has_actual_change = True

    if not has_actual_change:
        return Response(
            {"detail": "no actual changes detected"},
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

    event_type = OrderItemEvent.EVENT_TYPE_CHANGE_REQUESTED

    if action_type == OrderItem.ACTION_TYPE_PRICE_CHANGE:
        event_type = OrderItemEvent.EVENT_TYPE_PRICE_CHANGE_REQUESTED

    if action_type == OrderItem.ACTION_TYPE_ETA_CHANGE:
        event_type = OrderItemEvent.EVENT_TYPE_ETA_CHANGE_REQUESTED

    create_order_item_event(
        item=item,
        event_type=event_type,
        title="მომხმარებლის მოქმედება მოთხოვნილია",
        message=action_message,
        old_value=old_value,
        new_value={
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
        },
        actor_type=OrderItemEvent.ACTOR_TYPE_SYSTEM,
        actor_name="System",
    )

    serializer = OrderSerializer(order)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
def demo_update_item_status(request, item_id):
    session_id = request.data.get("session_id", "").strip()
    new_status = request.data.get("item_status", "").strip()
    message = request.data.get("message", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if new_status not in dict(OrderItem.ITEM_STATUS_CHOICES):
        return Response(
            {"detail": "invalid item_status"},
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

    old_status = item.item_status
    order = item.order

    if old_status == new_status:
        return Response(
            {"detail": "status is already set"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    old_value = {
        "item_status": old_status,
    }

    item.item_status = new_status

    if new_status != OrderItem.ITEM_STATUS_ACTION_REQUIRED:
        item.action_required = False
        item.action_type = OrderItem.ACTION_TYPE_NONE
        item.action_message = ""

    item.save()

    create_order_item_event(
        item=item,
        event_type=OrderItemEvent.EVENT_TYPE_STATUS_CHANGED,
        title="ნაწილის სტატუსი შეიცვალა",
        message=message or f"სტატუსი შეიცვალა: {old_status} → {new_status}",
        old_value=old_value,
        new_value={
            "item_status": item.item_status,
        },
        actor_type=OrderItemEvent.ACTOR_TYPE_SYSTEM,
        actor_name="System",
    )

    if new_status == OrderItem.ITEM_STATUS_COMPLETED:
        all_items_completed = not order.items.exclude(
            item_status=OrderItem.ITEM_STATUS_COMPLETED
        ).exists()

        if all_items_completed:
            order.status = Order.STATUS_COMPLETED
            order.save(update_fields=["status", "updated_at"])

    elif order.status not in [
        Order.STATUS_PAYMENT_PENDING,
        Order.STATUS_ACTION_REQUIRED,
        Order.STATUS_CANCELLED,
    ]:
        order.status = Order.STATUS_PROCESSING
        order.save(update_fields=["status", "updated_at"])

    serializer = OrderSerializer(order)
    return Response(serializer.data, status=status.HTTP_200_OK)