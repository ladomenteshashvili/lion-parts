"""LPK-182: failed-attempt limits around the Django Admin login."""

import importlib
import logging
import threading
import time
import unittest
from unittest import mock

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import caches
from django.db import DatabaseError, connection
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import clear_url_caches, resolve, reverse
from django.utils import timezone
from django.utils.translation import override as override_language

from api.admin_login import BLOCKED_MESSAGE, IP_LIMIT, USERNAME_LIMIT, admin_login, normalize_username
from api.tests.test_log_secrets import Capture

PASSWORD = "Correct-horse-9"
WRONG = "wrong-password-7"
IP = "198.51.100.1"
OTHER_IP = "198.51.100.2"
AUTHENTICATE = "django.contrib.auth.forms.authenticate"
MD5 = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def rest_framework(**overrides):
    return {**settings.REST_FRAMEWORK, **overrides}


def later(seconds):
    """Move the cache's clock only: expiry is decided there, so nothing has to sleep."""
    now, moved = time.time(), timezone.now() + timezone.timedelta(seconds=seconds)
    clock = (mock.patch("django.core.cache.backends.db.tz_now", return_value=moved),
             mock.patch("django.core.cache.backends.base.time.time", return_value=now + seconds))
    return _Both(*clock)


class _Both:
    def __init__(self, *patches):
        self.patches = patches

    def __enter__(self):
        for patch in self.patches:
            patch.start()

    def __exit__(self, *exc):
        for patch in self.patches:
            patch.stop()


@override_settings(PASSWORD_HASHERS=MD5)
class Base(TestCase):
    def setUp(self):
        caches["default"].clear()
        self.url = reverse("admin:login")
        self.staff = get_user_model().objects.create_superuser("boss", "boss@example.com", PASSWORD)

    def post(self, username="boss", password=WRONG, ip=IP, client=None, url=None, **extra):
        return (client or Client()).post(url or self.url, {"username": username, "password": password}, REMOTE_ADDR=ip,
                                         headers={"accept-language": "en"}, **extra)

    def fail_times(self, times, username="boss", ip=IP):
        for _ in range(times):
            self.assertEqual(self.post(username, ip=ip).status_code, 200)

    def assert_blocked(self, response):
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many failed login attempts", status_code=429)
        self.assertContains(response, 'id="login-form"', status_code=429)  # the admin login template


