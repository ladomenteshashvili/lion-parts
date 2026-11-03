"""LP-BE-103 (LPK-98): CSRF on every unsafe request, session auth, permissions (G§4.2, G§5.3, G§5.4)."""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.middleware.csrf import _get_new_csrf_string
from django.test import Client, TestCase, override_settings
from django.urls import path
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory

from api.permissions import IsCustomer, IsFullyVerified, IsPhoneVerified


@api_view(["POST"])
@permission_classes([AllowAny])
def public_post(request):
    return Response({"ok": True})


@api_view(["POST"])
@permission_classes([AllowAny])
def public_echo(request):
    return Response(request.data)


@api_view(["GET", "POST"])
def customer_only(request):  # no decorator: the default (IsCustomer) applies
    return Response({"ok": True})


@api_view(["POST"])
@permission_classes([IsFullyVerified])
def checkout_like(request):
    return Response({"ok": True})


urlpatterns = [
    path("public/", public_post),
    path("echo/", public_echo),
    path("customer/", customer_only),
    path("checkout/", checkout_like),
]


def csrf_client():
    """A browser-like client: CSRF enforced, with a valid csrftoken cookie it can echo."""
    client = Client(enforce_csrf_checks=True, headers={"accept-language": "en"})
    token = _get_new_csrf_string()
    client.cookies["csrftoken"] = token
    return client, token


@override_settings(ROOT_URLCONF=__name__)
class CsrfTests(TestCase):
    def test_anonymous_post_without_csrf_to_public_endpoint_is_rejected(self):
        client, _token = csrf_client()
        response = client.post("/public/", {}, content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "csrf_failed")

    def test_anonymous_post_with_csrf_token_passes(self):
        client, token = csrf_client()
        response = client.post("/public/", {}, content_type="application/json", headers={"x-csrftoken": token})
        self.assertEqual((response.status_code, response.json()), (200, {"ok": True}))

    def test_wrong_csrf_token_is_rejected(self):
        client, _token = csrf_client()
        response = client.post("/public/", {}, content_type="application/json",
                               headers={"x-csrftoken": _get_new_csrf_string()})
        self.assertEqual(response.json()["code"], "csrf_failed")

    def test_logged_in_post_without_csrf_is_rejected(self):
        staff = get_user_model().objects.create_user("op", password="x", is_staff=True)
        client, _token = csrf_client()
        client.force_login(staff)
        response = client.post("/customer/", {}, content_type="application/json")
        self.assertEqual(response.json()["code"], "csrf_failed")


class CsrfOnRealRoutesTests(TestCase):
    def test_every_mounted_api_route_rejects_unsafe_request_without_csrf(self):
        client, _token = csrf_client()
        for url in ("/api/health/", "/api/meta/"):
            with self.subTest(url=url):
                response = client.post(url, {}, content_type="application/json")
                self.assertEqual((response.status_code, response.json()["code"]), (403, "csrf_failed"))

    def test_public_routes_stay_public(self):
        for url in ("/api/health/", "/api/meta/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)


@override_settings(ROOT_URLCONF=__name__)
class PermissionTests(TestCase):
    def test_anonymous_get_to_customer_endpoint_is_401(self):
        response = self.client.get("/customer/", headers={"accept-language": "en"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"code": "not_authenticated", "detail": "Please sign in."})
        self.assertEqual(response["WWW-Authenticate"], "Session")

    def test_staff_session_cannot_call_customer_endpoints(self):
        staff = get_user_model().objects.create_superuser("admin", password="x")
        self.client.force_login(staff)
        response = self.client.get("/customer/", headers={"accept-language": "en"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "permission_denied")

    def test_inactive_user_session_is_anonymous(self):
        user = get_user_model().objects.create_user("gone", password="x")
        self.client.force_login(user)
        user.is_active = False
        user.save()
        self.assertEqual(self.client.get("/customer/").status_code, 401)

    def test_json_only_parser(self):
        response = self.client.post("/echo/", {"a": "b"})  # multipart form
        self.assertEqual((response.status_code, response.json()["code"]), (415, "unsupported_media_type"))
        response = self.client.post("/echo/", {"a": "b"}, content_type="application/json")
        self.assertEqual(response.json(), {"a": "b"})


class CustomerPermissionUnitTests(TestCase):
    """`user.customer` arrives with LP-BE-201; stand-in customers exercise the permission logic now."""

    def request_for(self, customer=None, authenticated=True):
        user = SimpleNamespace(is_authenticated=authenticated, is_active=True)
        if customer is not None:
            user.customer = customer
        request = APIRequestFactory().get("/")
        request.user = user
        return request

    def test_is_customer(self):
        customer = SimpleNamespace(is_phone_verified=False, is_email_verified=False)
        self.assertTrue(IsCustomer().has_permission(self.request_for(customer), None))
        self.assertFalse(IsCustomer().has_permission(self.request_for(), None))  # staff: no customer
        self.assertFalse(IsCustomer().has_permission(self.request_for(customer, authenticated=False), None))

    def test_phone_and_full_verification(self):
        unverified = SimpleNamespace(is_phone_verified=False, is_email_verified=False)
        phone_only = SimpleNamespace(is_phone_verified=True, is_email_verified=False)
        both = SimpleNamespace(is_phone_verified=True, is_email_verified=True)
        self.assertFalse(IsPhoneVerified().has_permission(self.request_for(unverified), None))
        self.assertTrue(IsPhoneVerified().has_permission(self.request_for(phone_only), None))
        self.assertFalse(IsFullyVerified().has_permission(self.request_for(phone_only), None))
        self.assertTrue(IsFullyVerified().has_permission(self.request_for(both), None))
        self.assertEqual((IsFullyVerified.code, IsPhoneVerified.code),
                         ("verification_required", "phone_verification_required"))

    @override_settings(ROOT_URLCONF=__name__)
    def test_customer_without_email_verification_gets_verification_required(self):
        from rest_framework.test import APIClient

        client = APIClient()
        user = SimpleNamespace(is_authenticated=True, is_active=True,
                               customer=SimpleNamespace(is_phone_verified=True, is_email_verified=False))
        client.force_authenticate(user=user)
        response = client.post("/checkout/", {}, format="json", headers={"accept-language": "en"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"code": "verification_required",
                                           "detail": "Phone and email must be verified before payment."})
