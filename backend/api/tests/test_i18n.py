"""LP-BE-1301 (LPK-166): i18n foundation (G§4.11, G§5.8)."""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import path
from django.utils import translation
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from api.drf_messages import DRF_MESSAGES
from api.errors import MESSAGES
from api.exceptions import DEFAULT_MESSAGES
from orders.texts import EVENT_TEXTS

BACKEND_DIR = Path(settings.BASE_DIR)

# Every dict of gettext_lazy msgids the backend renders. Each story registers its own here
# (MESSAGES, DEFAULT_MESSAGES, EVENT_TEXTS, DEFAULT_TITLES ...); the completeness test covers them all.
TRANSLATED_REGISTRIES = [MESSAGES, DEFAULT_MESSAGES, DRF_MESSAGES, EVENT_TEXTS]
# Codes deliberately left untranslated (provider-facing, never shown to users; G§9 "—").
UNTRANSLATED_CODES = set()

# shortcut: legacy files that still contain Georgian literals; each is cleaned up by the named story.
# The test fails once a listed file has none left, so this list only shrinks.
LEGACY_GEORGIAN_FILES = {
    "orders/invoices.py": "LP-BE-604",
}
GEORGIAN = re.compile(r"[\u10a0-\u10ff]")  # Georgian script block (U+10A0-U+10FF)


class EchoSerializer(serializers.Serializer):
    name = serializers.CharField()


@api_view(["POST"])
@permission_classes([AllowAny])
def echo_view(request):
    serializer = EchoSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return Response(serializer.validated_data)


urlpatterns = [path("echo/", echo_view)]


def non_english_languages():
    return [code for code, _ in settings.LANGUAGES if code != "en"]


class LanguageResolutionTests(TestCase):
    def get_meta(self, accept_language=None):
        headers = {"accept-language": accept_language} if accept_language else {}
        return self.client.get("/api/meta/", headers=headers)

    def test_supported_languages_are_activated(self):
        for code in ("ka", "en", "ru"):
            with self.subTest(code=code):
                self.assertEqual(self.get_meta(code)["Content-Language"], code)

    def test_unsupported_or_missing_language_falls_back_to_georgian(self):
        self.assertEqual(self.get_meta("de")["Content-Language"], "ka")
        self.assertEqual(self.get_meta()["Content-Language"], "ka")

    def test_default_language_is_georgian(self):
        self.assertEqual(settings.LANGUAGE_CODE, "ka")

    def test_meta_lists_languages_with_their_own_names(self):
        data = self.get_meta("en").json()
        self.assertEqual(data["default_language"], "ka")
        self.assertEqual([lang["code"] for lang in data["languages"]], ["ka", "en", "ru"])
        names = {lang["code"]: lang["name"] for lang in data["languages"]}
        self.assertEqual(names["en"], "English")
        self.assertEqual(names["ru"], "Русский")
        self.assertTrue(GEORGIAN.search(names["ka"]))
        # Own names don't depend on the request language.
        self.assertEqual(self.get_meta("ru").json()["languages"], data["languages"])

    @override_settings(ROOT_URLCONF=__name__)
    def test_russian_request_gets_russian_drf_validation_messages(self):
        response = self.client.post("/echo/", {}, content_type="application/json",
                                    headers={"accept-language": "ru"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response["Content-Language"], "ru")
        with translation.override("ru"):
            expected = str(serializers.CharField().error_messages["required"])
        self.assertNotEqual(expected, "This field is required.")
        self.assertIn(expected, response.content.decode())

    def test_adding_a_language_needs_only_configuration(self):
        with self.settings(LANGUAGES=settings.LANGUAGES + [("de", "German")]):
            response = self.get_meta("de")
            self.assertEqual(response["Content-Language"], "de")
            self.assertIn({"code": "de", "name": "Deutsch"}, response.json()["languages"])


class CatalogTests(SimpleTestCase):
    def test_every_non_english_language_has_a_compiled_catalog(self):
        for code in non_english_languages():
            with self.subTest(code=code):
                catalog = BACKEND_DIR / "locale" / code / "LC_MESSAGES"
                self.assertTrue((catalog / "django.po").exists(), f"missing {code} .po")
                self.assertTrue((catalog / "django.mo").exists(),
                                f"missing {code} .mo: run `python manage.py compilemessages`")

    def test_catalogs_have_no_fuzzy_or_empty_entries(self):
        """Fuzzy entries are not compiled, so they silently show English; every msgid needs a msgstr."""
        for code in non_english_languages():
            with self.subTest(code=code):
                po = (BACKEND_DIR / "locale" / code / "LC_MESSAGES" / "django.po").read_text(encoding="utf-8")
                entries = po.split("\n\n")[1:]  # skip the header entry
                self.assertEqual([e for e in entries if "#, fuzzy" in e], [])
                # an empty msgstr / msgstr[n] (plural forms too); no-wrap catalogs have no continuation lines
                self.assertEqual([e for e in entries if re.search(r'^msgstr(\[\d+\])? ""$', e, re.M)], [])

    def test_every_registered_msgid_is_translated(self):
        for code in non_english_languages():
            for registry in TRANSLATED_REGISTRIES:
                for key, msg in registry.items():
                    if key in UNTRANSLATED_CODES:
                        continue
                    with self.subTest(language=code, key=key):
                        with translation.override("en"):
                            english = str(msg)
                        with translation.override(code):
                            self.assertNotEqual(str(msg), english, f"{key!r} has no {code} translation")

    def test_language_names_are_translated(self):
        with translation.override("ru"):
            self.assertEqual(str(dict(settings.LANGUAGES)["ka"]), "Грузинский")


class NoGeorgianLiteralsTests(SimpleTestCase):
    def python_files(self):
        for file in BACKEND_DIR.rglob("*.py"):
            rel = file.relative_to(BACKEND_DIR).as_posix()
            if rel.startswith(("venv/", "locale/")) or "/migrations/" in rel:
                continue
            yield rel, file

    def test_no_georgian_literals_outside_legacy_files(self):
        offenders = [rel for rel, file in self.python_files()
                     if rel not in LEGACY_GEORGIAN_FILES and GEORGIAN.search(file.read_text("utf-8"))]
        self.assertEqual(offenders, [], "Use gettext_lazy with English msgids (G§5.8)")

    def test_legacy_allowlist_only_shrinks(self):
        files = dict(self.python_files())
        stale = [rel for rel in LEGACY_GEORGIAN_FILES
                 if rel not in files or not GEORGIAN.search(files[rel].read_text("utf-8"))]
        self.assertEqual(stale, [], "No Georgian left: remove these from LEGACY_GEORGIAN_FILES")
