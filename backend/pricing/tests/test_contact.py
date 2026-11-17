"""LPK-230, LPK-231, LPK-232: public contact details in Site settings and GET /api/meta/."""

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import translation

from pricing.models import SiteSettings

from .test_admin import AdminTestCase, site_form

CONTACT_KEYS = {"phone", "email", "address", "working_hours", "whatsapp_url"}
EMPTY = dict.fromkeys(CONTACT_KEYS, "")


def set_contact(**fields):
    SiteSettings.objects.filter(pk=SiteSettings.get().pk).update(**fields)


class ContactValidationTests(TestCase):
    def errors(self, **fields):
        site = SiteSettings.get()
        for name, value in fields.items():
            setattr(site, name, value)
        try:
            site.full_clean()
        except ValidationError as error:
            return set(error.message_dict)
        return set()

    def test_unset_by_default_and_valid(self):
        site = SiteSettings.get()
        self.assertEqual((site.contact_phone, site.contact_email, site.contact_address_i18n,
                          site.contact_working_hours_i18n, site.contact_whatsapp_url), ("", "", {}, {}, ""))
        self.assertEqual(self.errors(), set())

    def test_phone_numbers(self):
        for good in ("+995 32 200 00 00", "+995555123456", "(032) 2-00-00-00", "555 12 34 56"):
            with self.subTest(good=good):
                self.assertEqual(self.errors(contact_phone=good), set())
        for bad in ("12345", "call us", "+995 555 12 34 56 ext", "+", "555-", "+995 <b>5</b>", "tel:+995555123456",
                    "1" * 31):
            with self.subTest(bad=bad):
                self.assertEqual(self.errors(contact_phone=bad), {"contact_phone"})

    def test_email(self):
        self.assertEqual(self.errors(contact_email="info@example.com"), set())
        self.assertEqual(self.errors(contact_email="not-an-email"), {"contact_email"})

    def test_whatsapp_link_only_leads_to_whatsapp(self):
        for good in ("https://wa.me/995555123456", "https://api.whatsapp.com/send?phone=995555123456"):
            with self.subTest(good=good):
                self.assertEqual(self.errors(contact_whatsapp_url=good), set())
        for bad in ("http://wa.me/995555123456", "https://wa.me.example.com/1", "https://example.com/wa.me/1",
                    "https://wa.me@example.com/1", "https://user@wa.me/1", "https://wa.me:8443/1",
                    "https://wa.me:abc/1", "javascript:alert(1)", "https://WA.ME/1"):
            with self.subTest(bad=bad):
                self.assertEqual(self.errors(contact_whatsapp_url=bad), {"contact_whatsapp_url"})


class MetaContactTests(TestCase):
    def contact(self, language=None):
        headers = {"accept-language": language} if language else {}
        return self.client.get("/api/meta/", headers=headers).json()["contact"]

    def test_unset_contact_is_empty_strings_and_other_keys_stay(self):
        data = self.client.get("/api/meta/").json()
        self.assertEqual(data["contact"], EMPTY)
        self.assertIn("pickup_points", data)  # existing keys unchanged: api/tests/test_meta.py checks the full set

    def test_values_and_texts_in_the_request_language(self):
        set_contact(contact_phone="+995 32 200 00 00", contact_email="info@example.com",
                    contact_address_i18n={"ka": "Address (ka)", "en": "Address", "ru": "Address (ru)"},
                    contact_working_hours_i18n={"ka": "Hours (ka)"},
                    contact_whatsapp_url="https://wa.me/995555123456")
        self.assertEqual(self.contact(), {"phone": "+995 32 200 00 00", "email": "info@example.com",
                                          "address": "Address (ka)", "working_hours": "Hours (ka)",
                                          "whatsapp_url": "https://wa.me/995555123456"})
        self.assertEqual(self.contact("ru")["address"], "Address (ru)")
        self.assertEqual(self.contact("en")["address"], "Address")
        self.assertEqual(self.contact("en")["working_hours"], "Hours (ka)")  # falls back to the default language

    def test_only_contact_fields_are_published(self):
        """The invoice seller's details are a separate, internal block, even when they hold the same kind of data."""
        set_contact(invoice_seller_phone="+995 32 111 11 11", invoice_seller_email="billing@example.com",
                    invoice_seller_code="123456789", invoice_bank_details_i18n={"ka": "IBAN GE00"})
        body = self.client.get("/api/meta/").content.decode()
        self.assertEqual(self.contact(), EMPTY)
        for private in ("111 11 11", "billing@example.com", "123456789", "IBAN"):
            self.assertNotIn(private, body)

    def test_no_extra_query_for_contact(self):
        self.client.get("/api/meta/")  # the settings row exists from the seed; warm anything lazy
        # Same as before LPK-231: contact comes from the settings row already read. (The currency helpers read the
        # row twice more; that predates this change.)
        with self.assertNumQueries(5):
            self.client.get("/api/meta/")

    def test_public_and_read_only(self):
        self.assertEqual(self.client.get("/api/meta/").status_code, 200)  # no session needed
        self.assertEqual(self.client.post("/api/meta/").status_code, 405)


class ContactAdminTests(AdminTestCase):
    URL = "/admin/pricing/sitesettings/1/change/"

    def test_operator_sets_and_clears_contact_details(self):
        response = self.client.post(self.URL, site_form(
            contact_phone="+995 32 200 00 00", contact_email="info@example.com",
            contact_whatsapp_url="https://wa.me/995555123456",
            contact_address_i18n_0="Address (ka)", contact_address_i18n_2="Address (ru)",
            contact_working_hours_i18n_0="10:00-19:00"))
        self.assertEqual(response.status_code, 302, response.content[:3000])
        site = SiteSettings.get()
        self.assertEqual(site.contact_address_i18n, {"ka": "Address (ka)", "ru": "Address (ru)"})
        self.assertEqual(site.contact_working_hours_i18n, {"ka": "10:00-19:00"})

        response = self.client.post(self.URL, site_form(contact_phone="", contact_address_i18n_0="",
                                                        contact_address_i18n_2=""))
        self.assertEqual(response.status_code, 302, response.content[:3000])
        site = SiteSettings.get()
        self.assertEqual((site.contact_phone, site.contact_address_i18n), ("", {}))

    def test_invalid_values_are_refused_with_the_field_error(self):
        response = self.client.post(self.URL, site_form(contact_whatsapp_url="https://example.com/chat",
                                                        contact_phone="call us"))
        self.assertEqual(response.status_code, 200)
        errors = response.context["adminform"].form.errors
        self.assertEqual(set(errors), {"contact_whatsapp_url", "contact_phone"})
        self.assertEqual(SiteSettings.get().contact_whatsapp_url, "")

    def test_a_translation_needs_the_georgian_text(self):
        """The house rule for translated fields: optional, but once filled the default language is required, so
        every language has something to fall back to."""
        response = self.client.post(self.URL, site_form(contact_address_i18n_2="Address (ru)"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context["adminform"].form.errors), {"contact_address_i18n"})

    def test_labels_in_georgian_and_russian(self):
        for language in ("ka", "ru"):
            with self.subTest(language=language):
                page = self.client.get(self.URL, headers={"accept-language": language}).content.decode()
                with translation.override(language):
                    for text in ("Contact details", "Shown to customers on the website. Leave a field empty to hide it.",
                                 "contact phone", "WhatsApp link", "working hours"):
                        translated = translation.gettext(text)
                        self.assertNotEqual(translated, text)
                        self.assertIn(translated[1:], page)  # labels are capitalized; Georgian has no case
