"""Checkout and order endpoints (G§8.6, G§8.7). Thin views: logic in orders.checkout / orders.services."""

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

from . import checkout
from .invoices import invoice_language, render_invoice
from .models import Order
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


@api_view(["GET"])
def order_list(request):
    orders = order_queryset().filter(customer=request.user.customer)
    paginator = Pagination()
    page = paginator.paginate_queryset(orders, request)
    return paginator.get_paginated_response(orders_data(request, page))


@api_view(["GET"])
def order_detail(request, order_number):
    return Response(order_data(request, own_order(request, order_number, order_queryset(detail=True)), detail=True))
