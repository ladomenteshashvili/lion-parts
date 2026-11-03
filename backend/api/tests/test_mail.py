"""LP-BE-207: templated, translated emails (G§5.8, G§7.11)."""

import smtplib
from unittest import mock

from django.core import mail
from django.test import SimpleTestCase, override_settings
from django.utils import translation

from api.mail import SUBJECTS, frontend_link, send_templated_email

CONTEXT = {"name": "Nino", "new_email": "new@example.com"}


@override_settings(FRONTEND_BASE_URL="https://lionparts.ge/")
class SendTemplatedEmailTests(SimpleTestCase):
    def test_frontend_link_carries_params_and_language(self):
        self.assertEqual(frontend_link("/reset-password", "ru", uid="MTI", token="a-b"),
                         "https://lionparts.ge/reset-password?uid=MTI&token=a-b&lang=ru")

    def test_every_template_renders_with_its_link_and_reaches_the_outbox(self):
        for template in SUBJECTS:
            with self.subTest(template=template):
                mail.outbox = []
                link = frontend_link("/verify-email", "en", token="t1")
                self.assertTrue(send_templated_email("nino@example.com", template, {**CONTEXT, "link": link}, "en"))
                message = mail.outbox[0]
                with translation.override("en"):
                    self.assertEqual((message.to, message.subject), (["nino@example.com"], str(SUBJECTS[template])))
                html = message.alternatives[0].content
                if template != "email_changed_notice":
                    self.assertIn(link, message.body)  # plain text keeps "&", not "&amp;"
                    self.assertIn(link.replace("&", "&amp;"), html)
                else:
                    self.assertIn("new@example.com", message.body)

    def test_russian_customer_gets_a_russian_email_and_lang_ru_link(self):
        link = frontend_link("/verify-email", "ru", token="t1")
        send_templated_email("nino@example.com", "verify_email", {**CONTEXT, "link": link}, "ru")
        message = mail.outbox[0]
        self.assertEqual(message.subject, "Подтвердите адрес электронной почты")
        self.assertIn("Здравствуйте, Nino!", message.body)
        self.assertIn("lang=ru", message.body)

    def test_smtp_failure_returns_false_and_logs(self):
        with mock.patch("api.mail.EmailMultiAlternatives.send", side_effect=smtplib.SMTPException("down")), \
                self.assertLogs("api.mail", "ERROR") as logs:
            ok = send_templated_email("nino@example.com", "password_reset", {**CONTEXT, "link": "x"}, "ka")
        self.assertFalse(ok)
        self.assertIn("password_reset", logs.output[0])

    def test_every_template_is_translated(self):
        for template in SUBJECTS:
            rendered = {}
            for lang in ("en", "ka", "ru"):
                mail.outbox = []
                send_templated_email("n@example.com", template, {**CONTEXT, "link": "L"}, lang)
                rendered[lang] = (mail.outbox[0].subject, mail.outbox[0].body)
            for lang in ("ka", "ru"):
                with self.subTest(template=template, language=lang):
                    self.assertNotEqual(rendered[lang][0], rendered["en"][0])
                    self.assertNotEqual(rendered[lang][1], rendered["en"][1])
