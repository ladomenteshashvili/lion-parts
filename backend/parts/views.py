from django.shortcuts import render

# Create your views here.
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status


@api_view(["POST"])
def search_parts(request):
    part_number = request.data.get("part_number", "").strip()
    vin = request.data.get("vin", "").strip()

    if not part_number:
        return Response(
            {"detail": "part_number is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response({
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
                "note": "Demo offer. Supplier integration will be added later.",
            }
        ],
    })
