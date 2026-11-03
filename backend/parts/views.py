"""Parts endpoints (G§8.4). Thin views: validation in serializers, logic in parts.services."""

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from api.errors import ApiError
from api.pagination import Pagination
from api.permissions import IsPhoneVerified
from api.throttles import PublicLinkThrottle, SearchThrottle
from pricing.validity import is_expired

from . import services
from .history import FILTERS, build_history, unseen_ready_quotes
from .models import PartQuoteRequest, PartSearchLog
from .serializers import CalculatePriceSerializer, QuoteRequestSerializer, SearchSerializer


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([SearchThrottle])
def search(request):
    serializer = SearchSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    log = services.run_search(request, **serializer.validated_data)
    return Response(services.search_response(request, log))


@api_view(["GET", "POST"])
def quote_requests(request):
    customer = request.user.customer
    if request.method == "GET":
        paginator = Pagination()
        page = paginator.paginate_queryset(PartQuoteRequest.objects.filter(customer=customer), request)
        return paginator.get_paginated_response([services.quote_request_data(qr) for qr in page])

    if not IsPhoneVerified().has_permission(request, None):
        raise ApiError("phone_verification_required", status=403)
    serializer = QuoteRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    log = None
    if data["request_type"] == PartQuoteRequest.REQUEST_TYPE_WEIGHT_PRICE:
        log = services.owned_log(request, data["search_id"])
    elif not customer.get_tariff().can_request_quote:
        raise ApiError("quote_request_not_allowed", status=403)
    qr, created = services.create_quote_request(customer, data, log)
    return Response(services.quote_request_data(qr), status=201 if created else 200)


@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([PublicLinkThrottle])
def public_quote(request, token):
    qr = PartQuoteRequest.objects.filter(notification_token=token, price_ready_at__isnull=False,
                                         unit_price_usd__isnull=False).first()
    if qr is None or is_expired(qr.price_valid_until):  # an expired quote needs operator re-approval
        raise ApiError("not_found", status=404)
    return Response(services.public_quote_data(qr))


@api_view(["GET", "DELETE"])
def history(request):
    customer = request.user.customer
    if request.method == "DELETE":
        PartSearchLog.objects.filter(customer=customer, hidden_at__isnull=True).update(hidden_at=timezone.now())
        return Response(status=204)  # quote requests stay
    filter_name = request.query_params.get("filter", "all")
    if filter_name not in FILTERS:
        raise serializers.ValidationError({"filter": [serializers.ErrorDetail(
            str(_("Choose one of: all, ready, checking, other.")), code="invalid")]})
    paginator = Pagination()
    page = paginator.paginate_queryset(build_history(customer, filter_name), request)
    return paginator.get_paginated_response(page, new_count=unseen_ready_quotes(customer).count())


@api_view(["POST"])
def history_seen(request):
    unseen_ready_quotes(request.user.customer).update(seen_at=timezone.now())
    return Response(status=204)


@api_view(["POST"])
@throttle_classes([SearchThrottle])  # each call queries the supplier
def calculate_price(request):
    if not request.user.customer.get_tariff().can_enter_weight:
        raise ApiError("weight_entry_not_allowed", status=403)
    serializer = CalculatePriceSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    log = services.owned_log(request, data["search_id"])
    new_log = services.calculate_weight_price(request, log, data["offer_id"], data["weight_kg"])
    return Response({"search_id": new_log.pk, "price_valid_until": new_log.price_valid_until,
                     "offer": services.present_offer(request, new_log.offers()[0], new_log.part_number,
                                                     new_log.currency, new_log.exchange_rate)})
