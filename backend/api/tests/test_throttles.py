"""LP-BE-104 (LPK-99): throttle classes (G§5.5); rates in REST_FRAMEWORK, state in the DB cache."""

import os
import subprocess
import sys
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache, caches
from django.core.cache.backends.db import DatabaseCache
from django.db import connection, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import path
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
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

    def test_non_object_json_body_does_not_crash(self):
        # Throttles run before the view's serializer; a JSON array must not cause a 500.
        for url in ("/send-code/", "/login/"):
            with self.subTest(url=url):
                self.assertEqual(self.post(url, [1]).status_code, 200)

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


def login_request(email="nino@example.com", ip="10.9.0.1"):
    return Request(APIRequestFactory().post("/", {"email": email}, format="json", REMOTE_ADDR=ip),
                   parsers=[JSONParser()])


class SlotTests(TestCase):
    """LPK-221: slots, expiry, Retry-After and the failure behaviour."""

    def attempt(self, throttle=throttles.LoginIpThrottle):
        instance = throttle()
        return instance.allow_request(login_request(), None), instance

    def use_up(self):
        for _ in range(10):
            self.assertTrue(self.attempt()[0])

    def test_refused_requests_take_no_slot_and_report_a_bounded_wait(self):
        self.use_up()
        allowed, throttle = self.attempt()
        self.assertFalse(allowed)
        self.assertTrue(1 <= throttle.wait() <= 60, throttle.wait())

    def test_an_expired_slot_is_free_again_and_only_that_one(self):
        self.use_up()
        with connection.cursor() as cursor:
            cursor.execute("SELECT cache_key FROM cache_table ORDER BY cache_key LIMIT 1")
            oldest = cursor.fetchone()[0]
            cursor.execute("UPDATE cache_table SET expires = %s WHERE cache_key = %s",
                           [timezone.now() - timedelta(seconds=5), oldest])
        self.assertTrue(self.attempt()[0])
        self.assertFalse(self.attempt()[0])

    def test_the_cache_table_holds_no_personal_data(self):
        self.attempt(throttles.EmailLoginThrottle)
        with connection.cursor() as cursor:
            cursor.execute("SELECT cache_key FROM cache_table")
            keys = " ".join(row[0] for row in cursor.fetchall())
        self.assertIn("login_email", keys)
        self.assertNotIn("nino", keys)

    def test_an_identifier_of_any_length_works(self):
        request = login_request(email="x" * 300 + "@example.com")
        self.assertTrue(throttles.EmailLoginThrottle().allow_request(request, None))

    def test_a_failing_cache_refuses_and_logs_without_the_identifier(self):
        for broken in ({"get_many": mock.Mock(side_effect=RuntimeError("nino@example.com"))},
                       {"add": mock.Mock(side_effect=RuntimeError("nino@example.com"))}):
            with self.subTest(broken=list(broken)), mock.patch.multiple(cache, **broken), \
                    self.assertLogs("api.throttles", "ERROR") as logs:
                allowed, throttle = self.attempt(throttles.EmailLoginThrottle)
            self.assertFalse(allowed)
            self.assertEqual(throttle.wait(), 60)
            self.assertNotIn("nino", " ".join(logs.output))

    def test_add_reporting_failure_is_a_refusal(self):
        # DatabaseCache swallows database errors in add and answers False
        with mock.patch.object(cache, "add", return_value=False):
            allowed, throttle = self.attempt()
        self.assertFalse(allowed)
        self.assertEqual(throttle.wait(), 60)

    def test_requests_without_an_identifier_are_not_counted(self):
        self.assertTrue(throttles.EmailLoginThrottle().allow_request(login_request(email=""), None))


@unittest.skipUnless(connection.vendor == "postgresql", "needs real parallel connections")
class ConcurrentSlotTests(TransactionTestCase):
    """LPK-221: never more than the limit, whatever the interleaving."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_threads_cannot_overshoot(self):
        cases = ((throttles.EmailLoginThrottle, 5), (throttles.LoginIpThrottle, 10), (throttles.OtpVerifyThrottle, 10))
        for throttle, limit in cases:
            workers = limit * 3
            barrier, allowed, lock = threading.Barrier(workers), [], threading.Lock()

            def run():
                try:
                    barrier.wait()
                    result = throttle().allow_request(login_request(), None)
                    with lock:
                        allowed.append(result)
                finally:
                    connections.close_all()

            threads = [threading.Thread(target=run) for _ in range(workers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            with self.subTest(throttle=throttle.__name__):
                self.assertEqual((len(allowed), sum(allowed)), (workers, limit))

    def test_separate_processes_cannot_overshoot(self):
        """Gunicorn workers: one connection and one interpreter each. They start together at a shared time."""
        start_at = time.time() + 4
        code = (
            "import time, django; django.setup();\n"
            "from rest_framework.test import APIRequestFactory\n"
            "from api.throttles import LoginIpThrottle\n"
            f"time.sleep(max(0, {start_at} - time.time()))\n"
            "request = APIRequestFactory().post('/', REMOTE_ADDR='10.9.0.2')\n"
            "print(sum(LoginIpThrottle().allow_request(request, None) for _ in range(8)))\n"
        )
        env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings", "DB_NAME": connection.settings_dict["NAME"]}
        processes = [subprocess.Popen([sys.executable, "-c", code], cwd=Path(settings.BASE_DIR), env=env, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
        results = [process.communicate(timeout=60) for process in processes]
        self.assertEqual([process.returncode for process in processes], [0] * 4, results)
        self.assertEqual(sum(int(out.strip()) for out, _err in results), 10)
