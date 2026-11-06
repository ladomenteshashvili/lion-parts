"""LP-BE-207: templated, translated emails (G§5.8, G§7.11)."""

import smtplib
import socket
from unittest import mock

from django.conf import settings
from django.core import mail
from django.test import SimpleTestCase, override_settings
from django.utils import translation

from api.mail import SUBJECTS, frontend_link, send_templated_email, transactional_footer

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


class ReplyToTests(SimpleTestCase):
    """LPK-184: the header is added only when configured, on templated emails."""

    def send(self):
        mail.outbox = []
        self.assertTrue(send_templated_email("n@example.com", "password_reset", {**CONTEXT, "link": "L"}, "en"))
        return mail.outbox[0]

    @override_settings(EMAIL_REPLY_TO="")
    def test_not_configured_means_no_header(self):
        message = self.send()
        self.assertEqual(message.reply_to, [])
        self.assertNotIn("Reply-To", message.message())

    @override_settings(EMAIL_REPLY_TO="Lion Parts Support <support@example.com>")
    def test_configured_sets_the_header_and_keeps_the_sender(self):
        message = self.send()
        self.assertEqual(message.message()["Reply-To"], "Lion Parts Support <support@example.com>")
        self.assertEqual(message.from_email, settings.DEFAULT_FROM_EMAIL)


class FooterTests(SimpleTestCase):
    """LPK-185: identity, purpose, support route and reply guidance in every language, plain and HTML."""

    def rendered(self, language):
        mail.outbox = []
        send_templated_email("n@example.com", "password_reset", {**CONTEXT, "link": "L"}, language)
        return mail.outbox[0].body, mail.outbox[0].alternatives[0].content

    @override_settings(EMAIL_REPLY_TO="Support Team <support@example.com>")
    def test_with_a_mailbox_it_names_the_address_in_each_language(self):
        seen = set()
        for language in ("ka", "en", "ru"):
            with self.subTest(language=language):
                text, html = self.rendered(language)
                for part in (text, html):
                    self.assertIn("Lion Parts", part)
                    self.assertIn("support@example.com", part)
                    self.assertNotIn("Support Team", part)
                    self.assertNotIn("unsubscribe", part.lower())
                with translation.override(language):
                    self.assertIn(transactional_footer(), text)
                    seen.add(transactional_footer())
        self.assertEqual(len(seen), 3)  # really translated

    @override_settings(EMAIL_REPLY_TO="", FRONTEND_BASE_URL="https://lionparts.ge/")
    def test_without_a_mailbox_it_does_not_invite_replies(self):
        with translation.override("en"):
            footer = transactional_footer()
        self.assertIn("Please do not reply to this email", footer)
        self.assertIn("https://lionparts.ge", footer)
        self.assertNotIn("reply to this email.", footer.replace("do not reply to this email.", ""))

    @override_settings(EMAIL_REPLY_TO="", FRONTEND_BASE_URL="https://x.example/?a=1&b=<2>")
    def test_the_html_footer_stays_escaped(self):
        _text, html = self.rendered("en")
        self.assertIn("a=1&amp;b=&lt;2&gt;", html)
        self.assertNotIn("<2>", html)


@override_settings(MAILERS={"default": {"BACKEND": "django.core.mail.backends.smtp.EmailBackend", "OPTIONS": {
    "host": "smtp.invalid", "port": 587, "username": "", "password": "", "use_tls": False, "timeout": 7}}})
class SmtpTimeoutTests(SimpleTestCase):
    """LPK-181, through the real SMTP backend. smtplib and the DNS lookup are mocked: nothing touches the network."""

    TOKEN = "SECRET-TOKEN"
    RECIPIENT = "private-recipient@example.com"

    def setUp(self):
        patches = (mock.patch("django.core.mail.backends.smtp.smtplib.SMTP"),
                   mock.patch("django.core.mail.backends.smtp.DNS_NAME"))
        self.smtp, dns = (patch.start() for patch in patches)
        dns.get_fqdn.return_value = "test.invalid"
        for patch in patches:
            self.addCleanup(patch.stop)

    def send(self):
        return send_templated_email(self.RECIPIENT, "password_reset",
                                    {**CONTEXT, "link": f"https://x.invalid/?token={self.TOKEN}"}, "en")

    def assert_fails_quietly(self):
        with self.assertLogs("api.mail", "ERROR") as logs:
            self.assertIs(self.send(), False)  # would raise here if the exception escaped
        record = logs.records[0]
        self.assertIsNone(record.exc_info)
        for private in (self.RECIPIENT, self.TOKEN, "x.invalid"):
            self.assertNotIn(private, logs.output[0])
        return record.getMessage()

    def test_the_configured_timeout_reaches_the_smtp_connection(self):
        self.assertIs(self.send(), True)
        self.assertEqual(self.smtp.call_args.kwargs["timeout"], 7)

    def test_a_timeout_while_connecting_returns_false(self):
        for error in (TimeoutError("timed out"), socket.timeout("timed out")):
            with self.subTest(error=type(error).__name__):
                self.smtp.side_effect = error
                self.assertEqual(self.assert_fails_quietly(), "Sending the password_reset email failed: TimeoutError")

    def test_a_timeout_while_sending_returns_false(self):
        self.smtp.return_value.sendmail.side_effect = socket.timeout("timed out")
        self.assertEqual(self.assert_fails_quietly(), "Sending the password_reset email failed: TimeoutError")

    def test_smtp_errors_never_put_the_recipient_or_token_in_the_log(self):
        errors = (smtplib.SMTPRecipientsRefused({self.RECIPIENT: (550, b"no")}),
                  TimeoutError(f"{self.RECIPIENT} {self.TOKEN}"), OSError(f"https://x.invalid/?token={self.TOKEN}"))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.smtp.return_value.sendmail.side_effect = error
                self.assert_fails_quietly()

    def test_a_successful_send_is_unchanged(self):
        self.assertIs(self.send(), True)
        args = self.smtp.return_value.sendmail.call_args.args
        self.assertEqual(self.smtp.return_value.sendmail.call_count, 1)
        self.assertEqual(args[1], [self.RECIPIENT])
        self.assertIn(self.TOKEN, args[2].decode())
