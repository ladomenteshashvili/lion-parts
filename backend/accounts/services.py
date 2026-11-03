"""Account services (G§3.1): views and admin call these, never the other way around."""

from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.contrib.auth.hashers import make_password
from django.core import signing
from django.db import IntegrityError, transaction
from django.utils import timezone

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
