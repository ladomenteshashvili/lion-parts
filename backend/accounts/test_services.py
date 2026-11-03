"""Account services: LP-BE-1303 customer_language (more services are tested per story)."""

from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from accounts.services import customer_language


class CustomerLanguageTests(SimpleTestCase):
    def test_preferred_language_wins(self):
        self.assertEqual(customer_language(SimpleNamespace(preferred_language="ru"), fallback="en"), "ru")

    def test_fallback_then_default(self):
        self.assertEqual(customer_language(None, fallback="en"), "en")
        self.assertEqual(customer_language(None), "ka")
        self.assertEqual(customer_language(None, fallback="de"), "ka")

    @override_settings(LANGUAGES=[("ka", "Georgian"), ("en", "English")])
    def test_language_removed_from_settings_falls_back(self):
        self.assertEqual(customer_language(SimpleNamespace(preferred_language="ru"), fallback="en"), "en")
