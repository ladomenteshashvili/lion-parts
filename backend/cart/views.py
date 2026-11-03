"""Cart endpoints (G§8.5): guest session or customer (AllowAny), always server-priced."""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import services
from .serializers import AddItemSerializer


@api_view(["POST"])
@permission_classes([AllowAny])
def add_item(request):
    serializer = AddItemSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    cart = services.add_item(request, serializer.validated_data)
    return Response(services.cart_data(request, cart), status=201)
