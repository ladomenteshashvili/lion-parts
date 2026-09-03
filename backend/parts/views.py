from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from accounts.models import Customer

from .providers import PartsProviderError, search_parts_provider
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

    try:
        return Response(search_parts_provider(part_number, vin or None))
    except PartsProviderError:
        return Response(
            {"detail": "parts provider request failed"},
            status=status.HTTP_502_BAD_GATEWAY,
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