"""LPK-246: the branded layout of the account emails (button, footer contacts, logo, translations)."""

from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import translation
from django.utils.translation import gettext

from api.mail import frontend_link, send_templated_email
from pricing.models import SiteSettings

BUTTONS = {"verify_email": ("Confirm email", "Подтвердить почту"),
           "password_reset": ("Set a new password", "Задать новый пароль"),
           "email_change_confirm": ("Confirm new email", "Подтвердить новую почту")}
NEW = ("Button not working? Copy this link into your browser:", "Car parts from the USA, priced by part number",
       "You received this email because this address is on a Lion Parts account. It was sent automatically.")


def translated(language, msgid):
    with translation.override(language):
        return gettext(msgid)


@override_settings(FRONTEND_BASE_URL="https://test2.lionparts.ge/")
class EmailLayoutTests(TestCase):
    def send(self, template="verify_email", language="en"):
        mail.outbox = []
        link = frontend_link("/verify-email", language, token="t1")
        self.assertTrue(send_templated_email("nino@example.com", template,
                                             {"name": "Nino", "new_email": "new@example.com", "link": link}, language))
        message = mail.outbox[0]
        return message, message.alternatives[0].content, link

    def contacts(self, **values):
        site = SiteSettings.get()
        for field in ("contact_phone", "contact_email", "facebook_url", "instagram_url"):
            setattr(site, field, values.get(field, ""))
        site.save()

    def test_each_link_email_has_one_red_button_and_a_fallback_link(self):
        for template, (label, _ru) in BUTTONS.items():
            with self.subTest(template=template):
                message, html, link = self.send(template)
                escaped = link.replace("&", "&amp;")
                self.assertIn(f'<a href="{escaped}" style="display:inline-block;', html)
                self.assertIn(f">{label}</a>", html)
                self.assertEqual(html.count('bgcolor="#d71920"'), 1)
                self.assertIn("Button not working? Copy this link into your browser:", html)
                self.assertEqual(html.count(f'href="{escaped}"'), 2)  # the button and the visible fallback
                self.assertIn(f"<h1 style=\"margin:0 0 16px;font-size:21px;line-height:1.35;font-weight:700;"
                              f"color:#111111;\">{message.subject}</h1>", html)
                # the text version stays plain text with the raw link
                self.assertIn(link, message.body)
                self.assertNotIn("<", message.body)
        _message, html, _link = self.send("email_changed_notice")
        self.assertNotIn('bgcolor="#d71920"', html)
        self.assertIn("new@example.com", html)

    def test_logo_and_site_link_are_absolute_https_for_the_environment(self):
        _message, html, _link = self.send()
        self.assertIn('<img src="https://test2.lionparts.ge/email/logo-on-dark.png" width="54" height="44" '
                      'alt="Lion Parts"', html)
        self.assertIn('<a href="https://test2.lionparts.ge/">', html)
        self.assertIn(">test2.lionparts.ge</td>", html)
        with override_settings(FRONTEND_BASE_URL="https://lionparts.ge"):
            _message, html, _link = self.send()
        self.assertIn('src="https://lionparts.ge/email/logo-on-dark.png"', html)
        for forbidden in ("<svg", "<script", "<style", "fonts.googleapis"):
            self.assertNotIn(forbidden, html)

    def test_footer_shows_the_contacts_that_are_set(self):
        self.contacts(contact_phone="+995 555 12 34 56", contact_email="info@lionparts.ge",
                      facebook_url="https://facebook.com/lionparts", instagram_url="https://instagram.com/lionparts")
        _message, html, _link = self.send()
        for part in ('href="tel:+995555123456"', ">+995 555 12 34 56</a>", 'href="mailto:info@lionparts.ge"',
                     'href="https://facebook.com/lionparts"', ">Facebook</a>", 'href="https://instagram.com/lionparts"',
                     ">Instagram</a>"):
            self.assertIn(part, html)
        self.assertEqual(html.count("&nbsp;·&nbsp;"), 4)

    def test_empty_contacts_leave_no_link_or_separator(self):
        self.contacts(contact_email="info@lionparts.ge")
        _message, html, _link = self.send()
        self.assertEqual(html.count("&nbsp;·&nbsp;"), 1)
        for absent in ("tel:", "Facebook", "Instagram"):
            self.assertNotIn(absent, html)
        self.contacts()
        _message, html, _link = self.send()
        self.assertNotIn("&nbsp;·&nbsp;", html)
        self.assertNotIn("mailto:", html)

    def test_contact_values_are_escaped(self):
        self.contacts(contact_email="a@b.ge", facebook_url='https://facebook.com/x"><script>')
        _message, html, _link = self.send()
        self.assertNotIn('"><script>', html)
        self.assertIn("&quot;&gt;&lt;script&gt;", html)

    def test_without_site_settings_the_email_still_goes_out(self):
        with mock.patch("pricing.models.SiteSettings.get", side_effect=RuntimeError("db down")), \
                self.assertLogs("api.mail", "WARNING") as logs:
            _message, html, link = self.send()
        self.assertIn("Confirm email", html)
        self.assertNotIn("t1", "".join(logs.output))  # neither the token nor the body is logged

    def test_georgian_and_russian(self):
        for language in ("ka", "ru"):
            for template, (label, russian) in BUTTONS.items():
                with self.subTest(language=language, template=template):
                    _message, html, _link = self.send(template, language)
                    self.assertIn(f'<html lang="{language}">', html)
                    expected = translated(language, label)
                    self.assertNotEqual(expected, label)
                    if language == "ru":
                        self.assertEqual(expected, russian)  # the spec's wording
                    self.assertIn(f">{expected}</a>", html)
                    for msgid in NEW:
                        self.assertNotEqual(translated(language, msgid), msgid)
                        self.assertIn(translated(language, msgid).split(":")[0], html)
                    for english in (label, "Button not working?", "Car parts from the USA", "You received this email"):
                        self.assertNotIn(english, html)
        _message, html, _link = self.send("verify_email", "ru")
        self.assertIn("Вы получили это письмо, потому что этот адрес указан в аккаунте Lion Parts.", html)
