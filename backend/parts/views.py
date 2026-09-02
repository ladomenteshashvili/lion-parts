from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response


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