"""Account services (G§3.1): views and admin call these, never the other way around."""

from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.contrib.auth.hashers import make_password
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import serializers

from api.errors import ApiError
from api.mail import frontend_link, send_templated_email

from . import otp
from .models import Customer, LegalEntityProfile


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


# Login (G§5.7): read the guest session key before login() rotates it, then attach guest data.

def attach_guest_data(old_session_key, customer):
    """Hook: guest search logs (LP-BE-407), guest cart merge (LP-BE-506), support tickets (LP-BE-1001)."""


def login_customer(request, customer):
    old_key = request.session.session_key
    login(request, customer.user, backend="django.contrib.auth.backends.ModelBackend")
    attach_guest_data(old_key, customer)


def can_log_in(user):
    """Only active, non-anonymized customers use the customer API; staff have no Customer."""
    customer = getattr(user, "customer", None) if user is not None else None
    return bool(user and user.is_active and customer and customer.anonymized_at is None)


# Registration step 2 (G§5.7)

def create_customer_from_draft(draft):
    """Create User + Customer (+ company) from a verified draft. A duplicate that appeared since
    step 1 (another registration won the race) -> 409 email_taken."""
    from .serializers import email_is_taken, phone_is_taken  # serializers import services

    now = timezone.now()
    try:
        with transaction.atomic():
            if email_is_taken(draft["email"]) or phone_is_taken(draft["phone"]) or (
                draft.get("personal_id") and Customer.objects.filter(personal_id=draft["personal_id"]).exists()
            ) or (
                draft["account_type"] == Customer.TYPE_LEGAL and LegalEntityProfile.objects.filter(
                    company_identification_code=draft["company_identification_code"]).exists()
            ):
                raise IntegrityError("duplicate")
            user = get_user_model()(username=draft["email"], email=draft["email"],
                                    first_name=draft["first_name"], last_name=draft["last_name"])
            user.password = draft["password"]  # already hashed in step 1
            user.save()
            customer = Customer.objects.create(
                user=user, account_type=draft["account_type"], first_name=draft["first_name"],
                last_name=draft["last_name"], personal_id=draft.get("personal_id") or None, phone=draft["phone"],
                is_phone_verified=True, phone_verified_at=now, terms_accepted_at=now,
                marketing_opt_in=draft["marketing_opt_in"], preferred_language=draft["preferred_language"],
            )
            if customer.account_type == Customer.TYPE_LEGAL:
                LegalEntityProfile.objects.create(
                    customer=customer, company_official_name=draft["company_official_name"],
                    company_identification_code=draft["company_identification_code"],
                )
    except IntegrityError:
        raise ApiError("email_taken", status=409) from None
    return customer


# Email verification (G§5.7, LP-BE-208)

EMAIL_VERIFY_SALT = "email-verify"


def send_email_verification(customer):
    user = customer.user
    token = signing.dumps({"u": user.pk, "e": user.email}, salt=EMAIL_VERIFY_SALT)
    language = customer_language(customer)
    link = frontend_link("/verify-email", language, token=token)
    return send_templated_email(user.email, "verify_email", {"name": customer.first_name, "link": link}, language)


EMAIL_TOKEN_MAX_AGE = 48 * 3600


def verify_email_token(token):
    """Mark the email verified. Invalid: tampered, older than 48 h, unknown/inactive user, or issued
    for an address the user no longer has (email changed since)."""
    try:
        data = signing.loads(str(token or ""), salt=EMAIL_VERIFY_SALT, max_age=EMAIL_TOKEN_MAX_AGE)
        customer = Customer.objects.select_related("user").get(user__pk=data["u"], user__is_active=True)
    except (signing.BadSignature, Customer.DoesNotExist, KeyError, TypeError):
        raise ApiError("token_invalid") from None
    if customer.user.email != data["e"] or customer.anonymized_at:
        raise ApiError("token_invalid")
    if not customer.is_email_verified:
        customer.is_email_verified = True
        customer.email_verified_at = timezone.now()
        customer.save(update_fields=["is_email_verified", "email_verified_at", "updated_at"])
    return customer


# Password reset (G§5.7): Django's token generator; single use because the hash covers the password.

