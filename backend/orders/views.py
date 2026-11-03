"""Checkout and order endpoints (G§8.6, G§8.7). Thin views: logic in orders.checkout / orders.services."""

import uuid
from datetime import timedelta

from django.db.models import Case, Count, Exists, IntegerField, OuterRef, Q, When
from django.http import HttpResponse
from django.utils import timezone
from django.utils.translation import get_language
from rest_framework import serializers
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from api.errors import MESSAGES, ApiError
from api.pagination import Pagination
from api.permissions import IsFullyVerified
from api.throttles import PublicLinkThrottle, SupportCustomerThrottle, SupportGuestThrottle

from payments.models import Payment
from payments.services import request_payment, start_payment

from . import checkout, services
from .invoices import invoice_language, render_invoice
from .models import Notification, Order, OrderItem, OrderSupportMessage, SupportTicket
from .support import TicketSerializer, create_ticket, ticket_data
from .notifications import PUBLIC_LINK_DAYS, notification_data, public_notification_data
from .serializers import order_data, order_queryset, orders_data, payment_data


def own_order(request, order_number, queryset=None):
    """The customer's own order, else 404 (never reveal that it exists)."""
    queryset = Order.objects if queryset is None else queryset
    order = queryset.filter(order_number=order_number, customer=request.user.customer).first()
    if order is None:
        raise ApiError("not_found", status=404)
    return order


@api_view(["GET"])
@permission_classes([IsFullyVerified])
def checkout_options(request):
    return Response(checkout.checkout_options(request, request.user.customer))


@api_view(["GET"])
def invoice(request, order_number):
    order = own_order(request, order_number)
    language = invoice_language(request.query_params.get("lang"), get_language())
    response = HttpResponse(render_invoice(order, language), content_type="text/html; charset=utf-8")
    response["X-Robots-Tag"] = "noindex"
    response["Content-Language"] = language
    return response


@api_view(["POST"])
@permission_classes([IsFullyVerified])
def checkout_create(request):
    customer = request.user.customer
    serializer = checkout.CheckoutSerializer(data=request.data, context={"customer": customer})
    serializer.is_valid(raise_exception=True)
    order, payment = checkout.create_order(customer, serializer.validated_data)
    body = {}
    if payment.provider != "invoice" and not start_payment(payment):  # after commit, no locks held
        body = {"code": "payment_init_failed", "detail": str(MESSAGES["payment_init_failed"])}
    payment.refresh_from_db()
    order.refresh_from_db()
    return Response({"order": order_data(request, order), "payment": payment_data(payment), **body}, status=201)


@api_view(["POST"])
@permission_classes([IsFullyVerified])
def order_payments(request, order_number):
    order = own_order(request, order_number)
    method = request.data.get("method") if isinstance(request.data, dict) else None
    if method not in ("card", "invoice"):
        raise serializers.ValidationError({"method": [serializers.ErrorDetail(
            str(serializers.ChoiceField.default_error_messages["invalid_choice"]).format(input=method), "invalid")]})
    payment = request_payment(order.pk, method)
    if payment.provider != "invoice":
        start_payment(payment)  # after commit; a failure shows as status "failed"
    payment.refresh_from_db()
    return Response(payment_data(payment), status=201)


@api_view(["GET"])
def order_payment(request, order_number, payment_id):
    order = own_order(request, order_number)
    payment = Payment.objects.filter(pk=payment_id, order=order).first()
    if payment is None:
        raise ApiError("not_found", status=404)
    return Response(payment_data(payment))


def _groups():
    """G§7.8 order groups -> filter (over a queryset annotated with `pickup`)."""
    return {
        "all": Q(), "action": Q(status=Order.STATUS_ACTION_REQUIRED),
        "to_pay": Q(balance_due__gt=0) & ~Q(status=Order.STATUS_CANCELLED), "to_pickup": Q(pickup=True),
        "active": Q(status__in=Order.OPEN_STATUSES), "completed": Q(status=Order.STATUS_COMPLETED),
        "cancelled": Q(status=Order.STATUS_CANCELLED),
    }


@api_view(["GET"])
def order_list(request):
    """G§8.7: ?group= filter, action first, then to_pay, then newest; group_counts in one query."""
    groups = _groups()
    group = request.query_params.get("group", "all")
    if group not in groups:
        raise serializers.ValidationError({"group": [serializers.ErrorDetail(
            str(serializers.ChoiceField.default_error_messages["invalid_choice"]).format(input=group), "invalid")]})
    mine = Order.objects.filter(customer=request.user.customer).annotate(pickup=Exists(OrderItem.objects.filter(
        order=OuterRef("pk"), item_status=OrderItem.STATUS_READY_FOR_PICKUP)))
    counts = mine.aggregate(**{name: Count("pk", filter=q) for name, q in groups.items()})
    orders = order_queryset().filter(pk__in=mine.filter(groups[group]).values("pk")).annotate(priority=Case(
        When(status=Order.STATUS_ACTION_REQUIRED, then=0), When(groups["to_pay"], then=1), default=2,
        output_field=IntegerField())).order_by("priority", "-created_at", "-id")
    paginator = Pagination()
    page = paginator.paginate_queryset(orders, request)
    return paginator.get_paginated_response(orders_data(request, page), group_counts=counts)


@api_view(["GET"])
def order_detail(request, order_number):
    return Response(order_data(request, own_order(request, order_number, order_queryset(detail=True)), detail=True))


@api_view(["POST"])
def item_decision(request, item_id):
    decision = request.data.get("decision") if isinstance(request.data, dict) else None
    if decision not in ("accept", "cancel", "acknowledge"):
        raise serializers.ValidationError({"decision": [serializers.ErrorDetail(
            str(serializers.ChoiceField.default_error_messages["invalid_choice"]).format(input=decision), "invalid")]})
    customer = request.user.customer
    order = services.decide(customer, item_id, decision, actor_name=f"{customer.first_name} {customer.last_name}")
    return _order_response(request, order)