class LimitTests(Base):
    def test_ten_failures_from_one_ip_block_the_next_post(self):
        for attempt in range(IP_LIMIT):
            self.assertEqual(self.post(f"user{attempt}").status_code, 200)
        self.assert_blocked(self.post("someone-else"))

    def test_five_failures_for_one_username_from_different_ips_block_the_next_attempt(self):
        for attempt in range(USERNAME_LIMIT):
            self.assertEqual(self.post(ip=f"203.0.113.{attempt}").status_code, 200)
        self.assert_blocked(self.post(ip="203.0.113.200"))

    def test_correct_credentials_do_not_bypass_a_lockout_and_are_never_checked(self):
        self.fail_times(USERNAME_LIMIT)
        client = Client()
        with mock.patch(AUTHENTICATE, side_effect=authenticate) as check:
            self.assert_blocked(self.post(password=PASSWORD, ip=OTHER_IP, client=client))
        check.assert_not_called()
        self.assertNotIn("_auth_user_id", client.session)

    def test_an_ip_lockout_also_blocks_correct_credentials(self):
        for attempt in range(IP_LIMIT):
            self.post(f"user{attempt}")
        with mock.patch(AUTHENTICATE, side_effect=authenticate) as check:
            self.assert_blocked(self.post(password=PASSWORD))
        check.assert_not_called()

    def test_a_correct_login_below_the_limit_succeeds_and_clears_the_username_counter(self):
        self.fail_times(USERNAME_LIMIT - 1)
        client = Client()
        response = self.post(password=PASSWORD, client=client)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.session["_auth_user_id"], str(self.staff.pk))
        self.fail_times(USERNAME_LIMIT, ip=OTHER_IP)  # a full allowance again
        self.assert_blocked(self.post(ip=OTHER_IP))

    def test_a_correct_login_keeps_the_ip_failures_and_uses_none_itself(self):
        for attempt in range(IP_LIMIT - 1):
            self.post(f"user{attempt}")
        self.assertEqual(self.post(password=PASSWORD).status_code, 302)
        self.assertEqual(self.post("last").status_code, 200)  # the tenth failure
        self.assert_blocked(self.post("one-more"))

    def test_get_is_never_throttled(self):
        for attempt in range(IP_LIMIT):
            self.post(f"user{attempt}")
        self.fail_times(USERNAME_LIMIT, ip=OTHER_IP)
        for ip in (IP, OTHER_IP):
            self.assertEqual(Client().get(self.url, REMOTE_ADDR=ip).status_code, 200)

    def test_usernames_and_ips_are_independent(self):
        self.fail_times(USERNAME_LIMIT)
        self.assert_blocked(self.post(ip=OTHER_IP))
        self.assertEqual(self.post("another", ip=OTHER_IP).status_code, 200)  # other username, other IP
        self.assertEqual(self.post("another").status_code, 200)  # same IP, still under its limit

    def test_a_blocked_attempt_uses_up_nothing(self):
        self.fail_times(USERNAME_LIMIT)
        for _ in range(20):
            self.assert_blocked(self.post())
        for attempt in range(IP_LIMIT - USERNAME_LIMIT):  # the IP still has exactly its remaining allowance
            self.assertEqual(self.post(f"user{attempt}").status_code, 200)
        self.assert_blocked(self.post("one-more"))

    def test_a_valid_customer_login_counts_as_a_failure(self):
        get_user_model().objects.create_user("customer", "c@example.com", PASSWORD)
        for _ in range(USERNAME_LIMIT):
            self.assertEqual(self.post("customer", PASSWORD).status_code, 200)
        self.assert_blocked(self.post("customer", PASSWORD))

    def test_a_form_without_credentials_counts_nothing(self):
        for data in ({"username": "boss"}, {"password": "x"}, {"username": "   ", "password": "x"}, {}):
            for _ in range(USERNAME_LIMIT + 1):
                self.assertEqual(Client().post(self.url, data, REMOTE_ADDR=IP).status_code, 200)
        self.fail_times(USERNAME_LIMIT)
        self.assert_blocked(self.post())

    def test_the_message_is_translated(self):
        self.fail_times(USERNAME_LIMIT)
        for language in ("ka", "ru"):
            with self.subTest(language=language), override_language(language):
                translated = str(BLOCKED_MESSAGE)
                self.assertNotEqual(translated, "Too many failed login attempts. Please try again later.")
                response = Client().post(self.url, {"username": "boss", "password": WRONG}, REMOTE_ADDR=IP,
                                         headers={"accept-language": language})
                self.assertContains(response, translated, status_code=429)


class ExpiryTests(Base):
    def test_a_lockout_lasts_15_minutes_and_then_ends(self):
        self.fail_times(USERNAME_LIMIT)
        with later(14 * 60 + 58):
            self.assert_blocked(self.post(ip=OTHER_IP))
        with later(15 * 60 + 2):
            self.assertEqual(self.post(password=PASSWORD, ip=OTHER_IP).status_code, 302)

    def test_an_ip_lockout_ends_too(self):
        for attempt in range(IP_LIMIT):
            self.post(f"user{attempt}")
        with later(15 * 60 + 2):
            self.assertEqual(self.post("someone").status_code, 200)

    def test_each_failure_expires_15_minutes_after_itself(self):
        self.fail_times(3)
        with later(600):
            self.fail_times(2)
            self.assert_blocked(self.post())
        with later(15 * 60 + 2):  # the first three are gone, the last two are not
            self.fail_times(3)
            self.assert_blocked(self.post())
        with later(600 + 15 * 60 + 2):  # everything has expired
            self.fail_times(1)

    def test_attempts_while_blocked_do_not_extend_a_lockout(self):
        self.fail_times(USERNAME_LIMIT)
        with later(14 * 60):
            for _ in range(3):
                self.assert_blocked(self.post())
        with later(15 * 60 + 2):
            self.assertEqual(self.post(password=PASSWORD).status_code, 302)


