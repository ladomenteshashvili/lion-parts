"""Throttle classes (G§5.5). Rates: REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]; state: the shared
DatabaseCache, so limits hold across gunicorn workers. Client IP: get_ident() with NUM_PROXIES."""

from django.core.exceptions import ValidationError
from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle

from accounts.validators import normalize_phone


def body(request):
    """Request JSON as a dict; throttles run before validation, so a JSON array/string must not crash."""
    return request.data if isinstance(request.data, dict) else {}


class SettingsRateThrottle(SimpleRateThrottle):
    """Reads the rate at request time (DRF caches it at import), so settings changes apply."""

    def get_rate(self):
        return api_settings.DEFAULT_THROTTLE_RATES[self.scope]

    def key(self, ident):
        return self.cache_format % {"scope": self.scope, "ident": ident} if ident else None


class IpThrottle(SettingsRateThrottle):
    def get_cache_key(self, request, view):
        return self.key(self.get_ident(request))


class OtpSendIpThrottle(IpThrottle):
    scope = "otp_send_ip"


class OtpVerifyThrottle(IpThrottle):
    scope = "otp_verify"


class LoginIpThrottle(IpThrottle):
    scope = "login_ip"


class RegisterThrottle(IpThrottle):
    scope = "register"


class PasswordResetThrottle(IpThrottle):
    scope = "password_reset"


class SearchThrottle(IpThrottle):
    scope = "search"


class SupportGuestThrottle(IpThrottle):
    """Guests only (G§8.9): logged-in customers are identified and not limited per IP."""

    scope = "support_guest"

    def get_cache_key(self, request, view):
        return None if request.user and request.user.is_authenticated else super().get_cache_key(request, view)


class PublicLinkThrottle(IpThrottle):
    """Public token links (/api/public/n/, /api/public/q/): slows down token guessing per IP (D23)."""

    scope = "public_link"


class PhoneThrottle(SettingsRateThrottle):
    """Per phone number, whatever the session or IP (stops SMS bombing one number)."""

    scope = "otp_send_phone"

    def get_cache_key(self, request, view):
        try:
            phone = normalize_phone(body(request).get("phone", ""))
        except ValidationError:  # invalid input is rejected by the view's serializer
            phone = request.session.get("registration_draft", {}).get("phone")
        return self.key(phone)


class EmailLoginThrottle(SettingsRateThrottle):
    """Per target email (stops password guessing on one account from many IPs)."""

    scope = "login_email"

    def get_cache_key(self, request, view):
        return self.key(str(body(request).get("email", "")).strip().lower())


class EmailSendThrottle(SettingsRateThrottle):
    """Per logged-in user."""

    scope = "email_send"

    def get_cache_key(self, request, view):
        return self.key(request.user.pk if request.user.is_authenticated else None)