def _order_response(request, order, status=200):
    return Response(order_data(request, order_queryset(detail=True).get(pk=order.pk), detail=True), status=status)


@api_view(["POST"])
def order_cancel(request, order_number):
    order = own_order(request, order_number)
    reason = request.data.get("reason", "") if isinstance(request.data, dict) else ""
    if not isinstance(reason, str) or len(reason) > 1000:
        raise serializers.ValidationError({"reason": [serializers.ErrorDetail(
            str(serializers.CharField.default_error_messages["max_length"]).format(max_length=1000), "invalid")]})
    customer = request.user.customer
    order = services.cancel_order(order.pk, reason.strip(), actor="customer",
                                  actor_name=f"{customer.first_name} {customer.last_name}")
    return _order_response(request, order)


@api_view(["POST"])
def courier_fee_accept(request, order_number):
    return _order_response(request, services.respond_courier_fee(own_order(request, order_number).pk, accept=True))


@api_view(["POST"])
def courier_fee_decline(request, order_number):
    return _order_response(request, services.respond_courier_fee(own_order(request, order_number).pk, accept=False))


class SupportMessageSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=2000)
    item_id = serializers.IntegerField(required=False, allow_null=True)


@api_view(["POST"])
@throttle_classes([SupportCustomerThrottle])
def support_message(request, order_number):
    """G§7.16: a customer message on their order, optionally about one of its items. -> 201 Order."""
    order = own_order(request, order_number)
    data = SupportMessageSerializer(data=request.data)
    data.is_valid(raise_exception=True)
    item = None
    if data.validated_data.get("item_id") is not None:
        item = order.items.filter(pk=data.validated_data["item_id"]).first()
        if item is None:
            raise serializers.ValidationError({"item_id": [serializers.ErrorDetail(
                str(serializers.PrimaryKeyRelatedField.default_error_messages["does_not_exist"]).format(
                    pk_value=data.validated_data["item_id"]), "invalid")]})
    customer = request.user.customer
    OrderSupportMessage.objects.create(order=order, item=item, sender_type=OrderSupportMessage.SENDER_CUSTOMER,
                                       sender_name=f"{customer.first_name} {customer.last_name}",
                                       message=data.validated_data["message"], is_read_by_customer=True)
    return _order_response(request, order, status=201)


@api_view(["POST"])
def support_acknowledge(request, order_number):
    order = own_order(request, order_number)
    order.support_messages.filter(is_read_by_customer=False).exclude(
        sender_type=OrderSupportMessage.SENDER_CUSTOMER).update(is_read_by_customer=True)
    return _order_response(request, order)


# Notifications (G§8.9, LP-BE-904)

@api_view(["GET"])
def notification_list(request):
    mine = Notification.objects.filter(customer=request.user.customer).select_related("order", "item")
    unread = mine.filter(read_at__isnull=True)
    shown = unread if request.query_params.get("unread") in ("1", "true") else mine
    paginator = Pagination()
    page = paginator.paginate_queryset(shown, request)
    return paginator.get_paginated_response([notification_data(n) for n in page], unread_count=unread.count())


@api_view(["POST"])
def notification_read(request, notification_id):
    """-> 204. A support reply also marks its order message read by the customer (support_unread_count)."""
    n = Notification.objects.filter(pk=notification_id, customer=request.user.customer).first()
    if n is None:
        raise ApiError("not_found", status=404)
    if n.read_at is None:
        Notification.objects.filter(pk=n.pk, read_at__isnull=True).update(read_at=timezone.now())
    if n.event_type == "support_reply" and n.support_message_id:
        OrderSupportMessage.objects.filter(pk=n.support_message_id, order__customer=request.user.customer).update(
            is_read_by_customer=True)
    return Response(status=204)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([PublicLinkThrottle])
def public_notification(request, token):
    """D23: unknown, malformed, guest and expired (> 30 days) tokens are the same 404."""
    try:
        token = uuid.UUID(token)
    except ValueError:
        raise ApiError("not_found", status=404) from None
    n = Notification.objects.filter(token=token, customer__isnull=False, created_at__gte=timezone.now() - timedelta(
        days=PUBLIC_LINK_DAYS)).select_related("order", "item").first()
    if n is None:
        raise ApiError("not_found", status=404)
    return Response(public_notification_data(n))


# Support tickets (G§8.9, LP-BE-1001)

@api_view(["GET", "POST"])
@permission_classes([AllowAny])
@throttle_classes([SupportGuestThrottle, SupportCustomerThrottle])
def support_tickets(request):
    """POST: guests (contact required, throttled per IP) and customers. GET: the customer's own tickets."""
    from accounts.services import can_log_in, ensure_session

    customer = request.user.customer if can_log_in(request.user) else None
    if request.method == "GET":
        if customer is None:  # staff and other non-customers: 403 like every customer endpoint
            raise NotAuthenticated() if not request.user.is_authenticated else PermissionDenied()
        paginator = Pagination()
        page = paginator.paginate_queryset(SupportTicket.objects.filter(customer=customer), request)
        return paginator.get_paginated_response([ticket_data(t) for t in page])
    serializer = TicketSerializer(data=request.data, context={"customer": customer})
    serializer.is_valid(raise_exception=True)
    ticket = create_ticket(serializer.validated_data, customer=customer,
                           session_key=None if customer else ensure_session(request))
    return Response({"id": ticket.pk, "context": ticket.context, "status": ticket.status,
                     "created_at": ticket.created_at}, status=201)
