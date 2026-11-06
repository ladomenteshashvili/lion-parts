"""LPK-187: what the Report-Only policy would report on the backend's own pages. Browsers are not run here: this reads the HTML
of the admin pages and the invoice for what the policy forbids: inline scripts, inline handlers and other origins."""

import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.testing import make_customer
from orders.models import NotificationChannelSetting
from orders.testing import make_order
from payments.models import Payment
from pricing.models import ExchangeRate

SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)
HANDLER = re.compile(r"<[a-z][^>]*\s(on[a-z]+)\s*=", re.I)
EXTERNAL = re.compile(r"""<(?:script|link|img|iframe|form)\b[^>]*\s(?:src|href|action)\s*=\s*["'](?:https?:)?//""", re.I)
JS_URL = re.compile(r"""\s(?:href|src|action)\s*=\s*["']\s*javascript:""", re.I)


def violations(html):
    found = []
    for attributes, body in SCRIPT.findall(html):
        kind = re.search(r"""type\s*=\s*["']([^"']+)""", attributes)
        is_data = kind is not None and "javascript" not in kind.group(1) and kind.group(1) != "module"
        if "src=" not in attributes and body.strip() and not is_data:
            found.append(f"inline script: {body.strip()[:60]}")
    found += [f"inline handler {name}" for name in HANDLER.findall(html)]
    found += ["another origin" for _ in EXTERNAL.findall(html)]
    found += ["javascript: URL" for _ in JS_URL.findall(html)]
    return found


@override_settings(SECURE_SSL_REDIRECT=False)
class BackendPageTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_superuser("csp-staff", "csp@example.com", "Csp-Staff!pass1")
        self.customer = make_customer()
        self.order = make_order(self.customer)
        ExchangeRate.objects.get_or_create(date="2026-01-01", currency="USD", defaults={"rate": "2.7000"})
        self.client.force_login(self.staff)

    def admin_pages(self):
        payment = Payment.objects.create(order=self.order, provider="invoice", amount=self.order.total or 1,
                                         currency=self.order.currency)
        channel = NotificationChannelSetting.objects.first()  # its change page has the translated-text widgets
        return ["admin:index", "admin:orders_order_changelist", "admin:payments_payment_changelist",
                "admin:pricing_exchangerate_changelist", "admin:parts_partquoterequest_changelist",
                ("admin:orders_order_change", self.order.pk), ("admin:payments_payment_change", payment.pk),
                ("admin:orders_notificationchannelsetting_change", channel.pk), "admin:pricing_sitesettings_changelist"]

    def test_the_policy_is_report_only_and_never_enforcing(self):
        response = self.client.get(reverse("admin:index"))
        self.assertIn("Content-Security-Policy-Report-Only", response.headers)
        self.assertNotIn("Content-Security-Policy", response.headers)
        self.assertFalse(getattr(settings, "SECURE_CSP", None))

    def test_admin_pages_have_nothing_the_policy_would_report(self):
        for page in self.admin_pages():
            name, *args = (page,) if isinstance(page, str) else page
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=args), follow=True)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(violations(response.content.decode()), [])

    def test_the_admin_login_page_has_nothing_the_policy_would_report(self):
        self.client.logout()
        response = self.client.get(reverse("admin:login"))
        self.assertEqual(violations(response.content.decode()), [])

    def test_the_invoice_has_nothing_the_policy_would_report(self):
        client = self.client_class()
        client.force_login(self.customer.user)
        response = client.get(f"/api/orders/{self.order.order_number}/invoice/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(violations(response.content.decode()), [])
