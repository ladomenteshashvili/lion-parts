"""Phone OTP (G§5.7): 6 digits, stored hashed, TTL, resend cooldown per (session, phone, purpose),
max attempts per code. Codes and API keys are never logged."""

import logging
import math
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _

from api.errors import ApiError

from .models import PhoneVerificationCode
from .sms import SenderGeError, send_sms

SMS_TEXT = _("Lion Parts code: {code}")

logger = logging.getLogger(__name__)


def demo_codes_enabled():
    return settings.SMS_DEMO_MODE and (settings.DEBUG or settings.ENABLE_DEMO_ENDPOINTS)


def mask_phone(phone):
    return f"+995 {phone[:3]} •• •• {phone[-2:]}"


def _pending(session_key, phone, purpose):
    return PhoneVerificationCode.objects.filter(
        session_key=session_key, phone=phone, purpose=purpose, status=PhoneVerificationCode.STATUS_PENDING,
    )


def send_code(*, session_key, phone, purpose, customer=None, language=None):
    now = timezone.now()
    cooldown = settings.PHONE_VERIFICATION_RESEND_SECONDS
    ttl = settings.PHONE_VERIFICATION_CODE_TTL_MINUTES * 60

    latest = _pending(session_key, phone, purpose).order_by("-created_at").first()
    if latest and (age := (now - latest.created_at).total_seconds()) < cooldown:
        raise ApiError("otp_resend_too_soon", status=409, resend_in=math.ceil(cooldown - age))

    if not settings.SENDER_GE_ENABLED and not demo_codes_enabled():
        # Nobody could ever receive this code: fail loudly instead of pretending it was sent.
        logger.error("OTP not sent: SMS delivery is disabled and demo codes are off")
        raise ApiError("sms_failed", status=502)

    code = f"{secrets.randbelow(10**6):06d}"
    with translation.override(language or translation.get_language()):
        text = str(SMS_TEXT).format(code=code)
    try:
        provider_response = send_sms(phone, text)
    except SenderGeError:
        raise ApiError("sms_failed", status=502) from None  # no usable code is stored

    _pending(session_key, phone, purpose).update(status=PhoneVerificationCode.STATUS_EXPIRED)
    otp = PhoneVerificationCode(
        session_key=session_key, customer=customer, phone=phone, purpose=purpose,
        max_attempts=settings.PHONE_VERIFICATION_MAX_ATTEMPTS, expires_at=now + timedelta(seconds=ttl),
        provider_message_id=str(provider_response.get("messageId", ""))[:120]
        if isinstance(provider_response, dict) else "",
    )
    otp.set_code(code)
    otp.save()

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
