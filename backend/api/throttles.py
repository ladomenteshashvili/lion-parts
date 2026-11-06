"""Throttle classes (G§5.5). Rates: REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]; state: the shared
DatabaseCache, so limits hold across gunicorn workers. Client IP: get_ident() with NUM_PROXIES.

Each allowed request takes one of N cache slots (LPK-221), as the admin login does. cache.add decides each slot through
the cache table's primary key, so parallel requests in any thread or worker cannot overshoot; DRF's own read-then-write
history can. Each slot expires one window after its request, which keeps DRF's sliding window. If the cache fails, the
request is refused: these limits protect logins and paid SMS, so they never fail open."""

import logging

from django.core.exceptions import ValidationError
from django.utils.crypto import salted_hmac
from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle

from accounts.validators import normalize_phone

logger = logging.getLogger(__name__)


def body(request):
    """Request JSON as a dict; throttles run before validation, so a JSON array/string must not crash."""
    return request.data if isinstance(request.data, dict) else {}


class SettingsRateThrottle(SimpleRateThrottle):
    """Reads the rate at request time (DRF caches it at import), so settings changes apply."""

    def get_rate(self):
        return api_settings.DEFAULT_THROTTLE_RATES[self.scope]

    def key(self, ident):
        return self.cache_format % {"scope": self.scope, "ident": ident} if ident else None

    def allow_request(self, request, view):
        key = self.get_cache_key(request, view)
        if self.rate is None or key is None:
            return True
        # Keyed digest: no phone, email or address in the cache table, and any identifier fits the key column.
        digest = salted_hmac("api.throttles", key, algorithm="sha256").hexdigest()
        slots = [f"throttle:{self.scope}:{digest}:{slot}" for slot in range(self.num_requests)]
        self.wait_seconds = self.duration
        try:
            held = self.cache.get_many(slots)  # also deletes expired rows; a stale read is safe, add decides
            now = self.timer()
            if any(slot not in held and self.cache.add(slot, now, self.duration) for slot in slots):
                return True
            taken_at = self.cache.get_many(slots).values()  # an add that lost a race is not in the first read
            if taken_at:
                self.wait_seconds = max(min(taken_at) + self.duration - now, 1)
        except Exception as error:  # any cache backend: refused and logged, never allowed through
            logger.error("Throttle %s: the cache failed (%s); request refused", self.scope, type(error).__name__)
        return False

    def wait(self):
        return self.wait_seconds


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


class SupportCustomerThrottle(SettingsRateThrottle):
    """Support tickets and order messages per logged-in user (guests: SupportGuestThrottle)."""

    scope = "support_customer"

    def get_cache_key(self, request, view):
        if request.method != "POST" or not (request.user and request.user.is_authenticated):
            return None
        return self.key(str(request.user.pk))
