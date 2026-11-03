"""Auth and profile endpoints (G§8.2, G§8.3). Thin views: validation in serializers, logic in services."""

from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from api.throttles import OtpSendIpThrottle, PhoneThrottle, RegisterThrottle

from . import services
from .serializers import RegisterSerializer


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
