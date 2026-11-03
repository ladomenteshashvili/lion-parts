"""Cart endpoints (G§8.5): guest session or customer (AllowAny), always server-priced."""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import services
from .serializers import AddItemSerializer, UpdateItemSerializer, VinSerializer


@api_view(["POST"])
@permission_classes([AllowAny])
def add_item(request):
    serializer = AddItemSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    cart = services.add_item(request, serializer.validated_data)
    return Response(services.cart_data(request, cart), status=201)


@api_view(["GET"])
@permission_classes([AllowAny])
def cart(request):
    return Response(services.cart_data(request, services.get_cart(request, create=False)))


@api_view(["PATCH", "DELETE"])
@permission_classes([AllowAny])
def item(request, item_id):
    line = services.own_line(request, item_id)
    if request.method == "DELETE":
        line.delete()
    else:
        serializer = UpdateItemSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        services.update_line(line, serializer.validated_data)
    return Response(services.cart_data(request, line.cart))


@api_view(["POST"])
@permission_classes([AllowAny])
def apply_vin(request):
    serializer = VinSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    cart = services.get_cart(request)
    services.apply_vin(cart, serializer.validated_data["vin"])
    return Response(services.cart_data(request, cart))
