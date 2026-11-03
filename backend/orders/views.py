"""Checkout and order endpoints (G§8.6, G§8.7). Thin views: logic in orders.checkout / orders.services."""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from api.permissions import IsFullyVerified

from . import checkout


@api_view(["GET"])
@permission_classes([IsFullyVerified])
def checkout_options(request):
    return Response(checkout.checkout_options(request, request.user.customer))
