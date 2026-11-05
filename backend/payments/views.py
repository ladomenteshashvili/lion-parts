"""Provider-side payment endpoints (G§8.8)."""

import logging

from django.views.decorators.csrf import csrf_exempt
from rest_framework import serializers
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from api.errors import ApiError
from api.permissions import IsCustomer
from orders.serializers import payment_data

from .models import Payment
from .providers import PLATFORM_STATUSES, get_provider
from .services import record_provider_result

logger = logging.getLogger(__name__)


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([])
def callback(request, provider):
    """The adapter verifies signature, amount and currency; anything invalid -> 400, no state change."""
    adapter = get_provider()
    if provider != adapter.name:
        raise ApiError("not_found", status=404)
    try:
        payment, status = adapter.handle_callback(request)
        if status not in PLATFORM_STATUSES:
            raise ValueError(f"unexpected status {status!r}")
        if payment.provider != adapter.name:  # e.g. never let a card callback mark an invoice paid
            raise ValueError(f"payment {payment.pk} belongs to provider {payment.provider!r}")
    except Exception:  # never trust a callback the adapter couldn't verify
        logger.warning("Invalid %s payment callback", provider, exc_info=True)
        raise ApiError("invalid_callback", status=400)
    record_provider_result(payment, status, adapter, payload={"callback": True})
    return Response({"ok": True})


@api_view(["POST"])
@permission_classes([IsCustomer])
def fake_complete(request, payment_id):
    """Demo only (mounted with ENABLE_DEMO_ENDPOINTS): the order's owner plays the provider's result
    for a pending fake payment (master plan D3)."""
    payment = Payment.objects.filter(pk=payment_id, provider="fake", order__customer=request.user.customer).first()
    if payment is None:
        raise ApiError("not_found", status=404)
    status = request.data.get("status") if isinstance(request.data, dict) else None
    if status not in ("paid", "failed", "expired"):
        raise serializers.ValidationError({"status": [serializers.ErrorDetail("paid | failed | expired", "invalid")]})
    if payment.status != Payment.STATUS_PENDING:
        raise ApiError("nothing_to_pay", status=409)
    return Response(payment_data(record_provider_result(payment, status, get_provider(), payload={"fake": True})))
