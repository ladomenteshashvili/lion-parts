"""LP-BE-104 (LPK-99): throttle classes (G§5.5); rates in REST_FRAMEWORK, state in the DB cache."""

from django.conf import settings
from django.core.cache import cache, caches
from django.core.cache.backends.db import DatabaseCache
from django.test import Client, TestCase, override_settings
from django.urls import path
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory

from api import throttles


def view_with(*classes):
    @api_view(["POST"])
    @permission_classes([AllowAny])
    @throttle_classes(list(classes))
    def view(request):
        return Response({"ok": True})
    return view


urlpatterns = [
    path("send-code/", view_with(throttles.PhoneThrottle, throttles.OtpSendIpThrottle)),
    path("login/", view_with(throttles.LoginIpThrottle, throttles.EmailLoginThrottle)),
]


def rates(**changes):
    return {**settings.REST_FRAMEWORK,
            "DEFAULT_THROTTLE_RATES": {**settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], **changes}}


@override_settings(ROOT_URLCONF=__name__)
class ThrottleTests(TestCase):
    def post(self, url, body, ip="10.0.0.1", client=None):
        return (client or Client()).post(url, body, content_type="application/json", REMOTE_ADDR=ip)

    def test_sixth_code_for_same_phone_is_throttled_across_sessions_and_ips(self):
        formats = ["+995 555 12 34 56", "555123456", "995555123456", "555 123 456", "+995555123456"]
        for i, phone in enumerate(formats):
            response = self.post("/send-code/", {"phone": phone}, ip=f"10.0.1.{i}")
            self.assertEqual(response.status_code, 200, phone)
        response = self.post("/send-code/", {"phone": "555123456"}, ip="10.0.1.99")
        self.assertEqual(response.status_code, 429)
        body = response.json()
        self.assertEqual(body["code"], "throttled")
        self.assertIsInstance(body["retry_after"], int)
        self.assertEqual(self.post("/send-code/", {"phone": "555999888"}, ip="10.0.1.99").status_code, 200)

    def test_phone_from_registration_draft_counts_too(self):
        client = Client()
        session = client.session
        session["registration_draft"] = {"phone": "555000111"}
        session.save()
        for i in range(5):
            self.assertEqual(self.post("/send-code/", {}, ip=f"10.0.2.{i}", client=client).status_code, 200)
        self.assertEqual(self.post("/send-code/", {"phone": "555000111"}, ip="10.0.2.50").status_code, 429)

    def test_eleventh_login_per_minute_from_one_ip_is_throttled(self):
        for i in range(10):
            self.assertEqual(self.post("/login/", {"email": f"u{i}@example.com"}).status_code, 200)
        self.assertEqual(self.post("/login/", {"email": "u11@example.com"}).status_code, 429)
        self.assertEqual(self.post("/login/", {"email": "u12@example.com"}, ip="10.0.0.2").status_code, 200)

    def test_login_attempts_per_email_are_limited_across_ips(self):
        for i in range(5):
            self.assertEqual(self.post("/login/", {"email": "Nino@Example.com"}, ip=f"10.0.3.{i}").status_code, 200)
        self.assertEqual(self.post("/login/", {"email": " nino@example.com"}, ip="10.0.3.9").status_code, 429)

    def test_rates_come_from_settings(self):
        with self.settings(REST_FRAMEWORK=rates(login_ip="2/min")):
            for i in range(2):
                self.assertEqual(self.post("/login/", {"email": f"x{i}@e.ge"}).status_code, 200)
            self.assertEqual(self.post("/login/", {"email": "x3@e.ge"}).status_code, 429)


class ThrottleConfigTests(TestCase):
    def test_every_scope_has_a_rate(self):
        classes = [c for c in vars(throttles).values()
                   if isinstance(c, type) and issubclass(c, throttles.SettingsRateThrottle) and getattr(c, "scope", None)]
        self.assertEqual({c.scope for c in classes}, set(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]))
        for cls in classes:
            cls()  # raises ImproperlyConfigured if its rate is missing

    def test_state_is_shared_through_the_database_cache(self):
        self.assertIsInstance(caches["default"], DatabaseCache)
        self.assertIs(throttles.SettingsRateThrottle.cache, cache)  # DRF's default cache = caches["default"]

    def ident(self, num_proxies, xff):
        request = APIRequestFactory().get("/", REMOTE_ADDR="192.0.2.1", HTTP_X_FORWARDED_FOR=xff)
        with self.settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": num_proxies}):
            return throttles.LoginIpThrottle().get_ident(request)

    def test_client_ip_uses_num_proxies(self):
        self.assertEqual(self.ident(0, "6.6.6.6"), "192.0.2.1")  # spoofed X-Forwarded-For ignored
        self.assertEqual(self.ident(1, "6.6.6.6, 198.51.100.7"), "198.51.100.7")  # nginx-appended address

    def test_num_proxies_defaults_to_zero(self):
        self.assertEqual(settings.REST_FRAMEWORK["NUM_PROXIES"], 0)

    def test_user_keyed_email_send_throttle(self):
        request = APIRequestFactory().post("/")
        request.user = type("U", (), {"is_authenticated": True, "pk": 7})()
        self.assertEqual(throttles.EmailSendThrottle().get_cache_key(request, None), "throttle_email_send_7")
        request.user = type("A", (), {"is_authenticated": False, "pk": None})()
        self.assertIsNone(throttles.EmailSendThrottle().get_cache_key(request, None))
