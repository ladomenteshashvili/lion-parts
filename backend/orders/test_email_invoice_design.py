"""LPK-246: order notification emails get an HTML alternative; the invoice gets the A4 brand design. Same texts,
same fields, same amounts and the same order and payment state as before."""

import re
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.forms.models import model_to_dict
from django.test import TestCase, override_settings
from django.utils import translation
from django.utils.translation import gettext
from rest_framework.test import APIClient

from accounts.testing import make_customer
from api.mail import transactional_footer
from orders.models import Notification, NotificationChannelSetting, NotificationTemplate, Order, SupportTicket
from orders.notifications import notification_title, notify, notify_contact, render_text, template_context
from orders.testing import make_order
from parts.models import PartQuoteRequest
from pricing.models import PickupPoint, SiteSettings

D = Decimal


def channel(event_type, value):
    NotificationChannelSetting.objects.filter(event_type=event_type).update(channel=value)


@override_settings(FRONTEND_BASE_URL="https://lionparts.ge", SENDER_GE_ENABLED=False)
class NotificationEmailTests(TestCase):
    def setUp(self):
        self.customer = make_customer(preferred_language="en")
        self.order = make_order(self.customer)
        channel("status_update", "email")
        channel("price_ready", "email")

    def html(self, message):
        self.assertEqual([kind for _html, kind in message.alternatives], ["text/html"])
        return message.alternatives[0].content

    def test_multipart_with_the_order_number_and_the_link_and_the_same_text(self):
        n = notify(self.customer, "status_update", order=self.order)
        message = mail.outbox[-1]
        link = f"https://lionparts.ge/n/{n.token}?lang=en"
        template = NotificationTemplate.objects.get(event_type="status_update", language="en")
        with translation.override("en"):
            text = render_text(template.email_template, template_context(n, link, notification_title(n)))
            self.assertEqual(message.body, f"{text}\n\n{transactional_footer()}")  # the plain text is unchanged
        html = self.html(message)
        self.assertIn('<html lang="en">', html)
        self.assertIn(f'<strong style="font-family:Menlo,Consolas,monospace;color:#111111;">{self.order.order_number}'
                      f'</strong>', html)
        self.assertIn(f'<a href="{link}" style="display:inline-block;', html)
        self.assertIn(">View order</a>", html)
        heading = re.search(r"<h1[^>]*>(.*?)</h1>", html).group(1)
        bare = message.subject.removeprefix("Lion Parts: ")
        self.assertEqual(heading, bare[:1].upper() + bare[1:])  # "the status …" -> "The status …"
        self.assertIn(f"<title>{heading}</title>", html)
        self.assertEqual(n.deliveries.get().status, "sent")

    def test_a_price_ready_email_without_an_order_links_to_the_quote(self):
        qr = PartQuoteRequest.objects.create(customer=self.customer, request_type="manual_search", part_number="P-9")
        notify(self.customer, "price_ready", quote_request=qr)
        html = self.html(mail.outbox[-1])
        self.assertIn(f'href="https://lionparts.ge/q/{qr.notification_token}?lang=en"', html)
        self.assertIn(">View details</a>", html)
        self.assertNotIn("Menlo", html)  # no order line

    def test_admin_edited_text_is_escaped_in_html_and_left_alone_in_text(self):
        NotificationTemplate.objects.filter(event_type="status_update", language="en").update(
            email_template="<b>Bold</b> & <script>alert(1)</script>\nSecond line")
        notify(self.customer, "status_update", order=self.order)
        message = mail.outbox[-1]
        html = self.html(message)
        self.assertNotIn("<b>Bold</b>", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;b&gt;Bold&lt;/b&gt; &amp; &lt;script&gt;alert(1)&lt;/script&gt;<br>Second line", html)
        self.assertTrue(message.body.startswith("<b>Bold</b> & <script>alert(1)</script>\nSecond line"))

    def test_a_broken_html_render_sends_the_text_alone_and_logs_no_content(self):
        with mock.patch("django.template.loader.render_to_string", side_effect=RuntimeError("Secret body")), \
                self.assertLogs("orders.notifications", "WARNING") as logs:
            n = notify(self.customer, "status_update", order=self.order)
        message = mail.outbox[-1]
        self.assertEqual((message.alternatives, n.deliveries.get().status), ([], "sent"))
        output = "".join(logs.output)
        for private in (str(n.token), "Secret body", self.order.order_number, message.body[:20]):
            self.assertNotIn(private, output)

    def test_georgian_and_russian(self):
        for language in ("ka", "ru"):
            with self.subTest(language=language), translation.override(language):
                view, order_word = gettext("View order"), gettext("Order")
                self.assertNotIn(view, ("View order", ""))
                self.customer.preferred_language = language
                self.customer.save(update_fields=["preferred_language"])
                notify(self.customer, "status_update", order=self.order, params={"language": language})
                html = self.html(mail.outbox[-1])
                self.assertIn(f'<html lang="{language}">', html)
                self.assertIn(f">{view}</a>", html)
                self.assertIn(f"{order_word}\n  <strong", html)
                self.assertNotIn("View order", html)

    def test_sending_leaves_order_and_payment_state_alone(self):
        before = model_to_dict(Order.objects.get(pk=self.order.pk))
        items = list(self.order.items.values())
        channel("payment_update", "both")
        notify(self.customer, "payment_update", order=self.order)
        self.assertEqual(model_to_dict(Order.objects.get(pk=self.order.pk)), before)
        self.assertEqual(list(self.order.items.values()), items)


@override_settings(FRONTEND_BASE_URL="https://lionparts.ge", SENDER_GE_ENABLED=False)
class GuestReplyEmailTests(TestCase):
    def test_guest_reply_has_html_without_a_button_or_account_wording(self):
        channel("support_reply", "email")
        ticket = SupportTicket.objects.create(contact="x@y.ge", language="en", context="general", message="Help")
        notify_contact(ticket, "We found it <img src=x onerror=alert(1)>")
        message = mail.outbox[-1]
        html = message.alternatives[0].content
        self.assertIn("We found it &lt;img src=x onerror=alert(1)&gt;", html)
        self.assertNotIn("<img src=x", html)
        self.assertNotIn('bgcolor="#d71920"', html)
        self.assertNotIn("this address is on a Lion Parts account", html)
        self.assertIn(">Support reply</h1>", html)
        with translation.override("en"):
            self.assertTrue(message.body.endswith(f"\n\n{transactional_footer()}"))
        self.assertNotIn("<", message.body.replace("<img src=x onerror=alert(1)>", ""))


class InvoiceDesignTests(TestCase):
    def setUp(self):
        self.customer = make_customer()
        self.client = APIClient()
        self.client.force_login(self.customer.user)
        site = SiteSettings.get()
        site.invoice_seller_name_i18n = {"ka": "seller-ka", "en": "Lion Parts LLC", "ru": "ООО Лайон Партс"}
        site.invoice_seller_code = "405000000"
        site.invoice_seller_address_i18n = {"en": "Tbilisi\nAgmashenebeli 1"}
        site.invoice_seller_phone, site.invoice_seller_email = "+995 32 200 00 00", "billing@lionparts.ge"
        site.invoice_bank_details_i18n = {"en": "TBC Bank\nIBAN GE00TB0000000000000000"}
        site.invoice_payment_due_text_i18n = {"en": "Pay within 3 business days."}
        site.save()

    def get(self, order, lang="en"):
        response = self.client.get(f"/api/orders/{order.order_number}/invoice/?lang={lang}")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_print_structure_and_every_field(self):
        order = make_order(self.customer, items=(("PART-1", D("100.00"), 2), ("PART-2", D("50.00"), 1),
                                                 ("PART-3", D("10.00"), 3)), payment_option="split_70_30")
        order.items.filter(part_number="PART-3").update(item_status="cancelled")
        point = PickupPoint.objects.create(name_i18n={"en": "Vake office"})
        Order.objects.filter(pk=order.pk).update(pickup_point=point, courier_fee=D("26.00"))
        order.refresh_from_db()
        html = self.get(order)
        for part in ('<html lang="en">', "@page { size: A4; margin: 14mm; }", "@media print", '<header class="head">',
                     'aria-label="Lion Parts"', f'<div class="no">{order.order_number}</div>', "Lion Parts LLC",
                     "Identification code: 405000000", "Tbilisi<br>Agmashenebeli 1", "+995 32 200 00 00",
                     "billing@lionparts.ge", "Pickup point:</strong> Vake office", "TBC Bank<br>IBAN",
                     f"Payment reference: <strong>{order.order_number}</strong>", "Pay within 3 business days.",
                     "Delivery fee", "Due now", "(cancelled)", '<div class="due"><span>Balance due</span>',
                     "2.6000 GEL per USD", "<footer>Lion Parts</footer>"):
            self.assertIn(part, html)
        self.assertEqual(html.count('<span class="pn">'), 3)
        # print rules come last, or the narrow-screen block (an A4 page is < 820px) overrides them on paper
        self.assertGreater(html.rindex("@media print"), html.rindex("@media (max-width: 820px)"))
        self.assertEqual(html.count("<script"), 0)
        # the invoice page is white: a white logo part (the on-dark variant's "PARTS" and "i" dot) would vanish
        self.assertIsNone(re.search(r'fill="(#fff(fff)?|white)"', html, re.I))
        self.assertNotIn("//fonts.", html)  # no other origin (LPK-187 CSP)
        for field in ("items_total", "discount_total", "courier_fee", "total", "amount_paid", "balance_due",
                      "amount_due_now"):
            amount = getattr(order, field)
            self.assertIn(f"₾{amount:,.2f}", html, field)

    def test_individual_and_company_buyers(self):
        order = make_order(self.customer)
        Order.objects.filter(pk=order.pk).update(billing_snapshot={
            "first_name": "Nino", "last_name": "Beridze", "personal_id": "01001012345", "phone": "555123456",
            "email": "nino@example.com"})
        html = self.get(order)
        self.assertIn('<div class="name">Nino Beridze</div>', html)
        self.assertIn("Personal ID: 01001012345", html)
        self.assertNotIn("Representative", html)
        Order.objects.filter(pk=order.pk).update(currency="USD", exchange_rate=D("1"), billing_snapshot={
            "first_name": "Giorgi", "last_name": "M", "company_official_name": "Example LLC",
            "company_identification_code": "404123456", "legal_address": "Tbilisi", "phone": "555123456"})
        html = self.get(order)
        for part in ('<div class="name">Example LLC</div>', "Identification code: 404123456",
                     "Representative: Giorgi M", "Amounts in USD. VAT included.", "$"):
            self.assertIn(part, html)
        self.assertNotIn("₾", html)
        self.assertNotIn("Personal ID", html)

    def test_delivery_address_is_escaped(self):
        order = make_order(self.customer)
        Order.objects.filter(pk=order.pk).update(delivery_method="delivery", delivery_city="tbilisi",
                                                 delivery_district="vake", delivery_address="<b>Street</b> 1")
        html = self.get(order)
        self.assertIn('<section class="ship"><strong>Delivery:</strong> Tbilisi, Vake, &lt;b&gt;Street&lt;/b&gt; 1',
                      html)

    def test_georgian_and_russian(self):
        order = make_order(self.customer)
        for language, seller in (("ka", "seller-ka"), ("ru", "ООО Лайон Партс")):
            with self.subTest(language=language):
                html = self.get(order, language)
                self.assertIn(f'<html lang="{language}">', html)
                self.assertIn(seller, html)
                with translation.override(language):
                    self.assertIn(f"<h1>{gettext('Invoice')}</h1>", html)
                for english in ("Balance due", "Payment reference", "Seller", "Buyer"):
                    self.assertNotIn(english, html)

    def test_rendering_changes_no_order_data(self):
        order = make_order(self.customer)
        before = model_to_dict(Order.objects.get(pk=order.pk))
        self.get(order)
        self.assertEqual(model_to_dict(Order.objects.get(pk=order.pk)), before)
        self.assertFalse(Notification.objects.exists())
