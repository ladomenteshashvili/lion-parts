"""LP-BE-102 (LPK-97): error envelope, ApiError and translated messages (G§4.4, G§5.6, G§9)."""

import json
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from django.test import Client, RequestFactory, SimpleTestCase, override_settings
from django.urls import get_resolver, path
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.throttling import BaseThrottle

from api.errors import MESSAGES, ApiError
from api.exceptions import _flatten

# Every code of the G§9 table (kept literal so a removed code fails the test).
G9_CODES = [
    "validation_error", "invalid_credentials", "otp_invalid", "otp_expired", "otp_attempts_exceeded",
    "registration_expired", "token_invalid", "invalid_callback", "not_authenticated", "csrf_failed",
    "verification_required", "phone_verification_required", "weight_entry_not_allowed",
    "quote_request_not_allowed", "permission_denied", "not_found", "email_taken", "already_verified",
    "otp_resend_too_soon", "offer_expired", "offer_not_ready", "cart_empty", "cart_invalid",
    "no_pending_action", "not_cancellable", "nothing_to_pay", "payment_in_progress",
    "account_has_open_orders", "throttled", "supplier_unavailable", "rate_unavailable", "sms_failed",
    "payment_init_failed",
]
KA_OTP_INVALID = "\u10d9\u10dd\u10d3\u10d8 \u10d0\u10e0\u10d0\u10e1\u10ec\u10dd\u10e0\u10d8\u10d0."
KA_VALIDATION = ("\u10e8\u10d4\u10d0\u10db\u10dd\u10ec\u10db\u10d4\u10d7 \u10e8\u10d4\u10d5\u10e1\u10d4"
                 "\u10d1\u10e3\u10da\u10d8 \u10d5\u10d4\u10da\u10d4\u10d1\u10d8.")
KA_REQUIRED = "\u10d5\u10d4\u10da\u10d8 \u10e1\u10d0\u10d5\u10d0\u10da\u10d3\u10d4\u10d1\u10e3\u10da\u10dd\u10d0."
KA_CSRF = ("\u10e3\u10e1\u10d0\u10e4\u10e0\u10d7\u10ee\u10dd\u10d4\u10d1\u10d8\u10e1 \u10e8\u10d4\u10db\u10dd"
           "\u10ec\u10db\u10d4\u10d1\u10d0 \u10d5\u10d4\u10e0 \u10db\u10dd\u10ee\u10d4\u10e0\u10ee\u10d3\u10d0. "
           "\u10d2\u10d0\u10dc\u10d0\u10d0\u10ee\u10da\u10d4\u10d7 \u10d2\u10d5\u10d4\u10e0\u10d3\u10d8.")


class DeliverySerializer(serializers.Serializer):
    city = serializers.CharField()
    district = serializers.CharField()


class LineSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1)


class CheckoutLikeSerializer(serializers.Serializer):
    delivery = DeliverySerializer()
    lines = LineSerializer(many=True, required=False)
    terms_accepted = serializers.BooleanField()

    def validate(self, attrs):
        if not attrs["terms_accepted"]:
            raise serializers.ValidationError("terms", code="required")
        return attrs


class AlwaysThrottled(BaseThrottle):
    def allow_request(self, request, view):
        return False

    def wait(self):
        return 4.2


class NeedsPhone(BasePermission):
    message = MESSAGES["phone_verification_required"]
    code = "phone_verification_required"

    def has_permission(self, request, view):
        return False


@api_view(["POST"])
@permission_classes([AllowAny])
def validate_view(request):
    CheckoutLikeSerializer(data=request.data).is_valid(raise_exception=True)
    return Response({})


def make_view(exc, methods=("GET",), **decorators):
    @api_view(list(methods))
    @permission_classes(decorators.get("permissions", [AllowAny]))
    @throttle_classes(decorators.get("throttles", []))
    def view(request):
        raise exc()
    return view


urlpatterns = [
    path("validate/", validate_view),
    path("otp/", make_view(lambda: ApiError("otp_invalid", attempts_left=2))),
    path("cart/", make_view(lambda: ApiError("cart_invalid", status=409,
                                             reasons=[{"item_id": 32, "code": "expired"}]))),
    path("throttled/", make_view(lambda: None, throttles=[AlwaysThrottled])),
    path("http404/", make_view(lambda: Http404("No Order matches the given query."))),
    path("forbidden/", make_view(lambda: DjangoPermissionDenied("secret reason"))),
    path("auth/", make_view(lambda: None, permissions=[IsAuthenticated])),
    path("phone/", make_view(lambda: None, permissions=[NeedsPhone])),
    path("parse/", validate_view),
]


