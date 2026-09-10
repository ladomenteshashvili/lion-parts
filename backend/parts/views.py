from decimal import Decimal, InvalidOperation

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
from .models import PartSearchLog
from .serializers import PartQuoteRequestSerializer


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
        "created_at": log.created_at,
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

    logs = PartSearchLog.objects.filter(
        customer_phone=customer.phone,
        status=PartSearchLog.STATUS_SUCCESS,
    ).order_by("-created_at", "-id")[:50]

    seen = set()
    results = []

    for log in logs:
        dedupe_key = (log.part_number.upper(), log.vin.upper())

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        results.append(_build_feed_item(log))

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

    if not customer or not customer.has_quote_request_permission():
        return Response(
            {"detail": "quote request is not enabled for this customer"},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = PartQuoteRequestSerializer(data=request.data)

    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    quote_request = serializer.save()

    return Response(
        PartQuoteRequestSerializer(quote_request).data,
        status=status.HTTP_201_CREATED,
    )