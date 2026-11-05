"""LPK-183: what the gthread audit relies on, so a later change cannot quietly break it. Threads run one after another
or wait on a barrier: nothing depends on timing."""

import copy
import threading
from unittest import mock

from django.contrib import admin
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.core.mail import mailers
from django.db import connections
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from api.admin_login import USERNAME_LIMIT, BlockedLoginForm
from parts.providers import DEMO_ROWS, fetch_rows
from payments.providers import get_provider


def in_a_thread(work):
    result = []

    def run():
        try:
            result.append(work())
        finally:
            connections.close_all()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    return result[0]


class PerThreadObjectsTests(SimpleTestCase):
    def test_each_thread_has_its_own_database_connection_object(self):
        self.assertIsNot(in_a_thread(lambda: connections["default"]), connections["default"])

    def test_each_thread_has_its_own_mail_backend(self):
        self.assertIsNot(in_a_thread(lambda: mailers["default"]), mailers["default"])

    def test_a_payment_provider_is_never_shared(self):
        self.assertIsNot(get_provider(), get_provider())


class LanguageIsolationTests(SimpleTestCase):
    def test_a_language_set_in_one_thread_never_reaches_another(self):
        languages = ("ka", "en", "ru")
        barrier, seen = threading.Barrier(len(languages)), {}

        def run(language):
            with translation.override(language):
                barrier.wait()  # all three languages are active at once
                seen[language] = translation.get_language()

        threads = [threading.Thread(target=run, args=(language,)) for language in languages]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(seen, {language: language for language in languages})


class SharedObjectsStayUnchangedTests(TestCase):
    @override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
    def test_a_blocked_admin_login_never_changes_the_shared_site(self):
        caches["default"].clear()
        before, seen = admin.site.login_form, []

        def clean(form):
            seen.append(admin.site.login_form)  # what any other thread would see while this request is being blocked
            raise ValidationError("blocked")

        with mock.patch.object(BlockedLoginForm, "clean", clean):
            for _ in range(USERNAME_LIMIT + 1):
                response = Client().post(reverse("admin:login"), {"username": "boss", "password": "wrong"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(seen, [before])
        self.assertIs(admin.site.login_form, before)

    def test_demo_rows_are_not_changed_by_a_search(self):
        before = copy.deepcopy(DEMO_ROWS)
        _provider, _raw, rows = fetch_rows("ABC123")
        rows[0]["name"] = "changed by one request"
        self.assertEqual(DEMO_ROWS, before)
