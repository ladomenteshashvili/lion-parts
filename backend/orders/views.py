"""Checkout and order endpoints (G§8.6, G§8.7). Thin views: logic in orders.checkout / orders.services."""

from django.http import HttpResponse
from django.utils.translation import get_language
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from api.errors import MESSAGES, ApiError
from api.permissions import IsFullyVerified

from payments.services import start_payment

from . import checkout
from .invoices import invoice_language, render_invoice
from .models import Order
from .serializers import order_data, payment_data


def own_order(request, order_number):
    """The customer's own order, else 404 (never reveal that it exists)."""
    order = Order.objects.filter(order_number=order_number, customer=request.user.customer).first()
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
