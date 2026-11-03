"""Auth and profile endpoints (G§8.2, G§8.3). Thin views: validation in serializers, logic in services."""

from django.contrib.auth import authenticate, logout
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from api.errors import ApiError
from api.throttles import EmailLoginThrottle, LoginIpThrottle, OtpSendIpThrottle, PhoneThrottle, RegisterThrottle

from . import services
from .serializers import CustomerSerializer, LoginSerializer, RegisterSerializer


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([RegisterThrottle, PhoneThrottle, OtpSendIpThrottle])
def register(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    services.ensure_session(request)
    draft = services.save_registration_draft(request, serializer.validated_data)
    return Response(services.send_registration_code(request, draft))


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([PhoneThrottle, OtpSendIpThrottle])
def register_resend_code(request):
    draft = services.load_registration_draft(request)
    return Response(services.send_registration_code(request, draft))


@ensure_csrf_cookie
@api_view(["GET"])
@permission_classes([AllowAny])
def csrf(request):
    return Response({"csrf_token": get_token(request)})


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([LoginIpThrottle, EmailLoginThrottle])
def login_view(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = authenticate(request, username=serializer.validated_data["email"].strip().lower(),
                        password=serializer.validated_data["password"])
    if not services.can_log_in(user):
        raise ApiError("invalid_credentials")  # same body for every failure (no account enumeration)
    services.login_customer(request, user.customer)
    return Response({"customer": CustomerSerializer(user.customer).data, "csrf_token": get_token(request)})


@api_view(["POST"])
def logout_view(request):
    logout(request)
    return Response({"csrf_token": get_token(request)})


@api_view(["GET"])
@permission_classes([AllowAny])
def me(request):
    customer = getattr(request.user, "customer", None) if services.can_log_in(request.user) else None
    return Response({
        "authenticated": customer is not None,
        "customer": CustomerSerializer(customer).data if customer else None,
        "cart_count": 0,  # filled by the cart (LP-BE-503)
        "badges": None,   # filled by notifications (LP-BE-904)
    })