class NormalizationTests(Base):
    VARIANTS = ("boss", "BOSS", " boss ", "\tBoSs\n", " boss ", "ｂｏｓｓ")

    def test_the_normalized_form_is_the_same(self):
        self.assertEqual({normalize_username(variant) for variant in self.VARIANTS}, {"boss"})

    def test_variants_share_one_limit(self):
        for variant in self.VARIANTS[:USERNAME_LIMIT]:
            self.assertEqual(self.post(variant, ip=OTHER_IP if variant == "BOSS" else IP).status_code, 200)
        for variant in self.VARIANTS:
            self.assert_blocked(self.post(variant, ip="203.0.113.9"))

    def test_a_look_alike_that_would_log_in_cannot_bypass_the_lockout(self):
        self.assertEqual(self.post("ｂｏｓｓ", PASSWORD).status_code, 302)  # the form normalizes it too
        self.fail_times(USERNAME_LIMIT)
        self.assert_blocked(self.post("ｂｏｓｓ", PASSWORD))

    def test_a_successful_login_clears_every_variant(self):
        self.fail_times(USERNAME_LIMIT - 1, "BOSS")
        self.assertEqual(self.post(" boss ", PASSWORD).status_code, 302)
        self.fail_times(USERNAME_LIMIT, "Boss", ip=OTHER_IP)


class ClientIpTests(Base):
    def spoofed(self, attempt):
        return {"x-forwarded-for": f"10.0.0.{attempt}"}

    def test_without_a_proxy_forwarded_headers_are_ignored(self):
        with self.settings(REST_FRAMEWORK=rest_framework(NUM_PROXIES=0)):
            for attempt in range(IP_LIMIT):
                self.assertEqual(Client().post(self.url, {"username": f"user{attempt}", "password": WRONG}, REMOTE_ADDR=IP,
                                               headers=self.spoofed(attempt)).status_code, 200)
            self.assert_blocked(Client().post(self.url, {"username": "x", "password": WRONG}, REMOTE_ADDR=IP,
                                              headers={"accept-language": "en", **self.spoofed(99)}))

    def post_through_nginx(self, username, forwarded):
        return Client().post(self.url, {"username": username, "password": WRONG}, REMOTE_ADDR="127.0.0.1",
                             headers={"accept-language": "en", "x-forwarded-for": forwarded})

    def test_behind_nginx_a_prepended_address_cannot_change_the_client(self):
        with self.settings(REST_FRAMEWORK=rest_framework(NUM_PROXIES=1)):
            for attempt in range(IP_LIMIT):  # nginx appends the real address last
                self.assertEqual(self.post_through_nginx(f"user{attempt}", f"10.0.0.{attempt}, {IP}").status_code, 200)
            self.assert_blocked(self.post_through_nginx("x", f"10.9.9.9, {IP}"))

    def test_behind_nginx_real_clients_stay_separate(self):
        with self.settings(REST_FRAMEWORK=rest_framework(NUM_PROXIES=1)):
            for attempt in range(IP_LIMIT):
                self.post_through_nginx(f"user{attempt}", IP)
            self.assert_blocked(self.post_through_nginx("x", IP))
            self.assertEqual(self.post_through_nginx("x", OTHER_IP).status_code, 200)


