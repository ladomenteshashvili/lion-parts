"""Checkout and order endpoints (G§8.6, G§8.7). Thin views: logic in orders.checkout / orders.services."""

from django.db.models import Case, Count, Exists, IntegerField, OuterRef, Q, When
from django.http import HttpResponse
from django.utils.translation import get_language
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from api.errors import MESSAGES, ApiError
from api.pagination import Pagination
from api.permissions import IsFullyVerified

from payments.models import Payment
from payments.services import request_payment, start_payment

from . import checkout, services
from .invoices import invoice_language, render_invoice
from .models import Order, OrderItem
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
