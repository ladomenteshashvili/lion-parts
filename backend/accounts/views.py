from django.shortcuts import render

# Create your views here.
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from .models import Customer
from .serializers import CustomerSerializer


@api_view(["GET"])
def get_profile(request):
    session_id = request.query_params.get("session_id", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer = Customer.objects.filter(session_id=session_id).first()

    if not customer:
        return Response(
            {"detail": "profile not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = CustomerSerializer(customer)
    return Response(serializer.data)


@api_view(["POST"])
def demo_login(request):
    session_id = request.data.get("session_id", "").strip()
    customer_name = request.data.get("customer_name", "").strip()
    customer_phone = request.data.get("customer_phone", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not customer_name:
        return Response(
            {"detail": "customer_name is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not customer_phone:
        return Response(
            {"detail": "customer_phone is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer, _created = Customer.objects.update_or_create(
        session_id=session_id,
        defaults={
            "name": customer_name,
            "phone": customer_phone,
            "is_phone_verified": False,
        },
    )

    serializer = CustomerSerializer(customer)
    return Response(serializer.data, status=status.HTTP_200_OK)