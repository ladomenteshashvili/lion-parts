from decimal import Decimal, InvalidOperation

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from accounts.models import Customer
from accounts.customer_sessions import get_customer_for_session

from .providers import (
    PartsProviderError,
    calculate_part_price_provider,
    search_parts_provider,
)
from .models import PartQuoteRequest, PartSearchLog
from .serializers import PartQuoteRequestSerializer, PublicPreparedQuoteSerializer


def _get_customer_by_session_id(session_id: str) -> Customer | None:
    if not session_id:
        return None

    return get_customer_for_session(session_id)


@api_view(["POST"])
def search_parts(request):
    part_number = request.data.get("part_number", "").strip()
    vin = request.data.get("vin", "").strip()
    session_id = request.data.get("session_id", "").strip()

    if not part_number:
        return Response(
            {"detail": "part_number is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer = _get_customer_by_session_id(session_id)

    try:
        return Response(
            search_parts_provider(
                part_number,
                vin or None,
                customer,
                session_id,
            )
        )
    except PartsProviderError:
        return Response(
            {"detail": "parts provider request failed"},
            status=status.HTTP_502_BAD_GATEWAY,
        )


def _build_feed_item(log: PartSearchLog) -> dict:
    normalized_response = log.normalized_response or {}
    response_results = normalized_response.get("results", [])

    first_result = {}
    if isinstance(response_results, list) and response_results:
        if isinstance(response_results[0], dict):
            first_result = response_results[0]

    return {
        "id": log.id,
        "part_number": log.part_number,
        "vin": log.vin,
        "provider": log.provider,
        "quote_id": normalized_response.get("quote_id", ""),
        "found_count": log.found_count,
        "top_result_name": first_result.get("name", ""),
        "top_result_price_gel": first_result.get("final_price_gel"),
        "feed_status": "search_result",
        "quote_request_id": None,
        "notification_token": None,
        "part_option_id": first_result.get("part_option_id", ""),
        "condition": first_result.get("condition", ""),
        "brand": first_result.get("brand", ""),
        "availability": first_result.get("availability", ""),
        "eta_days": first_result.get("eta_days"),
        "weight_kg": first_result.get("weight_kg"),
        "operator_message": "",
        "created_at": log.created_at,
    }


def _build_quote_request_feed_item(quote_request: PartQuoteRequest) -> dict:
    if quote_request.is_price_ready:
        feed_status = "price_ready"
    elif quote_request.status == PartQuoteRequest.STATUS_CANCELLED:
        feed_status = "cancelled"
    else:
        feed_status = "processing"

    return {
        "id": f"quote-request-{quote_request.id}",
        "part_number": quote_request.part_number,
        "vin": quote_request.vin,
        "provider": "operator",
        "quote_id": quote_request.quote_id or f"REQUEST-{quote_request.id}",
        "found_count": 1 if quote_request.is_price_ready else 0,
        "top_result_name": quote_request.name,
        "top_result_price_gel": quote_request.final_price_gel,
        "feed_status": feed_status,
        "quote_request_id": quote_request.id,
        "notification_token": (
            quote_request.notification_token if quote_request.is_price_ready else None
        ),
        "part_option_id": quote_request.part_option_id or f"REQUEST-{quote_request.id}",
        "condition": quote_request.condition,
        "brand": quote_request.brand,
        "availability": quote_request.availability,
        "eta_days": quote_request.eta_days,
        "weight_kg": quote_request.prepared_weight_kg,
        "operator_message": quote_request.operator_message,
        "created_at": quote_request.price_ready_at or quote_request.created_at,
    }


@api_view(["GET"])
def get_parts_feed(request):
    session_id = request.query_params.get("session_id", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer = get_customer_for_session(session_id)

    if not customer or not customer.is_phone_verified or not customer.phone:
        return Response(
            {
                "requires_phone_verification": True,
                "results": [],
            }
        )

    logs = list(PartSearchLog.objects.filter(
        customer_phone=customer.phone,
        status=PartSearchLog.STATUS_SUCCESS,
    ).order_by("-created_at", "-id")[:50])

    quote_requests = list(PartQuoteRequest.objects.filter(
        customer_phone=customer.phone,
    ).order_by("-updated_at", "-id")[:50])

    feed_entries = [
        (log.created_at, _build_feed_item(log)) for log in logs
    ] + [
        (
            quote_request.price_ready_at or quote_request.created_at,
            _build_quote_request_feed_item(quote_request),
        )
        for quote_request in quote_requests
    ]
    feed_entries.sort(key=lambda entry: entry[0], reverse=True)

    seen = set()
    results = []

    for _created_at, item in feed_entries:
        dedupe_key = (item["part_number"].upper(), item["vin"].upper())

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        results.append(item)

        if len(results) >= 10:
            break

    return Response(
        {
            "requires_phone_verification": False,
            "results": results,
        }
    )


@api_view(["POST"])
def calculate_part_price(request):
    session_id = request.data.get("session_id", "").strip()
    part_number = request.data.get("part_number", "").strip()
    part_option_id = request.data.get("part_option_id", "").strip()
    raw_weight_kg = request.data.get("weight_kg")

    if not session_id:
        return Response(
            {"session_id": ["This field is required."]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not part_number:
        return Response(
            {"part_number": ["This field is required."]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not part_option_id:
        return Response(
            {"part_option_id": ["This field is required."]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        weight_kg = Decimal(str(raw_weight_kg))
    except (InvalidOperation, TypeError):
        return Response(
            {"weight_kg": ["Enter a valid weight."]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if weight_kg <= 0:
        return Response(
            {"weight_kg": ["Weight must be greater than zero."]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer = _get_customer_by_session_id(session_id)

    if not customer or not customer.has_weight_entry_permission():
        return Response(
            {"detail": "weight entry is not enabled for this customer"},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        option = calculate_part_price_provider(
            part_number=part_number,
            part_option_id=part_option_id,
            weight_kg=weight_kg,
            customer=customer,
        )
    except PartsProviderError:
        return Response(
            {"detail": "part price calculation failed"},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    return Response(option)


@api_view(["POST"])
def create_quote_request(request):
    session_id = request.data.get("session_id", "").strip()

    if not session_id:
        return Response(
            {"session_id": ["This field is required."]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer = get_customer_for_session(session_id)

    serializer = PartQuoteRequestSerializer(data=request.data)

    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    request_type = serializer.validated_data.get(
        "request_type",
        PartQuoteRequest.REQUEST_TYPE_MANUAL_SEARCH,
    )

    if request_type == PartQuoteRequest.REQUEST_TYPE_WEIGHT_PRICE:
        if not customer or not customer.is_phone_verified:
            return Response(
                {"detail": "phone verification is required"},
                status=status.HTTP_403_FORBIDDEN,
            )

        missing_offer_fields = [
            field
            for field in ("quote_id", "part_option_id")
            if not serializer.validated_data.get(field)
        ]
        if missing_offer_fields:
            return Response(
                {
                    field: ["This field is required for a weight price request."]
                    for field in missing_offer_fields
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
    elif not customer or not customer.has_quote_request_permission():
        return Response(
            {"detail": "quote request is not enabled for this customer"},
            status=status.HTTP_403_FORBIDDEN,
        )

    existing_request = PartQuoteRequest.objects.filter(
        customer_phone=customer.phone,
        part_number__iexact=serializer.validated_data["part_number"],
        vin__iexact=serializer.validated_data.get("vin", ""),
        part_option_id=serializer.validated_data.get("part_option_id", ""),
        request_type=request_type,
        status__in=[
            PartQuoteRequest.STATUS_NEW,
            PartQuoteRequest.STATUS_CONTACTED,
        ],
    ).first()

    if existing_request:
        return Response(
            PartQuoteRequestSerializer(existing_request).data,
            status=status.HTTP_200_OK,
        )

    quote_request = serializer.save(
        customer_phone=customer.phone,
        customer_name=(
            serializer.validated_data.get("customer_name") or customer.name
        ),
    )

    return Response(
        PartQuoteRequestSerializer(quote_request).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
def get_prepared_quote(request, token):
    quote_request = get_object_or_404(
        PartQuoteRequest,
        notification_token=token,
        price_ready_at__isnull=False,
        final_price_gel__isnull=False,
    )
    return Response(PublicPreparedQuoteSerializer(quote_request).data)


@api_view(["POST"])
def acknowledge_prepared_quote(request, token):
    quote_request = get_object_or_404(
        PartQuoteRequest,
        notification_token=token,
        price_ready_at__isnull=False,
        final_price_gel__isnull=False,
    )

    if quote_request.notification_acknowledged_at is None:
        quote_request.notification_acknowledged_at = timezone.now()
        quote_request.save(update_fields=[
            "notification_acknowledged_at",
            "updated_at",
        ])

    return Response(PublicPreparedQuoteSerializer(quote_request).data)
