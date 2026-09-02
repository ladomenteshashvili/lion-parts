from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from accounts.models import Customer

from .serializers import PartQuoteRequestSerializer


@api_view(["POST"])
def search_parts(request):
    part_number = request.data.get("part_number", "").strip()
    vin = request.data.get("vin", "").strip()

    if not part_number:
        return Response(
            {"detail": "part_number is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response(
        {
            "quote_id": "Q-DEMO-0001",
            "part_number": part_number,
            "vin": vin or None,
            "results": [
                {
                    "part_option_id": "P-DEMO-001",
                    "name": "Demo OEM Part",
                    "condition": "New",
                    "brand": "OEM",
                    "availability": "Available",
                    "eta_days": 14,
                    "final_price_gel": 650.00,
                    "currency": "GEL",
                    "note": "Original new part. Demo offer. Supplier integration will be added later.",
                },
                {
                    "part_option_id": "P-DEMO-002",
                    "name": "Demo Aftermarket Part",
                    "condition": "New",
                    "brand": "Aftermarket",
                    "availability": "Available",
                    "eta_days": 10,
                    "final_price_gel": 520.00,
                    "currency": "GEL",
                    "note": "Lower price option. Compatibility must be confirmed before purchase.",
                },
                {
                    "part_option_id": "P-DEMO-003",
                    "name": "Demo OEM Express Part",
                    "condition": "New",
                    "brand": "OEM",
                    "availability": "Limited",
                    "eta_days": 7,
                    "final_price_gel": 790.00,
                    "currency": "GEL",
                    "note": "Faster ETA option. Final availability will be checked after payment.",
                },
            ],
        }
    )


@api_view(["POST"])
def create_quote_request(request):
    session_id = request.data.get("session_id", "").strip()

    if not session_id:
        return Response(
            {"session_id": ["This field is required."]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer = Customer.objects.filter(session_id=session_id).first()

    if not customer or not customer.can_request_quote:
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