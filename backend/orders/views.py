"""Checkout and order endpoints (G§8.6, G§8.7). Thin views: logic in orders.checkout / orders.services."""

from django.http import HttpResponse
from django.utils.translation import get_language
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from api.errors import ApiError
from api.permissions import IsFullyVerified

from . import checkout
from .invoices import invoice_language, render_invoice
from .models import Order


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
