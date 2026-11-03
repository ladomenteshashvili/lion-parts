"""Session-cookie authentication that enforces CSRF on every unsafe request (G§4.2, G§5.3)."""

from rest_framework import exceptions
from rest_framework.authentication import SessionAuthentication

from .errors import ApiError


class CsrfSessionAuthentication(SessionAuthentication):
    """DRF's SessionAuthentication only checks CSRF for logged-in users; login and
    registration (anonymous) must be protected too."""

    def authenticate(self, request):
        self.enforce_csrf(request)  # no-op for GET/HEAD/OPTIONS
        user = getattr(request._request, "user", None)
        return (user, None) if user and user.is_active else None

    def enforce_csrf(self, request):
        try:
            super().enforce_csrf(request)
        except exceptions.PermissionDenied:
            raise ApiError("csrf_failed", status=403) from None

    def authenticate_header(self, request):
        return "Session"  # makes DRF answer anonymous permission failures with 401, not 403
