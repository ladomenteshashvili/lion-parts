from decimal import Decimal, InvalidOperation

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from accounts.models import Customer

from .providers import (
    PartsProviderError,
    calculate_part_price_provider,
    search_parts_provider,
)
from .serializers import PartQuoteRequestSerializer


def _get_customer_by_session_id(session_id: str) -> Customer | None:
    if not session_id:
        return None

    return Customer.objects.filter(session_id=session_id).first()


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

    customer = Customer.objects.filter(session_id=session_id).first()

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