import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Customer, LegalEntityProfile, PhoneVerificationCode
from .customer_sessions import attach_customer_session, get_customer_for_session
from .serializers import CustomerSerializer
from .sms import SenderGeError, send_sms


def normalize_georgian_phone(phone):
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())

    if digits.startswith("995"):
        digits = digits[3:]

    if len(digits) != 9 or not digits.startswith("5"):
        raise ValueError("customer_phone must be a Georgian mobile number")

    return digits


def generate_sms_code():
    return f"{secrets.randbelow(1000000):06d}"


def is_real_customer_name(name, phone):
    cleaned_name = str(name or "").strip()
    return bool(cleaned_name) and cleaned_name != phone


def get_existing_verified_customer_by_phone(phone):
    return (
        Customer.objects.filter(
            phone=phone,
            is_phone_verified=True,
        )
        .order_by("-updated_at")
        .first()
    )


@api_view(["GET"])
def get_profile(request):
    session_id = request.query_params.get("session_id", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer = get_customer_for_session(session_id)

    if not customer:
        return Response(
            {"detail": "profile not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = CustomerSerializer(customer)
    return Response(serializer.data)



LEGAL_ENTITY_REQUIRED_FIELDS = [
    "company_identification_code",
    "company_official_name",
    "legal_address",
    "contact_first_name",
    "contact_last_name",
    "email",
    "mobile_phone",
]


def clean_required_text(data, field_name):
    value = str(data.get(field_name, "") or "").strip()

    if not value:
        raise ValueError(f"{field_name} is required")

    return value


def clean_legal_entity_email(email):
    email = str(email or "").strip().lower()

    if not email:
        raise ValueError("email is required")

    try:
        validate_email(email)
    except ValidationError:
        raise ValueError("invalid email")

    return email


def build_legal_entity_payload(data):
    payload = {
        "company_identification_code": clean_required_text(
            data,
            "company_identification_code",
        ),
        "company_official_name": clean_required_text(
            data,
            "company_official_name",
        ),
        "legal_address": clean_required_text(data, "legal_address"),
        "contact_first_name": clean_required_text(data, "contact_first_name"),
        "contact_last_name": clean_required_text(data, "contact_last_name"),
        "email": clean_legal_entity_email(data.get("email")),
    }

    payload["mobile_phone"] = normalize_georgian_phone(
        clean_required_text(data, "mobile_phone")
    )

    return payload


@api_view(["POST", "PUT", "PATCH"])
def upsert_legal_entity_profile(request):
    session_id = request.data.get("session_id", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer = get_customer_for_session(session_id)

    if not customer or not customer.is_phone_verified:
        return Response(
            {"detail": "phone verification required"},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        payload = build_legal_entity_payload(request.data)
    except ValueError as exc:
        return Response(
            {"detail": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    duplicate_company = LegalEntityProfile.objects.filter(
        company_identification_code=payload["company_identification_code"],
    ).exclude(customer=customer).exists()

    if duplicate_company:
        return Response(
            {"detail": "company identification code already exists"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    duplicate_email = LegalEntityProfile.objects.filter(
        email__iexact=payload["email"],
    ).exclude(customer=customer).exists()

    if duplicate_email:
        return Response(
            {"detail": "email already exists"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    old_profile = getattr(customer, "legal_entity_profile", None)

    is_mobile_verified = payload["mobile_phone"] == customer.phone
    mobile_verified_at = timezone.now() if is_mobile_verified else None

    if old_profile and old_profile.mobile_phone == payload["mobile_phone"]:
        is_mobile_verified = old_profile.is_mobile_verified
        mobile_verified_at = old_profile.mobile_verified_at

    LegalEntityProfile.objects.update_or_create(
        customer=customer,
        defaults={
            **payload,
            "is_mobile_verified": is_mobile_verified,
            "mobile_verified_at": mobile_verified_at,
            "is_active": True,
        },
    )

    customer.refresh_from_db()

    serializer = CustomerSerializer(customer)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
def demo_login(request):
    session_id = request.data.get("session_id", "").strip()
    customer_name = request.data.get("customer_name", "").strip()
    customer_phone = request.data.get("customer_phone", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not customer_name:
        return Response(
            {"detail": "customer_name is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not customer_phone:
        return Response(
            {"detail": "customer_phone is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer, _created = Customer.objects.update_or_create(
        session_id=session_id,
        defaults={
            "name": customer_name,
            "phone": customer_phone,
            "is_phone_verified": False,
        },
    )

    serializer = CustomerSerializer(customer)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
def send_phone_verification_code(request):
    session_id = request.data.get("session_id", "").strip()
    customer_phone = request.data.get("customer_phone", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not customer_phone:
        return Response(
            {"detail": "customer_phone is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        normalized_phone = normalize_georgian_phone(customer_phone)
    except ValueError:
        return Response(
            {
                "detail": (
                    "ტელეფონის ნომერი უნდა იყოს ქართული მობილური ნომერი, "
                    "მაგ: 555123456 ან +995555123456"
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    resend_after = timezone.now() - timedelta(
        seconds=settings.PHONE_VERIFICATION_RESEND_SECONDS
    )

    recent_code = PhoneVerificationCode.objects.filter(
        session_id=session_id,
        phone=normalized_phone,
        purpose=PhoneVerificationCode.PURPOSE_LOGIN,
        status=PhoneVerificationCode.STATUS_PENDING,
        created_at__gte=resend_after,
    ).first()

    if recent_code:
        remaining_seconds = max(
            0,
            int((recent_code.expires_at - timezone.now()).total_seconds()),
        )

        return Response(
            {
                "detail": "verification code already sent",
                "phone": normalized_phone,
                "expires_in_seconds": remaining_seconds,
                "retry_after_seconds": settings.PHONE_VERIFICATION_RESEND_SECONDS,
                "already_sent": True,
            },
            status=status.HTTP_200_OK,
        )

    code = generate_sms_code()
    message = f"Lion Parts verification code: {code}"

    try:
        provider_response = send_sms(normalized_phone, message)
    except SenderGeError as exc:
        return Response(
            {"detail": str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    expires_at = timezone.now() + timedelta(
        minutes=settings.PHONE_VERIFICATION_CODE_TTL_MINUTES
    )

    with transaction.atomic():
        PhoneVerificationCode.objects.filter(
            session_id=session_id,
            phone=normalized_phone,
            purpose=PhoneVerificationCode.PURPOSE_LOGIN,
            status=PhoneVerificationCode.STATUS_PENDING,
        ).update(status=PhoneVerificationCode.STATUS_EXPIRED)

        verification = PhoneVerificationCode(
            session_id=session_id,
            phone=normalized_phone,
            purpose=PhoneVerificationCode.PURPOSE_LOGIN,
            status=PhoneVerificationCode.STATUS_PENDING,
            max_attempts=settings.PHONE_VERIFICATION_MAX_ATTEMPTS,
            expires_at=expires_at,
            sent_message_id=str(provider_response.get("messageId", "")),
            provider_response=provider_response,
        )
        verification.set_code(code)
        verification.save()

    response_data = {
        "detail": "verification code sent",
        "phone": normalized_phone,
        "expires_in_seconds": settings.PHONE_VERIFICATION_CODE_TTL_MINUTES * 60,
    }

    if not settings.SENDER_GE_ENABLED:
        response_data["demo_code"] = code

    return Response(response_data, status=status.HTTP_200_OK)


@api_view(["POST"])
def verify_phone_code(request):
    session_id = request.data.get("session_id", "").strip()
    customer_phone = request.data.get("customer_phone", "").strip()
    code = request.data.get("code", "").strip()
    customer_name = request.data.get("customer_name", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not customer_phone:
        return Response(
            {"detail": "customer_phone is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not code:
        return Response(
            {"detail": "code is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        normalized_phone = normalize_georgian_phone(customer_phone)
    except ValueError:
        return Response(
            {"detail": "invalid customer_phone"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    verification = PhoneVerificationCode.objects.filter(
        session_id=session_id,
        phone=normalized_phone,
        purpose=PhoneVerificationCode.PURPOSE_LOGIN,
        status=PhoneVerificationCode.STATUS_PENDING,
    ).first()

    if not verification:
        return Response(
            {"detail": "verification code not found"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if verification.is_expired():
        verification.status = PhoneVerificationCode.STATUS_EXPIRED
        verification.save(update_fields=["status"])

        return Response(
            {"detail": "verification code expired"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if verification.attempts >= verification.max_attempts:
        verification.status = PhoneVerificationCode.STATUS_FAILED
        verification.save(update_fields=["status"])

        return Response(
            {"detail": "too many attempts"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    verification.attempts += 1

    if not verification.check_code(code):
        if verification.attempts >= verification.max_attempts:
            verification.status = PhoneVerificationCode.STATUS_FAILED

        verification.save(update_fields=["attempts", "status"])

        return Response(
            {"detail": "invalid verification code"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    existing_phone_customer = get_existing_verified_customer_by_phone(normalized_phone)
    existing_session_customer = Customer.objects.filter(session_id=session_id).first()

    final_customer_name = customer_name

    if (
        not is_real_customer_name(final_customer_name, normalized_phone)
        and existing_phone_customer
        and is_real_customer_name(existing_phone_customer.name, normalized_phone)
    ):
        final_customer_name = existing_phone_customer.name

    if (
        not is_real_customer_name(final_customer_name, normalized_phone)
        and existing_session_customer
        and existing_session_customer.phone == normalized_phone
        and is_real_customer_name(existing_session_customer.name, normalized_phone)
    ):
        final_customer_name = existing_session_customer.name

    if not is_real_customer_name(final_customer_name, normalized_phone):
        verification.save(update_fields=["attempts"])

        return Response(
            {
                "detail": "customer name is required",
                "phone": normalized_phone,
                "requires_customer_name": True,
            },
            status=status.HTTP_200_OK,
        )

    verification.status = PhoneVerificationCode.STATUS_VERIFIED
    verification.verified_at = timezone.now()
    verification.save(update_fields=["status", "verified_at", "attempts"])

    if existing_phone_customer:
        customer = existing_phone_customer
        customer.session_id = session_id
        customer.name = final_customer_name
        customer.phone = normalized_phone
        customer.is_phone_verified = True
        customer.save(
            update_fields=[
                "session_id",
                "name",
                "phone",
                "is_phone_verified",
                "updated_at",
            ]
        )
    else:
        customer, _created = Customer.objects.update_or_create(
            phone=normalized_phone,
            defaults={
                "session_id": session_id,
                "name": final_customer_name,
                "is_phone_verified": True,
            },
        )

    attach_customer_session(customer, session_id)

    serializer = CustomerSerializer(customer)
    return Response(serializer.data, status=status.HTTP_200_OK)

def get_customer_password_errors(password):
    password = str(password or "")
    errors = []

    if len(password) < 8:
        errors.append("password must be at least 8 characters long")

    if not any(ch.isupper() for ch in password):
        errors.append("password must contain at least one uppercase letter")

    if not any(ch.islower() for ch in password):
        errors.append("password must contain at least one lowercase letter")

    if not any(ch.isdigit() for ch in password):
        errors.append("password must contain at least one digit")

    if not any(not ch.isalnum() for ch in password):
        errors.append("password must contain at least one symbol")

    return errors


@api_view(["POST"])
def set_profile_password(request):
    session_id = request.data.get("session_id", "").strip()
    current_password = request.data.get("current_password", "")
    new_password = request.data.get("new_password") or request.data.get("password") or ""

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer = get_customer_for_session(session_id)

    if not customer or not customer.is_phone_verified:
        return Response(
            {"detail": "phone verification required"},
            status=status.HTTP_403_FORBIDDEN,
        )

    if customer.has_password:
        if not current_password:
            return Response(
                {"detail": "current password is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not customer.check_password(current_password):
            return Response(
                {"detail": "current password is incorrect"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    errors = get_customer_password_errors(new_password)

    if errors:
        return Response(
            {"password": errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer.set_password(new_password)

    serializer = CustomerSerializer(customer)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
def login_with_password(request):
    session_id = request.data.get("session_id", "").strip()
    customer_phone = request.data.get("customer_phone", "").strip()
    password = request.data.get("password", "")

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not customer_phone:
        return Response(
            {"detail": "customer_phone is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not password:
        return Response(
            {"detail": "password is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        normalized_phone = normalize_georgian_phone(customer_phone)
    except ValueError:
        return Response(
            {"detail": "invalid customer_phone"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer = Customer.objects.filter(
        phone=normalized_phone,
        is_phone_verified=True,
    ).first()

    if not customer or not customer.has_password:
        return Response(
            {"detail": "password login is not enabled for this account"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not customer.check_password(password):
        return Response(
            {"detail": "invalid phone or password"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    attach_customer_session(customer, session_id)

    serializer = CustomerSerializer(customer)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
def send_password_reset_code(request):
    session_id = request.data.get("session_id", "").strip()
    customer_phone = request.data.get("customer_phone", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not customer_phone:
        return Response(
            {"detail": "customer_phone is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        normalized_phone = normalize_georgian_phone(customer_phone)
    except ValueError:
        return Response(
            {"detail": "invalid customer_phone"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer = Customer.objects.filter(
        phone=normalized_phone,
        is_phone_verified=True,
    ).first()

    if not customer:
        return Response(
            {"detail": "verified customer not found"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    resend_after = timezone.now() - timedelta(
        seconds=settings.PHONE_VERIFICATION_RESEND_SECONDS
    )

    recent_code = PhoneVerificationCode.objects.filter(
        session_id=session_id,
        phone=normalized_phone,
        purpose=PhoneVerificationCode.PURPOSE_PASSWORD_RESET,
        status=PhoneVerificationCode.STATUS_PENDING,
        created_at__gte=resend_after,
    ).first()

    if recent_code:
        remaining_seconds = max(
            0,
            int((recent_code.expires_at - timezone.now()).total_seconds()),
        )

        return Response(
            {
                "detail": "verification code already sent",
                "phone": normalized_phone,
                "expires_in_seconds": remaining_seconds,
                "retry_after_seconds": settings.PHONE_VERIFICATION_RESEND_SECONDS,
                "already_sent": True,
            },
            status=status.HTTP_200_OK,
        )

    code = generate_sms_code()
    message = f"Lion Parts password reset code: {code}"

    try:
        provider_response = send_sms(normalized_phone, message)
    except SenderGeError as exc:
        return Response(
            {"detail": str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    expires_at = timezone.now() + timedelta(
        minutes=settings.PHONE_VERIFICATION_CODE_TTL_MINUTES
    )

    with transaction.atomic():
        PhoneVerificationCode.objects.filter(
            session_id=session_id,
            phone=normalized_phone,
            purpose=PhoneVerificationCode.PURPOSE_PASSWORD_RESET,
            status=PhoneVerificationCode.STATUS_PENDING,
        ).update(status=PhoneVerificationCode.STATUS_EXPIRED)

        verification = PhoneVerificationCode(
            session_id=session_id,
            phone=normalized_phone,
            purpose=PhoneVerificationCode.PURPOSE_PASSWORD_RESET,
            status=PhoneVerificationCode.STATUS_PENDING,
            max_attempts=settings.PHONE_VERIFICATION_MAX_ATTEMPTS,
            expires_at=expires_at,
            sent_message_id=str(provider_response.get("messageId", "")),
            provider_response=provider_response,
        )
        verification.set_code(code)
        verification.save()

    response_data = {
        "detail": "verification code sent",
        "phone": normalized_phone,
        "expires_in_seconds": settings.PHONE_VERIFICATION_CODE_TTL_MINUTES * 60,
    }

    if not settings.SENDER_GE_ENABLED:
        response_data["demo_code"] = code

    return Response(response_data, status=status.HTTP_200_OK)


@api_view(["POST"])
def reset_password(request):
    session_id = request.data.get("session_id", "").strip()
    customer_phone = request.data.get("customer_phone", "").strip()
    code = request.data.get("code", "").strip()
    new_password = request.data.get("new_password") or request.data.get("password") or ""

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not customer_phone:
        return Response(
            {"detail": "customer_phone is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not code:
        return Response(
            {"detail": "code is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        normalized_phone = normalize_georgian_phone(customer_phone)
    except ValueError:
        return Response(
            {"detail": "invalid customer_phone"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    errors = get_customer_password_errors(new_password)

    if errors:
        return Response(
            {"password": errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    verification = PhoneVerificationCode.objects.filter(
        session_id=session_id,
        phone=normalized_phone,
        purpose=PhoneVerificationCode.PURPOSE_PASSWORD_RESET,
        status=PhoneVerificationCode.STATUS_PENDING,
    ).first()

    if not verification:
        return Response(
            {"detail": "verification code not found"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if verification.is_expired():
        verification.status = PhoneVerificationCode.STATUS_EXPIRED
        verification.save(update_fields=["status"])

        return Response(
            {"detail": "verification code expired"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if verification.attempts >= verification.max_attempts:
        verification.status = PhoneVerificationCode.STATUS_FAILED
        verification.save(update_fields=["status"])

        return Response(
            {"detail": "too many attempts"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    verification.attempts += 1

    if not verification.check_code(code):
        if verification.attempts >= verification.max_attempts:
            verification.status = PhoneVerificationCode.STATUS_FAILED

        verification.save(update_fields=["attempts", "status"])

        return Response(
            {"detail": "invalid verification code"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer = Customer.objects.filter(
        phone=normalized_phone,
        is_phone_verified=True,
    ).first()

    if not customer:
        return Response(
            {"detail": "verified customer not found"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    verification.status = PhoneVerificationCode.STATUS_VERIFIED
    verification.verified_at = timezone.now()
    verification.save(update_fields=["status", "verified_at", "attempts"])

    customer.set_password(new_password)
    attach_customer_session(customer, session_id)

    serializer = CustomerSerializer(customer)
    return Response(serializer.data, status=status.HTTP_200_OK)
