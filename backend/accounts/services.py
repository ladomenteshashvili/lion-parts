"""Account services (G§3.1): views and admin call these, never the other way around."""

from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.utils import timezone

from api.errors import ApiError

from . import otp


def customer_language(customer=None, fallback=None):
    """Language for messages sent outside the customer's request (SMS, email, notifications):
    preferred_language, else `fallback` (e.g. the registration draft), else the default (G§5.8)."""
    codes = {code for code, _name in settings.LANGUAGES}
    for lang in (getattr(customer, "preferred_language", None), fallback):
        if lang in codes:
            return lang
    return settings.LANGUAGE_CODE


# Registration draft (G§5.7): the account is created only after the phone is verified.

DRAFT_KEY = "registration_draft"
DRAFT_MINUTES = 30


def ensure_session(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


def save_registration_draft(request, data):
    """Store the validated form in the session; the password only as a hash."""
    draft = {**data, "password": make_password(data["password"]),
             "expires_at": (timezone.now() + timedelta(minutes=DRAFT_MINUTES)).isoformat()}
    request.session[DRAFT_KEY] = draft
    return draft


def load_registration_draft(request):
    draft = request.session.get(DRAFT_KEY)
    if not draft or datetime.fromisoformat(draft["expires_at"]) <= timezone.now():
        raise ApiError("registration_expired")
    return draft


def send_registration_code(request, draft):
    return otp.send_code(session_key=ensure_session(request), phone=draft["phone"], purpose="registration",
                         language=customer_language(None, fallback=draft.get("preferred_language")))
