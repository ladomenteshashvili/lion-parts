"""LP-BE-1302: translated content fields, tr() and the admin widget (G§5.8)."""

from django.contrib.admin.sites import site
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import translation

from accounts.models import CustomerTariff
from api.admin_i18n import TranslatedJSONField, TranslatedJSONWidget
from api.i18n import tr

FOUR_LANGUAGES = [("ka", "Georgian"), ("en", "English"), ("ru", "Russian"), ("de", "Deutsch")]


class TrTests(SimpleTestCase):
    def test_fallback_order(self):
        value = {"ka": "Vake-ka", "en": "Vake"}
        self.assertEqual(tr(value, "ru"), "Vake-ka")  # default language
        self.assertEqual(tr(value, "en"), "Vake")
        self.assertEqual(tr({"ru": "", "en": "Only en"}, "ru"), "Only en")  # any non-empty value
        self.assertEqual(tr({}, "en"), "")
        self.assertEqual(tr(None), "")

    def test_uses_the_active_language_and_ignores_the_region(self):
        with translation.override("en-us"):
            self.assertEqual(tr({"ka": "k", "en": "e"}), "e")


class TranslatedJSONFieldTests(SimpleTestCase):
    def test_one_input_per_configured_language(self):
        self.assertEqual(len(TranslatedJSONWidget().widgets), 3)
        with override_settings(LANGUAGES=FOUR_LANGUAGES):
            html = TranslatedJSONWidget().render("name_i18n", {"ka": "k", "de": "d"})
        self.assertEqual(html.count("<input"), 4)
        self.assertIn("Deutsch (de)", html)
        self.assertIn('value="d"', html)

    def test_compress_and_validation(self):
        field = TranslatedJSONField(required=False)
        self.assertEqual(field.clean(["Vake-ka", " Vake ", ""]), {"ka": "Vake-ka", "en": "Vake"})
        self.assertEqual(field.clean(["", "", ""]), {})  # optional field may stay empty
        with self.assertRaises(ValidationError) as ctx:
            field.clean(["", "Vake", ""])  # something entered, default language missing
        self.assertEqual(ctx.exception.messages, [str(field.fields[0].error_messages["incomplete"])])
        with self.assertRaises(Exception):
            TranslatedJSONField(required=True).clean(["", "", ""])


class TariffAdminTranslatedFieldTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser("root", "root@example.com", "x")
        self.client.force_login(self.admin_user)
        self.tariff = CustomerTariff.objects.get(is_default=True)
        self.url = f"/admin/accounts/customertariff/{self.tariff.pk}/change/"

    def form_class(self):
        request = type("R", (), {"user": self.admin_user})()
        return site._registry[CustomerTariff].get_form(request, self.tariff)

    def test_change_page_shows_one_input_per_language(self):
        html = self.client.get(self.url).content.decode()
        for name in ("benefits_text_i18n_0", "benefits_text_i18n_1", "benefits_text_i18n_2"):
            self.assertIn(f'name="{name}"', html)

    def test_saving_without_the_default_language_fails(self):
        data = {"name": "Standard", "is_default": "on", "payment_terms": "full", "split_upfront_percent": "70",
                "benefits_text_i18n_0": "", "benefits_text_i18n_1": "Free delivery", "benefits_text_i18n_2": ""}
        form = self.form_class()(data, instance=self.tariff)
        self.assertFalse(form.is_valid())
        self.assertIn("benefits_text_i18n", form.errors)
        data["benefits_text_i18n_0"] = "Free delivery (ka)"
        form = self.form_class()(data, instance=self.tariff)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().benefits_text_i18n, {"ka": "Free delivery (ka)", "en": "Free delivery"})

    @override_settings(LANGUAGES=FOUR_LANGUAGES)
    def test_added_language_appears_without_code_changes(self):
        self.assertEqual(len(self.form_class().base_fields["benefits_text_i18n"].fields), 4)