def request_password_reset(email):
    """Send a reset link if an active customer has this email. Callers always answer the same way."""
    customer = Customer.objects.select_related("user").filter(
        user__email__iexact=email.strip(), user__is_active=True, anonymized_at__isnull=True,
    ).first()
    if customer is None:
        return
    user = customer.user
    language = customer_language(customer)
    link = frontend_link("/reset-password", language, uid=urlsafe_base64_encode(force_bytes(user.pk)),
                         token=default_token_generator.make_token(user))
    send_templated_email(user.email, "password_reset", {"name": customer.first_name, "link": link}, language)


def reset_password_user(uid, token):
    """The user a valid (uid, token) pair belongs to, else token_invalid."""
    try:
        user = get_user_model().objects.get(pk=urlsafe_base64_decode(str(uid)).decode())
    except (ValueError, TypeError, OverflowError, get_user_model().DoesNotExist):
        user = None
    if user is None or not can_log_in(user) or not default_token_generator.check_token(user, str(token)):
        raise ApiError("token_invalid")
    return user


# Email change (G§5.7): nothing changes until the new address confirms.

EMAIL_CHANGE_SALT = "email-change"


def request_email_change(customer, new_email):
    user = customer.user
    # "o" (the address being replaced) makes the link die if the email changes some other way first.
    token = signing.dumps({"u": user.pk, "e": new_email, "o": user.email}, salt=EMAIL_CHANGE_SALT)
    language = customer_language(customer)
    link = frontend_link("/profile/email/confirm", language, token=token)
    send_templated_email(new_email, "email_change_confirm", {"name": customer.first_name, "link": link}, language)


def confirm_email_change(token):
    from .serializers import email_is_taken  # serializers import services

    try:
        data = signing.loads(str(token or ""), salt=EMAIL_CHANGE_SALT, max_age=EMAIL_TOKEN_MAX_AGE)
        customer = Customer.objects.select_related("user").get(user__pk=data["u"], user__is_active=True)
        new_email, old_email = data["e"], data["o"]
    except (signing.BadSignature, Customer.DoesNotExist, KeyError, TypeError):
        raise ApiError("token_invalid") from None
    user = customer.user
    if customer.anonymized_at or user.email not in (old_email, new_email):
        raise ApiError("token_invalid")
    if user.email == new_email:
        return customer  # already confirmed: harmless repeat
    try:
        with transaction.atomic():
            if email_is_taken(new_email):
                raise IntegrityError("taken")
            user.email = user.username = new_email
            user.save(update_fields=["email", "username"])  # unique username also catches a parallel confirm
            customer.is_email_verified, customer.email_verified_at = True, timezone.now()
            customer.save(update_fields=["is_email_verified", "email_verified_at", "updated_at"])
    except IntegrityError:
        raise ApiError("email_taken", status=409) from None
    language = customer_language(customer)
    send_templated_email(old_email, "email_changed_notice", {"name": customer.first_name, "new_email": new_email},
                         language)
    return customer


# Phone change (G§8.3): the phone changes only after the new number confirms the OTP.

PENDING_PHONE_KEY = "pending_phone"


def start_phone_change(request, customer, phone):
    response = otp.send_code(session_key=ensure_session(request), phone=phone, purpose="phone_change",
                             customer=customer, language=customer_language(customer))
    request.session[PENDING_PHONE_KEY] = phone
    return response


def confirm_phone_change(request, customer, code):
    phone = request.session.get(PENDING_PHONE_KEY)
    if not phone:
        raise ApiError("otp_expired")
    otp.verify_code(session_key=request.session.session_key, phone=phone, purpose="phone_change", code=code)
    try:
        with transaction.atomic():
            customer.phone, customer.is_phone_verified, customer.phone_verified_at = phone, True, timezone.now()
            customer.save(update_fields=["phone", "is_phone_verified", "phone_verified_at", "updated_at"])
    except IntegrityError:  # another account took the number after the code was sent
        from .serializers import taken
        raise serializers.ValidationError({"phone": [taken("phone").detail[0]]}) from None
    request.session.pop(PENDING_PHONE_KEY, None)
    return customer