class ExistingBehaviourTests(Base):
    def test_the_csrf_check_runs_first_and_a_rejected_post_counts_nothing(self):
        for _ in range(USERNAME_LIMIT + 1):
            client = Client(enforce_csrf_checks=True)
            self.assertEqual(self.post(client=client).status_code, 403)
        client = Client(enforce_csrf_checks=True)
        client.get(self.url)
        response = client.post(self.url, {"username": "boss", "password": PASSWORD,
                                          "csrfmiddlewaretoken": client.cookies["csrftoken"].value}, REMOTE_ADDR=IP)
        self.assertEqual(response.status_code, 302)

    def test_the_blocked_page_needs_and_renews_a_csrf_form_too(self):
        self.fail_times(USERNAME_LIMIT)
        self.assertContains(self.post(), "csrfmiddlewaretoken", status_code=429)

    def test_the_login_route_is_the_wrapper(self):
        self.assertEqual(self.url, f"/{settings.ADMIN_URL_PATH}login/")
        self.assertIs(resolve(self.url).func, admin_login)

    def test_the_rest_of_the_admin_is_unchanged(self):
        response = Client().get(reverse("admin:index"))
        self.assertEqual((response.status_code, response["Location"]), (302, f"{self.url}?next={reverse('admin:index')}"))

    def test_next_is_followed_when_safe_and_ignored_when_not(self):
        target = reverse("admin:auth_user_changelist")
        for next_url, expected in ((target, target), ("https://evil.example/", settings.LOGIN_REDIRECT_URL)):
            with self.subTest(next_url=next_url):
                response = Client().post(f"{self.url}?next={next_url}",
                                         {"username": "boss", "password": PASSWORD, "next": next_url}, REMOTE_ADDR=IP)
                self.assertEqual((response.status_code, response["Location"]), (302, expected))

    def test_redirects_match_the_unwrapped_admin_login(self):
        target = reverse("admin:auth_user_changelist")
        for next_url in (target, "https://evil.example/", ""):
            with self.subTest(next_url=next_url):
                wrapped = Client().post(self.url, {"username": "boss", "password": PASSWORD, "next": next_url},
                                        REMOTE_ADDR=IP)
                request = RequestFactory().post(self.url, {"username": "boss", "password": PASSWORD, "next": next_url})
                request.session = self.client.session
                request.user = AnonymousUser()
                request._dont_enforce_csrf_checks = True
                self.assertEqual(wrapped["Location"], admin.site.login(request)["Location"])

    def test_the_real_login_form_carries_next_back_to_the_admin(self):
        self.assertContains(Client().get(self.url), f'name="next" value="{reverse("admin:index")}"')

    def test_a_logged_in_staff_user_is_still_redirected_from_the_page(self):
        client = Client()
        client.force_login(self.staff)
        self.assertEqual(client.get(self.url).status_code, 302)

    def test_the_session_is_rotated_on_login_as_before(self):
        client = Client()
        session = client.session
        session["before_login"] = True
        session.save()
        before = client.session.session_key
        self.post(password=PASSWORD, client=client)
        self.assertNotEqual(client.session.session_key, before)
        self.assertEqual(client.session["_auth_user_id"], str(self.staff.pk))
        self.assertEqual(client.get(reverse("admin:index")).status_code, 200)

    def test_the_login_page_is_never_cached(self):
        self.assertIn("no-cache", Client().get(self.url)["Cache-Control"])
        self.fail_times(USERNAME_LIMIT)
        self.assertIn("no-cache", self.post()["Cache-Control"])

    def test_a_configured_admin_path_gets_the_limits(self):
        import config.urls

        def reload_urls():
            clear_url_caches()
            importlib.reload(config.urls)

        self.addCleanup(reload_urls)
        with self.settings(ADMIN_URL_PATH="ops-test/"):
            reload_urls()
            self.failures_at("/ops-test/login/")
            self.assertEqual(Client().get("/admin/login/").status_code, 404)

    def failures_at(self, url):
        for _ in range(USERNAME_LIMIT):
            self.assertEqual(self.post(url=url).status_code, 200)
        self.assert_blocked(self.post(url=url))


