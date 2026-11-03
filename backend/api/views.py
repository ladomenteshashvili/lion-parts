from django.conf import settings
from django.utils.translation import get_language_info
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


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
