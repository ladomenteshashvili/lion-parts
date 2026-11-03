"""Parts endpoints (G§8.4). Thin views: validation in serializers, logic in parts.services."""

from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from api.throttles import SearchThrottle

from . import services
from .serializers import SearchSerializer


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([SearchThrottle])
def search(request):
    serializer = SearchSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    log = services.run_search(request, **serializer.validated_data)
    return Response(services.search_response(request, log))
