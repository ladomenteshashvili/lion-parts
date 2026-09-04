import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Customer, PhoneVerificationCode
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


@api_view(["GET"])
def get_profile(request):
    session_id = request.query_params.get("session_id", "").strip()

    if not session_id:
        return Response(
            {"detail": "session_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    customer = Customer.objects.filter(session_id=session_id).first()

    if not customer:
        return Response(
            {"detail": "profile not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = CustomerSerializer(customer)
    return Response(serializer.data)


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

    verification.status = PhoneVerificationCode.STATUS_VERIFIED
    verification.verified_at = timezone.now()
    verification.save(update_fields=["status", "verified_at", "attempts"])

    existing_customer = Customer.objects.filter(session_id=session_id).first()
    final_customer_name = customer_name

    if not final_customer_name and existing_customer:
        final_customer_name = existing_customer.name

    if not final_customer_name:
        final_customer_name = normalized_phone

    customer, _created = Customer.objects.update_or_create(
        session_id=session_id,
        defaults={
            "name": final_customer_name,
            "phone": normalized_phone,
            "is_phone_verified": True,
        },
    )

    serializer = CustomerSerializer(customer)
    return Response(serializer.data, status=status.HTTP_200_OK)