from django.conf import settings
from django.contrib.auth import password_validation
from django.http import JsonResponse
from django.utils.translation import get_language_info
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from pricing.services import meta_pricing

from .errors import MESSAGES

OTP_LENGTH = 6  # SMS codes are f"{secrets.randbelow(10**6):06d}" (G§5.7)


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    return Response({
        "status": "ok",
        "service": "lion-parts-backend",
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def meta(request):
    return Response({
        # Each language's own name (e.g. "English", "Русский"), never translated (G§8.1).
        "languages": [
            {"code": code, "name": get_language_info(code)["name_local"]}
            for code, _ in settings.LANGUAGES
        ],
        "default_language": settings.LANGUAGE_CODE,
        **meta_pricing(),
        "otp_length": OTP_LENGTH,
        "otp_resend_seconds": settings.PHONE_VERIFICATION_RESEND_SECONDS,
        "password_rules": {"min_length": _password_min_length(),
                           "upper": True, "lower": True, "digit": True, "symbol": True},
    })


def _password_min_length():
    for validator in password_validation.get_default_password_validators():
        if isinstance(validator, password_validation.MinimumLengthValidator):
            return validator.min_length
    return 0


def _error_response(code, status):
    return JsonResponse({"code": code, "detail": str(MESSAGES[code])}, status=status,
                        json_dumps_params={"ensure_ascii": False})


def csrf_failure(request, reason=""):
    """CSRF_FAILURE_VIEW: Django-level CSRF rejections get the JSON envelope, not an HTML page."""
    return _error_response("csrf_failed", 403)


def not_found(request, exception=None):
    """handler404 for URLs outside DRF views."""
    return _error_response("not_found", 404)


def server_error(request):
    """handler500: no traceback or HTML leaks to clients."""
    return _error_response("server_error", 500)