class LoggingTests(Base):
    def setUp(self):
        super().setUp()
        self.capture = Capture()
        self.loggers = [logging.getLogger(), logging.getLogger("django")]
        self.levels = [lg.level for lg in self.loggers]
        for lg in self.loggers:
            lg.addHandler(self.capture)
            lg.setLevel(logging.DEBUG)

    def tearDown(self):
        for lg, level in zip(self.loggers, self.levels):
            lg.removeHandler(self.capture)
            lg.setLevel(level)

    def test_no_credentials_or_plaintext_username_reach_any_log(self):
        username = "Private.Operator"
        self.fail_times(USERNAME_LIMIT, username)
        self.assert_blocked(self.post(username, PASSWORD))
        text = "\n".join(self.capture.lines)
        for secret in (username, username.lower(), PASSWORD, WRONG):
            self.assertNotIn(secret, text)

    def test_a_blocked_attempt_logs_the_ip_and_a_keyed_hash(self):
        self.fail_times(USERNAME_LIMIT)
        with self.assertLogs("api.admin_login", "WARNING") as logs:
            self.post()
        message = logs.records[0].getMessage()
        self.assertIn(f"ip={IP} ", message)
        self.assertIn("limit=username", message)
        hash_value = message.split("username_hash=")[1].split()[0]
        with self.settings(SECRET_KEY="another-key"), self.assertLogs("api.admin_login", "WARNING") as other:
            self.fail_times(USERNAME_LIMIT)
            self.post()
        self.assertNotIn(hash_value, other.output[0])  # it depends on the key: not a plain hash anyone can recompute


class InFlightTests(Base):
    """Attempts arriving while earlier passwords are still being checked. Each nested request runs inside the check
    of the one before, which is the worst possible overlap and needs no timing."""

    def overlap(self, limit, new_attempt):
        calls, statuses = [], []

        def check(*args, **kwargs):
            calls.append(1)
            if len(calls) <= limit + 2:
                statuses.append(new_attempt(len(calls)))
            return None

        with mock.patch(AUTHENTICATE, side_effect=check):
            statuses.append(new_attempt(0))
        return len(calls), statuses

    def test_the_username_limit_holds_while_checks_overlap(self):
        calls, statuses = self.overlap(USERNAME_LIMIT, lambda n: self.post(ip=f"203.0.113.{n}").status_code)
        self.assertEqual(calls, USERNAME_LIMIT)
        self.assertIn(429, statuses)

    def test_the_ip_limit_holds_while_checks_overlap(self):
        calls, statuses = self.overlap(IP_LIMIT, lambda n: self.post(f"user{n}").status_code)
        self.assertEqual(calls, IP_LIMIT)
        self.assertIn(429, statuses)


