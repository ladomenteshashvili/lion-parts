"""Failed-attempt limits around the Django Admin login (LPK-182). 10 per IP and 5 per username in 15 minutes.

Each attempt reserves one of N cache slots before the credentials are checked, and a successful login gives it back,
so only failures stay counted. Slots use cache.add because it is atomic (the cache table's primary key). cache.incr
is not: on DatabaseCache it reads then writes, which loses parallel updates and resets the expiry. Reserving first
also holds the limit while password checks run in parallel. Each slot expires 15 minutes after its own attempt."""

import copy
import logging
import unicodedata

from django.contrib import admin
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework.throttling import BaseThrottle

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 15 * 60
IP_LIMIT = 10
USERNAME_LIMIT = 5
BLOCKED_MESSAGE = _("Too many failed login attempts. Please try again later.")


class BlockedLoginForm(AdminAuthenticationForm):
    """Shows the message without ever checking the credentials."""

    def clean(self):
        raise ValidationError(BLOCKED_MESSAGE, code="throttled")


def normalize_username(raw):
    """NFKC and strip as the login form's UsernameField does, so a look-alike that logs in as the same user counts as
    that user. Lowercase on top: the backend is case-sensitive, so this only ever groups more attempts together."""
    return unicodedata.normalize("NFKC", raw).strip().lower()


def _digest(kind, value):
    """Keyed with SECRET_KEY, so neither the cache table nor the logs hold or reveal the username."""
    return salted_hmac(f"api.admin_login.{kind}", value, algorithm="sha256").hexdigest()


def _slots(kind, value, limit):
    digest = _digest(kind, value)
    return [f"admin_login:{kind}:{digest}:{slot}" for slot in range(limit)]


def _reserve(slots):
    """-> the slot taken, or None when all are in use."""
    taken = cache.get_many(slots)  # also deletes expired rows
    # Skipping taken slots avoids pointless failed inserts. A stale read is safe: cache.add still decides each slot.
    return next((slot for slot in slots if slot not in taken and cache.add(slot, 1, WINDOW_SECONDS)), None)


def _blocked(request):
    site = copy.copy(admin.site)  # this request only: the shared site keeps its real login form
    site.login_form = BlockedLoginForm
    response = site.login(request)
    response.status_code = 429
    return response


@sensitive_post_parameters()
def admin_login(request):
    """Wraps admin.site.login. CSRF is checked by the middleware before this view runs."""
    username = normalize_username(request.POST.get("username", ""))  # request.POST is empty on GET
    if not username or not request.POST.get("password"):  # no credentials to check: nothing to count
        return admin.site.login(request)

    ip = BaseThrottle().get_ident(request) or ""  # NUM_PROXIES decides which address counts
    username_slots = _slots("username", username, USERNAME_LIMIT)
    ip_slot = _reserve(_slots("ip", ip, IP_LIMIT))
    username_slot = _reserve(username_slots) if ip_slot else None
    if not username_slot:
        if ip_slot:
            cache.delete(ip_slot)  # a blocked attempt checks nothing, so it uses up nothing
        logger.warning("Admin login blocked: ip=%s username_hash=%s limit=%s", ip,
                       _digest("username", username)[:16], "username" if ip_slot else "ip")
        return _blocked(request)

    response = admin.site.login(request)
    if response.status_code == 302:  # LoginView redirects only after a successful login
        cache.delete(ip_slot)
        cache.delete_many(username_slots)
    return response