@override_settings(ROOT_URLCONF=__name__)
class EnvelopeTests(SimpleTestCase):
    def post(self, url, body, lang="ka"):
        return self.client.post(url, body, content_type="application/json", headers={"accept-language": lang})

    def get(self, url, lang="ka"):
        return self.client.get(url, headers={"accept-language": lang})

    def test_nested_validation_error_uses_dot_paths(self):
        response = self.post("/validate/", {"delivery": {"city": "tbilisi"}, "terms_accepted": True})
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["detail"], KA_VALIDATION)
        self.assertEqual(body["fields"], {"delivery.district": [{"code": "required", "message": KA_REQUIRED}]})

    def test_list_and_non_field_errors(self):
        response = self.post("/validate/", {"delivery": {"city": "a", "district": "b"},
                                             "lines": [{"quantity": 1}], "terms_accepted": False}, lang="en")
        fields = response.json()["fields"]
        self.assertEqual(list(fields), ["non_field_errors"])
        self.assertEqual(fields["non_field_errors"][0]["code"], "required")
        response = self.post("/validate/", {"delivery": {"city": "a", "district": "b"},
                                             "lines": [{"quantity": 1}, {"quantity": 0}], "terms_accepted": True},
                             lang="en")
        self.assertEqual(response.json()["fields"]["lines.1.quantity"][0]["code"], "min_value")

    def test_api_error_detail_follows_request_language(self):
        expected = {"ka": KA_OTP_INVALID, "en": "The code is incorrect.", "ru": "Неверный код."}
        for lang, detail in expected.items():
            with self.subTest(lang=lang):
                response = self.get("/otp/", lang)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"code": "otp_invalid", "detail": detail, "attempts_left": 2})

    def test_api_error_keeps_status_and_typed_extras(self):
        response = self.get("/cart/", "en")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["reasons"], [{"item_id": 32, "code": "expired"}])

    def test_throttled_has_integer_retry_after(self):
        response = self.get("/throttled/", "en")
        self.assertEqual(response.status_code, 429)
        body = response.json()
        self.assertEqual(body["code"], "throttled")
        self.assertEqual(body["retry_after"], 5)  # ceil(4.2): never earlier than allowed
        self.assertEqual(body["detail"], "Too many attempts. Please try again later.")

    def test_django_404_and_permission_denied_are_mapped_without_leaking_reasons(self):
        response = self.get("/http404/", "en")
        self.assertEqual((response.status_code, response.json()), (404, {"code": "not_found", "detail": "Not found."}))
        response = self.get("/forbidden/", "en")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"code": "permission_denied", "detail": "This action is not allowed."})

    def test_not_authenticated_and_permission_code(self):
        response = self.get("/auth/", "en")
        self.assertEqual(response.json(), {"code": "not_authenticated", "detail": "Please sign in."})
        # A failed permission is 401 for anonymous users (DRF); the permission's code shows once signed in.
        client = APIClient()
        client.force_authenticate(user=SimpleNamespace(is_authenticated=True, is_active=True))
        response = client.get("/phone/", headers={"accept-language": "ru"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"code": "phone_verification_required",
                                           "detail": "Необходимо подтвердить телефон."})

    def test_parse_error_and_method_not_allowed(self):
        response = self.client.post("/parse/", "{not json", content_type="application/json",
                                    headers={"accept-language": "en"})
        self.assertEqual(response.json(), {"code": "parse_error", "detail": "Malformed request."})
        response = self.client.post("/otp/", {}, content_type="application/json", headers={"accept-language": "en"})
        self.assertEqual((response.status_code, response.json()["code"]), (405, "method_not_allowed"))


class DjangoLevelErrorTests(SimpleTestCase):
    def test_missing_csrf_token_returns_json_csrf_failed(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post("/admin/login/", {"username": "x", "password": "y"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json(), {"code": "csrf_failed", "detail": KA_CSRF})

    @override_settings(DEBUG=False)
    def test_unknown_url_returns_json_404(self):
        response = self.client.get("/no-such-page/", headers={"accept-language": "en"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"code": "not_found", "detail": "Not found."})

    def test_handler500_returns_json_envelope(self):
        handler = get_resolver().resolve_error_handler(500)
        response = handler(RequestFactory().get("/"))
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(json.loads(response.content), {"code": "server_error",
                                                        "detail": str(MESSAGES["server_error"])})


class MessagesTests(SimpleTestCase):
    def test_every_g9_code_has_a_message(self):
        self.assertEqual([code for code in G9_CODES if code not in MESSAGES], [])

    def test_unknown_code_fails_loudly(self):
        with self.assertRaises(KeyError):
            ApiError("no_such_code")


class FlattenTests(SimpleTestCase):
    def test_flatten_shapes(self):
        leaf = [{"message": "m", "code": "required"}]
        self.assertEqual(_flatten({"delivery": {"district": leaf}}), {"delivery.district": [{"code": "required", "message": "m"}]})
        self.assertEqual(_flatten(leaf), {"non_field_errors": [{"code": "required", "message": "m"}]})
        self.assertEqual(_flatten({"lines": [{}, {"quantity": leaf}]}), {"lines.1.quantity": leaf})
