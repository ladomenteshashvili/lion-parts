from django.conf import settings
from django.http import JsonResponse
from django.utils.translation import get_language_info
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .errors import MESSAGES


@api_view(["GET"])
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
    })


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
