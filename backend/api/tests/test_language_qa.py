"""LP-BE-1306: language QA (G§4.11, G§5.8, G§9). Placeholders, language selection and fallback, seeded content in
every language, consistent customer-facing terminology, admin language."""

import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import translation
from django.utils.translation import gettext
from rest_framework.test import APIClient

from accounts.models import CustomerTariff
from orders.texts import DEFAULT_TITLES, EVENT_TEXTS, PLURAL_TEXTS
from pricing.models import PickupPoint, SiteSettings

LOCALE = Path(settings.BASE_DIR) / "locale"
TRANSLATED = [code for code, _name in settings.LANGUAGES if code != "en"]
PLACEHOLDER = re.compile(r"%\((\w+)\)[sd]|\{(\w+)\}|%[sd]")


def po_entries(language):
    """(msgid, msgid_plural, [msgstr...]) of our catalog (a small reader: no third-party parser)."""
    text = (LOCALE / language / "LC_MESSAGES" / "django.po").read_text("utf-8")
    for block in text.split("\n\n")[1:]:
        fields, current = {}, None
        for line in block.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            match = re.match(r'^(msgid_plural|msgid|msgstr(?:\[\d+\])?) "(.*)"$', line)
            if match:
                current = match.group(1)
                fields[current] = match.group(2)
            elif line.startswith('"') and current:
                fields[current] += line[1:-1]
        if fields.get("msgid"):
            strings = [v for k, v in sorted(fields.items()) if k.startswith("msgstr")]
            yield fields["msgid"], fields.get("msgid_plural"), strings


def placeholders(text):
    return sorted(m.group(0) for m in PLACEHOLDER.finditer(text))


class PlaceholderTests(SimpleTestCase):
    def test_every_translation_keeps_the_placeholders_of_its_msgid(self):
        problems = []
        for language in TRANSLATED:
            for msgid, plural, strings in po_entries(language):
                expected = set(placeholders(msgid)) | set(placeholders(plural or ""))
                for index, string in enumerate(strings):
                    got = set(placeholders(string))
                    # a plural form may omit the count ("one item" for n == 1) but never invent a placeholder
                    ok = got <= expected and (plural or got == expected)
                    if not ok:
                        problems.append((language, msgid, index, sorted(got), sorted(expected)))
        self.assertEqual(problems, [])

    def test_the_reader_sees_the_whole_catalog(self):
        for language in TRANSLATED:
            entries = list(po_entries(language))
            self.assertGreater(len(entries), 250, language)
            self.assertTrue(all(all(strings) for _msgid, _plural, strings in entries), language)

    def test_plural_texts_have_every_form(self):
        expected_forms = {"ka": 2, "ru": 3}
        msgids = {singular for singular, _plural in PLURAL_TEXTS.values()}
        for language in TRANSLATED:
            forms = {msgid: strings for msgid, plural, strings in po_entries(language) if plural}
            for msgid in msgids:
                self.assertEqual(len(forms[msgid]), expected_forms[language], (language, msgid))


class LanguageSelectionTests(TestCase):
    def detail(self, accept_language=None):
        headers = {"accept-language": accept_language} if accept_language is not None else {}
        response = APIClient().get("/api/orders/", headers=headers)  # 401 with a localized detail
        return response["Content-Language"], response.json()["detail"]

    def expected(self, language):
        with translation.override(language):
            return gettext("Please sign in.")

    def test_header_variants(self):
        cases = {"en": "en", "en-US": "en", "ru-RU,ru;q=0.9": "ru", "de,ru;q=0.5": "ru", "de": "ka", "*": "ka",
                 "ka": "ka", "": "ka", None: "ka", "EN": "en"}
        for header, language in cases.items():
            with self.subTest(header=header):
                self.assertEqual(self.detail(header), (language, self.expected(language)))

    def test_the_three_languages_really_differ(self):
        self.assertEqual(len({self.expected(code) for code in ("ka", "en", "ru")}), 3)

    def test_validation_field_messages_follow_the_language(self):
        messages = set()
        for language in ("ka", "en", "ru"):
            body = APIClient().post("/api/support/tickets/", {}, format="json",
                                    headers={"accept-language": language}).json()
            messages.add((body["detail"], body["fields"]["message"][0]["message"]))
        self.assertEqual(len(messages), 3)


class SeededContentTests(TestCase):
    """Everything seeded for customers exists in every language (an empty value would fall back silently)."""

    def assertComplete(self, value, what):
        codes = [code for code, _name in settings.LANGUAGES]
        self.assertEqual({code for code in codes if (value or {}).get(code)}, set(codes), what)

    def test_pickup_points_cities_districts_tariff(self):
        site = SiteSettings.get()
        for row in site.delivery_cities + site.delivery_districts:
            self.assertComplete(row.get("name"), row.get("code"))
        for point in PickupPoint.objects.all():
            self.assertComplete(point.name_i18n, f"pickup point {point.pk} name")
            for field in ("address_i18n", "working_hours_i18n"):  # not seeded: all languages or none (launch data)
                filled = {code for code, value in (getattr(point, field) or {}).items() if value}
                self.assertIn(filled, (set(), {code for code, _n in settings.LANGUAGES}), field)
        for tariff in CustomerTariff.objects.all():  # benefits text: launch data, all languages or none
            filled = {code for code, value in (tariff.benefits_text_i18n or {}).items() if value}
            self.assertIn(filled, (set(), {code for code, _n in settings.LANGUAGES}), tariff.name)

    def test_every_system_text_and_title_renders_differently_per_language(self):
        for registry in (EVENT_TEXTS, DEFAULT_TITLES):
            for code, text in registry.items():
                renders = set()
                for language in ("ka", "en", "ru"):
                    with translation.override(language):
                        renders.add(str(text))
                self.assertEqual(len(renders), 3, code)


class TerminologyTests(SimpleTestCase):
    """Customer-facing courier texts use the same "delivery fee" wording as the banner (language QA finding)."""

    CUSTOMER_FACING = ["Please confirm the delivery fee.", "Delivery fee", "Customer accepted the courier fee: {amount}.",
                       "Customer declined the courier fee."]

    def test_delivery_fee_wording_is_consistent(self):
        for language in TRANSLATED:
            with translation.override(language):
                banner = gettext("Delivery fee").split()[0]  # the stem of the banner/title wording
                for msgid in self.CUSTOMER_FACING:
                    with self.subTest(language=language, msgid=msgid):
                        self.assertIn(banner[:5].lower(), gettext(msgid).lower())


class AdminLanguageTests(TestCase):
    def test_admin_follows_accept_language_and_defaults_to_georgian(self):
        client = APIClient()
        client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))
        from accounts.testing import make_customer
        from orders.testing import make_order

        make_order(make_customer())  # the actions menu is only shown with rows
        labels = {}
        for header in ("en", "ru", None):
            headers = {"accept-language": header} if header else {}
            labels[header] = client.get("/admin/orders/order/", headers=headers).content.decode()
        self.assertIn("Cancel order", labels["en"])
        for language, page in (("ru", labels["ru"]), ("ka", labels[None])):
            with translation.override(language):
                self.assertIn(gettext("Cancel order"), page)
