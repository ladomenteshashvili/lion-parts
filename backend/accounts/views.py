"""Auth and profile endpoints (G§8.2, G§8.3). Thin views: validation in serializers, logic in services."""

from django.contrib.auth import authenticate, logout, update_session_auth_hash
from django.middleware.csrf import get_token
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from api.errors import ApiError
from api.throttles import (
    EmailLoginThrottle, EmailSendThrottle, LoginIpThrottle, OtpSendIpThrottle, OtpVerifyThrottle,
    PasswordResetThrottle, PhoneThrottle, RegisterThrottle,
)

from . import otp, services
from .serializers import (
    CodeSerializer, CustomerSerializer, DeleteAccountSerializer, EmailChangeSerializer, LoginSerializer, PasswordChangeSerializer, PasswordResetConfirmSerializer,
    PasswordResetSerializer, PhoneChangeSerializer, ProfileUpdateSerializer, RegisterSerializer, TokenSerializer, check_password_rules,
)


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([RegisterThrottle, PhoneThrottle, OtpSendIpThrottle])
def register(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    services.ensure_session(request)
    draft = services.save_registration_draft(request, serializer.validated_data)
    return Response(services.send_registration_code(request, draft))


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([PhoneThrottle, OtpSendIpThrottle])
def register_resend_code(request):
    draft = services.load_registration_draft(request)
    return Response(services.send_registration_code(request, draft))


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([OtpVerifyThrottle])
def register_verify_phone(request):
    draft = services.load_registration_draft(request)
    serializer = CodeSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    otp.verify_code(session_key=request.session.session_key, phone=draft["phone"], purpose="registration",
                    code=serializer.validated_data["code"])  # a wrong code leaves the draft for a retry
    customer = services.create_customer_from_draft(draft)
    services.login_customer(request, customer)
    request.session.pop(services.DRAFT_KEY, None)
    sent = services.send_email_verification(customer)
    return Response({"customer": CustomerSerializer(customer).data, "email_verification_sent": sent,
                     "csrf_token": get_token(request)}, status=201)

@ensure_csrf_cookie
@api_view(["GET"])
@permission_classes([AllowAny])
def csrf(request):
    return Response({"csrf_token": get_token(request)})


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([LoginIpThrottle, EmailLoginThrottle])
def login_view(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = authenticate(request, username=serializer.validated_data["email"].strip().lower(),
                        password=serializer.validated_data["password"])
    if not services.can_log_in(user):
        raise ApiError("invalid_credentials")  # same body for every failure (no account enumeration)
    services.login_customer(request, user.customer)
    return Response({"customer": CustomerSerializer(user.customer).data, "csrf_token": get_token(request)})


@api_view(["POST"])
def logout_view(request):
    logout(request)
    return Response({"csrf_token": get_token(request)})


@api_view(["GET"])
@permission_classes([AllowAny])
def me(request):
    customer = getattr(request.user, "customer", None) if services.can_log_in(request.user) else None
    return Response({
        "authenticated": customer is not None,
        "customer": CustomerSerializer(customer).data if customer else None,
        "cart_count": 0,  # filled by the cart (LP-BE-503)
        "badges": None,   # filled by notifications (LP-BE-904)
    })


@api_view(["POST"])
@throttle_classes([EmailSendThrottle])
def email_send_verification(request):
    customer = request.user.customer
    if customer.is_email_verified:
        raise ApiError("already_verified", status=409)
    services.send_email_verification(customer)
    return Response({"sent_to": customer.user.email, "resend_in": 60})


@api_view(["POST"])
@permission_classes([AllowAny])
def email_verify(request):
    serializer = TokenSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    customer = services.verify_email_token(serializer.validated_data["token"])
    own = request.user.is_authenticated and request.user.pk == customer.user_id
    return Response({"verified": True, "customer": CustomerSerializer(customer).data if own else None})


RESET_SENT = _("If the account exists, a reset link has been sent.")
PASSWORD_CHANGED = _("Password changed.")


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([PasswordResetThrottle])
def password_reset(request):
    serializer = PasswordResetSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    services.request_password_reset(serializer.validated_data["email"])
    return Response({"detail": str(RESET_SENT)})  # identical for known and unknown emails


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([PasswordResetThrottle])
def password_reset_confirm(request):
    serializer = PasswordResetConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    user = services.reset_password_user(data["uid"], data["token"])
    try:
        check_password_rules(data["new_password"], user.email, user.first_name, user.last_name)
    except serializers.ValidationError as exc:
        raise serializers.ValidationError({"new_password": exc.detail}) from None
    user.set_password(data["new_password"])  # rotates the session hash: other sessions end
    user.save(update_fields=["password"])
    return Response({"detail": str(PASSWORD_CHANGED)})


@api_view(["GET", "PATCH", "DELETE"])
def profile(request):
    customer = request.user.customer
    if request.method == "DELETE":
        serializer = DeleteAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not request.user.check_password(serializer.validated_data["current_password"]):
            raise ApiError("invalid_credentials")
        services.anonymize_customer(customer)
        logout(request)
        return Response(status=204)
    if request.method == "PATCH":
        serializer = ProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(customer, field, value)
        customer.save(update_fields=[*serializer.validated_data, "updated_at"])
    return Response(CustomerSerializer(customer).data)


@api_view(["POST"])
def profile_password(request):
    serializer = PasswordChangeSerializer(data=request.data, context={"user": request.user})
    serializer.is_valid(raise_exception=True)
    request.user.set_password(serializer.validated_data["new_password"])
    request.user.save(update_fields=["password"])
    update_session_auth_hash(request, request.user)  # keep this session; the others end
    return Response({"customer": CustomerSerializer(request.user.customer).data, "csrf_token": get_token(request)})


@api_view(["POST"])
@throttle_classes([EmailSendThrottle])
def profile_email(request):
    serializer = EmailChangeSerializer(data=request.data, context={"user": request.user})
    serializer.is_valid(raise_exception=True)
    new_email = serializer.validated_data["new_email"]
    services.request_email_change(request.user.customer, new_email)
    return Response({"sent_to": new_email, "resend_in": 60})


@api_view(["POST"])
@permission_classes([AllowAny])
def profile_email_confirm(request):
    serializer = TokenSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    customer = services.confirm_email_change(serializer.validated_data["token"])
    return Response({"email": customer.user.email})


@api_view(["POST"])
@throttle_classes([PhoneThrottle, OtpSendIpThrottle])
def profile_phone(request):
    serializer = PhoneChangeSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return Response(services.start_phone_change(request, request.user.customer, serializer.validated_data["phone"]))


@api_view(["POST"])
@throttle_classes([OtpVerifyThrottle])
def profile_phone_verify(request):
    serializer = CodeSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    customer = services.confirm_phone_change(request, request.user.customer, serializer.validated_data["code"])
    return Response(CustomerSerializer(customer).data)
