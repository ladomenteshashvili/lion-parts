"""LP-BE-1305: notification templates per language (G§6.5, G§7.11)."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import translation

from accounts.testing import make_customer
from orders.models import EVENT_TYPES, Notification, NotificationTemplate
from orders.notifications import notification_title, pick_template, render_text, template_context
from orders.testing import make_order


class TemplateSeedTests(TestCase):
    def test_every_event_has_ka_en_ru(self):
        self.assertEqual(set(NotificationTemplate.objects.values_list("event_type", "language")),
                         {(e, lang) for e in EVENT_TYPES for lang in ("ka", "en", "ru")})

    def test_seeded_texts_hold_no_amounts_names_or_operator_text(self):
        for template in NotificationTemplate.objects.all():
            text = template.sms_template + template.email_template
            for forbidden in ("amount", "total", "balance", "customer", "message", "first_name", "phone", "email"):
                self.assertNotIn(forbidden, text, (template, forbidden))
            self.assertIn("{{ link }}", template.sms_template)


class LookupTests(TestCase):
    def setUp(self):
        self.order = make_order(make_customer())
        self.notification = Notification.objects.create(customer=self.order.customer, event_type="status_update",
                                                        order=self.order)

    def render(self, language):
        template = pick_template("status_update", language)
        return render_text(template.sms_template, template_context(self.notification, "https://x/n/t?lang=en", "T"))

    def test_language_and_fallback(self):
        self.assertEqual(self.render("en"),
                         f"Lion Parts: the status of order {self.order.order_number} was updated. https://x/n/t?lang=en")
        NotificationTemplate.objects.filter(event_type="status_update", language="ru").delete()
        self.assertEqual(pick_template("status_update", "ru").language, settings.LANGUAGE_CODE)
        self.assertIn(self.order.order_number, self.render("ru"))  # Georgian text, no crash

    def test_restricted_context_and_plain_text(self):
        NotificationTemplate.objects.filter(event_type="status_update", language="en").update(
            sms_template="{{ order.customer.user.password }}|{{ customer }}|<b>{{ order.order_number }}</b> & co")
        self.assertEqual(self.render("en"), f"||<b>{self.order.order_number}</b> & co")

    def test_titles_follow_the_active_language(self):
        with translation.override("en"):
            self.assertEqual(notification_title(self.notification), "Order status updated")
        with translation.override("ru"):
            self.assertNotEqual(notification_title(self.notification), "Order status updated")


class AdminTests(TestCase):
    def test_a_broken_template_is_refused_by_the_admin_form(self):
        from orders.admin import NotificationTemplateForm

        data = {"language": "en", "sms_template": "{% if %}", "email_subject": "ok", "email_template": "ok"}
        form = NotificationTemplateForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("sms_template", form.errors)
        self.assertTrue(NotificationTemplateForm(data={**data, "sms_template": "{{ link }}"}).is_valid())

    def test_channel_page_shows_the_templates_inline(self):
        from orders.models import NotificationChannelSetting

        self.client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))
        setting = NotificationChannelSetting.objects.get(event_type="status_update")
        page = self.client.get(f"/admin/orders/notificationchannelsetting/{setting.pk}/change/")
        self.assertContains(page, "templates-2-sms_template")  # ka, en, ru


class TagRestrictionTests(TestCase):
    """M5 audit fix 2: only {% if %} logic in staff-edited templates."""

    def test_other_tags_are_refused_in_the_admin_and_never_rendered(self):
        from django.core import mail
        from django.template import TemplateSyntaxError
        from django.test import override_settings

        from orders.admin import NotificationTemplateForm
        from orders.models import NotificationChannelSetting
        from orders.notifications import check_template, notify

        for source in ("{% debug %}", "{% include 'admin/base.html' %}", "{% load static %}", "{%- now 'Y' %}"):
            with self.subTest(source=source):
                with self.assertRaises(TemplateSyntaxError):
                    check_template(source)
                self.assertFalse(NotificationTemplateForm(data={"language": "en", "sms_template": source,
                                                                "email_subject": "", "email_template": ""}).is_valid())
        check_template("{% if order %}{{ order.order_number }}{% else %}-{% endif %}")
        customer = make_customer(preferred_language="en")
        NotificationChannelSetting.objects.filter(event_type="status_update").update(channel="email")
        NotificationTemplate.objects.filter(event_type="status_update", language="en").update(
            email_template="{% debug %}")
        with override_settings(SENDER_GE_ENABLED=False), self.assertLogs("orders.notifications", "ERROR"):
            n = notify(customer, "status_update", order=make_order(customer))
        self.assertEqual((n.deliveries.count(), len(mail.outbox)), (0, 0))
