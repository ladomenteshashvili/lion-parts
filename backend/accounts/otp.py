"""Phone OTP (G§5.7): 6 digits, stored hashed, TTL, resend cooldown per (session, phone, purpose),
max attempts per code. Codes and API keys are never logged.

Sending (LPK-222): cache.add claims the send before anything else, so parallel requests in any thread or worker send one
billable SMS. No lock or transaction is held during the provider call. The claim is given back once the outcome is known:
after success the new code's row enforces the cooldown, and after a definite rejection a retry is safe. When the SMS may
have left (timeout, 5xx, unexpected error) the claim stays until it expires, so a retry cannot pay for a second one."""

import logging
import math
import secrets
import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone, translation
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext_lazy as _

from api.errors import ApiError

from .models import PhoneVerificationCode
from .sms import SenderGeDeliveryUnknown, SenderGeError, send_sms

SMS_TEXT = _("Lion Parts code: {code}")
MIN_CLAIM_SECONDS = 60  # outlasts a slow provider call, so a claim cannot expire while its SMS is being sent

logger = logging.getLogger(__name__)


def demo_codes_enabled():
    return settings.SMS_DEMO_MODE and (settings.DEBUG or settings.ENABLE_DEMO_ENDPOINTS)


def mask_phone(phone):
    return f"+995 {phone[:3]} •• •• {phone[-2:]}"


def _pending(session_key, phone, purpose):
    return PhoneVerificationCode.objects.filter(
        session_key=session_key, phone=phone, purpose=purpose, status=PhoneVerificationCode.STATUS_PENDING,
    )


def _drop_expired_claim(claim):
    """DatabaseCache.add overwrites an expired row with a plain UPDATE, which parallel requests can all win, and cache.get
    deletes by key, so it can remove a row another request has just replaced. This deletes only a row still expired (or
    about to be, so none can expire before add reads it). add then inserts, and the primary key picks one winner."""
    table = connection.ops.quote_name(settings.CACHES["default"]["LOCATION"])
    soon = connection.ops.adapt_datetimefield_value(timezone.now() + timedelta(seconds=2))
    with connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {table} WHERE cache_key = %s AND expires < %s", [cache.make_key(claim), soon])


def _claim_send(session_key, phone, purpose, cooldown):
    """-> the cache key now held by this request, or otp_resend_too_soon. A cache that cannot answer also refuses."""
    digest = salted_hmac("accounts.otp.send", f"{session_key}|{phone}|{purpose}", algorithm="sha256").hexdigest()
    claim, seconds = f"otp_send:{digest}", max(cooldown, MIN_CLAIM_SECONDS)
    _drop_expired_claim(claim)
    if cache.add(claim, time.time() + seconds, seconds):
        return claim
    expires_at = cache.get(claim)
    resend_in = math.ceil(expires_at - time.time()) if expires_at else cooldown
    raise ApiError("otp_resend_too_soon", status=409, resend_in=max(resend_in, 1))


def send_code(*, session_key, phone, purpose, customer=None, language=None):
    cooldown = settings.PHONE_VERIFICATION_RESEND_SECONDS
    ttl = settings.PHONE_VERIFICATION_CODE_TTL_MINUTES * 60

    claim = _claim_send(session_key, phone, purpose, cooldown)
    now = timezone.now()  # read under the claim: a row stored by the previous holder is visible below
    latest = _pending(session_key, phone, purpose).order_by("-created_at").first()
    if latest and (age := (now - latest.created_at).total_seconds()) < cooldown:
        cache.delete(claim)
        raise ApiError("otp_resend_too_soon", status=409, resend_in=math.ceil(cooldown - age))

    if not settings.SENDER_GE_ENABLED and not demo_codes_enabled():
        cache.delete(claim)
        # Nobody could ever receive this code: fail loudly instead of pretending it was sent.
        logger.error("OTP not sent: SMS delivery is disabled and demo codes are off")
        raise ApiError("sms_failed", status=502)

    code = f"{secrets.randbelow(10**6):06d}"
    with translation.override(language or translation.get_language()):
        text = str(SMS_TEXT).format(code=code)
    try:
        provider_response = send_sms(phone, text)
    except SenderGeDeliveryUnknown:
        logger.warning("OTP send outcome unknown: the resend cooldown stays in place")
        raise ApiError("sms_failed", status=502) from None  # no usable code is stored
    except SenderGeError:
        cache.delete(claim)
        raise ApiError("sms_failed", status=502) from None  # no usable code is stored

    otp = PhoneVerificationCode(
        session_key=session_key, customer=customer, phone=phone, purpose=purpose,
        max_attempts=settings.PHONE_VERIFICATION_MAX_ATTEMPTS, expires_at=now + timedelta(seconds=ttl),
        provider_message_id=str(provider_response.get("messageId", ""))[:120]
        if isinstance(provider_response, dict) else "",
    )
    otp.set_code(code)
    with transaction.atomic():
        _pending(session_key, phone, purpose).update(status=PhoneVerificationCode.STATUS_EXPIRED)
        otp.save()
    cache.delete(claim)

    response = {"masked_phone": mask_phone(phone), "expires_in": ttl, "resend_in": cooldown}
    if demo_codes_enabled():
        response["demo_code"] = code
    return response


def verify_code(*, session_key, phone, purpose, code):
    """Return the verified code row, or raise otp_expired / otp_attempts_exceeded / otp_invalid.
    The attempt is counted before the check, under a row lock, so parallel guesses can't exceed
    max_attempts. Errors are raised after the transaction so the counter is saved."""
    error = None
    with transaction.atomic():
        otp = _pending(session_key, phone, purpose).select_for_update().order_by("-created_at").first()
        if otp is None:
            error = ApiError("otp_expired")
        elif otp.expires_at <= timezone.now():
            otp.status = PhoneVerificationCode.STATUS_EXPIRED
            error = ApiError("otp_expired")
        elif otp.attempts >= otp.max_attempts:
            otp.status = PhoneVerificationCode.STATUS_FAILED
            error = ApiError("otp_attempts_exceeded")
        else:
            otp.attempts += 1
            if otp.check_code(str(code or "")):
                otp.status = PhoneVerificationCode.STATUS_VERIFIED
                otp.verified_at = timezone.now()
            else:
                error = ApiError("otp_invalid", attempts_left=otp.max_attempts - otp.attempts)
        if otp is not None:
            otp.save(update_fields=["status", "attempts", "verified_at", "updated_at"])
    if error:
        raise error
    return otp
