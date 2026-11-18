"""LPK-230, LPK-231, LPK-232: contact page details in Site settings and GET /api/meta/ "contact"."""

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import translation

from api.tests import contract as C
from pricing.admin import SiteSettingsAdmin
from pricing.models import SiteSettings

from .test_admin import AdminTestCase, site_form

# The fixed frontend contract: API key -> model field.
FIELDS = {"phone": "contact_phone", "email": "contact_email", "whatsapp": "whatsapp_phone", "viber": "viber_phone",
          "facebook": "facebook_url", "instagram": "instagram_url"}
PHONE_FIELDS = ("contact_phone", "whatsapp_phone", "viber_phone")
URL_FIELDS = ("facebook_url", "instagram_url")
VALUES = {"contact_phone": "+995 32 200 00 00", "contact_email": "info@example.com",
          "whatsapp_phone": "+995 555 12 34 56", "viber_phone": "+995 (555) 12-34-56",
          "facebook_url": "https://www.facebook.com/example", "instagram_url": "https://www.instagram.com/example/"}


def set_contact(**fields):
    SiteSettings.objects.filter(pk=SiteSettings.get().pk).update(**fields)


class ContactValidationTests(TestCase):
    def errors(self, **fields):
        site = SiteSettings.get()
        for name, value in fields.items():
            setattr(site, name, value)
        try:
            with translation.override("en"):
                site.full_clean()
        except ValidationError as error:
            with translation.override("en"):
                return {field: [str(m) for m in messages] for field, messages in error.message_dict.items()}
        return {}

    def test_empty_by_default_and_valid(self):
        site = SiteSettings.get()
        self.assertEqual([getattr(site, f) for f in FIELDS.values()], [""] * 6)
        self.assertEqual(self.errors(), {})

    def test_all_values_valid(self):
        self.assertEqual(self.errors(**VALUES), {})

    def test_phone_formats_on_all_three_fields(self):
        good = ("+995 555 12 34 56", "+995555123456", "+995 (32) 2-00-00-00", "+1 212 555 0100", "+44 20 7946 0958")
        bad = ("555 12 34 56", "call me", "+995<script>", "+995 555 12 34 56 ext. 1", "tel:+995555123456",
               "+------", "+(((())))", "+12345", "+995 555 12 34 56\n", "+9955551234567890123456")
        for field in PHONE_FIELDS:
            for value in good:
                with self.subTest(field=field, good=value):
                    self.assertEqual(self.errors(**{field: value}), {})
            for value in bad:
                with self.subTest(field=field, bad=value):
                    self.assertEqual(self.errors(**{field: value}),
                                     {field: ["Enter a phone number like +995 555 12 34 56."]})

    def test_email(self):
        self.assertEqual(set(self.errors(contact_email="not-an-email")), {"contact_email"})

    def test_links_are_web_links_only(self):
        for field in URL_FIELDS:
            for bad in ("javascript:alert(1)", "ftp://example.com/page", "data:text/html,x", "www.facebook.com/x"):
                with self.subTest(field=field, bad=bad):
                    self.assertEqual(set(self.errors(**{field: bad})), {field})
            with self.subTest(field=field, good="http"):
                self.assertEqual(self.errors(**{field: "http://example.com/page"}), {})


class MetaContactTests(TestCase):
    def contact(self, **headers):
        return self.client.get("/api/meta/", headers=headers).json()["contact"]

    def test_six_keys_empty_by_default(self):
        data = self.client.get("/api/meta/").json()
        self.assertEqual(data["contact"], dict.fromkeys(C.CONTACT, ""))
        self.assertEqual(set(data), C.META)  # every existing key is still there

    def test_saved_values_are_returned_as_stored(self):
        set_contact(**VALUES)
        self.assertEqual(self.contact(), {key: VALUES[field] for key, field in FIELDS.items()})

    def test_same_values_in_every_language(self):
        set_contact(**VALUES)
        expected = self.contact()
        for language in ("ka", "en", "ru"):
            with self.subTest(language=language):
                self.assertEqual(self.contact(accept_language=language), expected)
                self.assertEqual(self.client.get(f"/api/meta/?lang={language}").json()["contact"], expected)

    def test_contract_names_exactly_these_keys(self):
        self.assertEqual(C.CONTACT, set(FIELDS))
        self.assertEqual(set(self.contact()), C.CONTACT)

    def test_invoice_seller_details_stay_private(self):
        set_contact(invoice_seller_phone="+995 32 111 11 11", invoice_seller_email="billing@example.com",
                    invoice_seller_code="123456789", invoice_bank_details_i18n={"ka": "IBAN GE00"})
        body = self.client.get("/api/meta/").content.decode()
        self.assertEqual(self.contact(), dict.fromkeys(C.CONTACT, ""))
        for private in ("111 11 11", "billing@example.com", "123456789", "IBAN"):
            self.assertNotIn(private, body)

    def test_no_extra_query(self):
        self.client.get("/api/meta/")
        # 5, as before LPK-231: contact comes from the settings row already read. (The currency helpers read that row
        # twice more; that predates this change.)
        with self.assertNumQueries(5):
            self.client.get("/api/meta/")

    def test_public_and_read_only(self):
        self.assertEqual(self.client.get("/api/meta/").status_code, 200)
        self.assertEqual(self.client.post("/api/meta/").status_code, 405)


class ContactAdminTests(AdminTestCase):
    URL = "/admin/pricing/sitesettings/1/change/"

    def test_operator_saves_and_clears_all_six(self):
        response = self.client.post(self.URL, site_form(**VALUES))
        self.assertEqual(response.status_code, 302, response.content[:3000])
        site = SiteSettings.get()
        self.assertEqual({f: getattr(site, f) for f in VALUES}, VALUES)

        response = self.client.post(self.URL, site_form(**dict.fromkeys(VALUES, "")))
        self.assertEqual(response.status_code, 302, response.content[:3000])
        site = SiteSettings.get()
        self.assertEqual([getattr(site, f) for f in VALUES], [""] * 6)

    def test_invalid_values_are_refused_per_field(self):
        response = self.client.post(self.URL, site_form(whatsapp_phone="555 12 34 56", viber_phone="call me",
                                                        contact_phone="+995<script>",
                                                        facebook_url="javascript:alert(1)"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context["adminform"].form.errors),
                         {"whatsapp_phone", "viber_phone", "contact_phone", "facebook_url"})
        self.assertEqual(SiteSettings.get().whatsapp_phone, "")

    def test_fieldset_after_invoice_with_the_six_fields(self):
        with translation.override("en"):
            fieldsets = [(str(name), options) for name, options in SiteSettingsAdmin.fieldsets]
        names = [name for name, _options in fieldsets]
        self.assertEqual(names[names.index("Invoice") + 1], "Contact page")
        self.assertEqual(dict(fieldsets)["Contact page"]["fields"], tuple(FIELDS.values()))

    def test_georgian_and_russian_admin_texts(self):
        texts = ("Contact page", "contact phone", "contact email", "WhatsApp number", "Viber number", "Facebook page",
                 "Instagram page", "Enter a phone number like +995 555 12 34 56.",
                 "Shown on the website's contact page; an empty field is hidden. Pickup point addresses and working "
                 "hours come from Pickup points.")
        for language in ("ka", "ru"):
            with self.subTest(language=language):
                page = self.client.get(self.URL, headers={"accept-language": language}).content.decode()
                with translation.override(language):
                    for text in texts:
                        translated = translation.gettext(text)
                        self.assertNotEqual(translated, text, text)
                        self.assertIn(translated[1:].replace("'", "&#x27;"), page.replace("&quot;", '"'), text)
