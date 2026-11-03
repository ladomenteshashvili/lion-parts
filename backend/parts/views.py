"""Parts endpoints (G§8.4). Thin views: validation in serializers, logic in parts.services."""

from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from api.errors import ApiError
from api.pagination import Pagination
from api.permissions import IsPhoneVerified
from api.throttles import SearchThrottle

from . import services
from .models import PartQuoteRequest
from .serializers import QuoteRequestSerializer, SearchSerializer


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
def public_quote(request, token):
    qr = PartQuoteRequest.objects.filter(notification_token=token, price_ready_at__isnull=False,
                                         unit_price__isnull=False).first()
    if qr is None:
        raise ApiError("not_found", status=404)
    return Response(services.public_quote_data(qr))