class ReservationTests(Base):
    """cache.get_many spares taken slots a failed insert; cache.add stays the only thing that grants a slot."""

    def spy(self):
        return mock.patch("api.admin_login.cache", wraps=caches["default"])

    def adds(self, spy, kind):
        return [call.args[0] for call in spy.add.call_args_list if f":{kind}:" in call.args[0]]

    def test_a_full_username_bucket_attempts_no_insert_however_often_it_is_tried(self):
        self.fail_times(USERNAME_LIMIT)
        with self.spy() as spy:
            for _ in range(20):
                self.assert_blocked(self.post())
        self.assertEqual(self.adds(spy, "username"), [])

    def test_a_full_ip_bucket_attempts_no_insert_at_all(self):
        for attempt in range(IP_LIMIT):
            self.post(f"user{attempt}")
        with self.spy() as spy:
            for attempt in range(20):
                self.assert_blocked(self.post(f"other{attempt}"))
        spy.add.assert_not_called()

    def test_only_the_first_free_slot_is_tried(self):
        self.fail_times(3)
        with self.spy() as spy:
            self.assertEqual(self.post().status_code, 200)
        for kind in ("username", "ip"):
            with self.subTest(kind=kind):
                self.assertEqual([key.rsplit(":", 1)[1] for key in self.adds(spy, kind)], ["3"])

    def test_cache_add_still_decides_when_the_read_is_stale(self):
        self.fail_times(USERNAME_LIMIT)
        with self.spy() as spy, mock.patch(AUTHENTICATE, side_effect=authenticate) as check:
            spy.get_many = mock.Mock(return_value={})  # as if another request filled them after this read
            self.assert_blocked(self.post(ip=OTHER_IP))
        self.assertEqual(len(self.adds(spy, "username")), USERNAME_LIMIT)
        check.assert_not_called()

    def test_losing_a_slot_to_a_parallel_request_moves_on_to_the_next(self):
        real = caches["default"].add

        def add(key, *args, **kwargs):
            return False if ":username:" in key and key.endswith(":0") else real(key, *args, **kwargs)

        with self.spy() as spy:
            spy.add.side_effect = add
            self.assertEqual(self.post().status_code, 200)
        self.assertEqual([key.rsplit(":", 1)[1] for key in self.adds(spy, "username")], ["0", "1"])

    def test_a_cache_that_refuses_writes_blocks_the_login(self):
        with self.spy() as spy, mock.patch(AUTHENTICATE, side_effect=authenticate) as check:
            spy.add.side_effect = lambda *args, **kwargs: False
            self.assert_blocked(self.post(password=PASSWORD))
        check.assert_not_called()

    def test_a_cache_that_cannot_be_read_does_not_check_the_credentials(self):
        client = Client(raise_request_exception=False)
        with self.spy() as spy, mock.patch(AUTHENTICATE, side_effect=authenticate) as check:
            spy.get_many.side_effect = DatabaseError("cache table unavailable")
            response = self.post(password=PASSWORD, client=client)
        self.assertEqual(response.status_code, 500)
        check.assert_not_called()
        self.assertNotIn("_auth_user_id", client.session)


class CacheCapacityTests(Base):
    def test_the_cache_keeps_enough_rows(self):
        self.assertEqual(caches["default"]._max_entries, 100000)

    def test_other_cache_traffic_keeps_a_lockout_in_place(self):
        self.fail_times(USERNAME_LIMIT)
        for key in range(400):  # more rows than Django's default cache limit
            caches["default"].add(f"throttle_search_{key}", 1, 900)
        self.assert_blocked(self.post(ip=OTHER_IP))


@override_settings(PASSWORD_HASHERS=MD5)
@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL (CI)")
class ParallelAttemptTests(TransactionTestCase):
    """Real parallel requests on separate connections, all released together while every check waits for the others."""

    serialized_rollback = True
    REQUESTS = 12

    def setUp(self):
        caches["default"].clear()
        self.url = reverse("admin:login")
        get_user_model().objects.create_superuser("boss", "boss@example.com", PASSWORD)

    def race(self, attempt):
        results, calls, lock, start = [], [], threading.Lock(), threading.Barrier(self.REQUESTS)

        def check(*args, **kwargs):
            with lock:
                calls.append(1)
            return None

        def run(number):
            start.wait()
            try:
                results.append(attempt(number).status_code)
            finally:
                connection.close()

        with mock.patch(AUTHENTICATE, side_effect=check):
            threads = [threading.Thread(target=run, args=(number,)) for number in range(self.REQUESTS)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        return results, calls

    def test_parallel_failures_for_one_username_stop_exactly_at_the_limit(self):
        results, calls = self.race(lambda n: Client().post(self.url, {"username": "boss", "password": WRONG},
                                                           REMOTE_ADDR=f"203.0.113.{n}"))
        self.assertEqual(len(calls), USERNAME_LIMIT)
        self.assertEqual(sorted(results), [200] * USERNAME_LIMIT + [429] * (self.REQUESTS - USERNAME_LIMIT))

    def test_parallel_failures_from_one_ip_stop_exactly_at_the_limit(self):
        results, calls = self.race(lambda n: Client().post(self.url, {"username": f"user{n}", "password": WRONG},
                                                           REMOTE_ADDR=IP))
        self.assertEqual(len(calls), IP_LIMIT)
        self.assertEqual(sorted(results), [200] * IP_LIMIT + [429] * (self.REQUESTS - IP_LIMIT))
