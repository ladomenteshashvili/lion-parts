"""LP-BE-604: invoice HTML (G§7.10, G§4.11, D16)."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import make_customer
from orders.models import Order
from orders.testing import make_order
from pricing.models import ExchangeRate, SiteSettings

D = Decimal


class InvoiceTests(TestCase):
    def setUp(self):
        self.customer = make_customer()
        self.client = APIClient()
        self.client.force_login(self.customer.user)
        site = SiteSettings.get()
        site.invoice_seller_name_i18n = {"ka": "seller-ka", "en": "Lion Parts LLC", "ru": "OOO Lion Parts"}
        site.invoice_bank_details_i18n = {"ka": "bank-ka", "en": "IBAN GE00TB0000000000000000", "ru": "IBAN RU-text"}
        site.save()

    def get(self, order, lang=None, **headers):
        url = f"/api/orders/{order.order_number}/invoice/" + (f"?lang={lang}" if lang else "")
        return self.client.get(url, **headers)

    def test_gel_invoice_with_frozen_amounts(self):
        order = make_order(self.customer, items=(("PART-1", D("164.26"), 2),))
        response = self.get(order, "en")
        self.assertEqual((response.status_code, response["Content-Type"], response["X-Robots-Tag"]),
                         (200, "text/html; charset=utf-8", "noindex"))
        html = response.content.decode()
        self.assertIn('<html lang="en">', html)
        for text in ("Invoice", order.order_number, "Lion Parts LLC", "IBAN GE00TB0000000000000000", "₾427.08",
                     "₾854.16", "Balance due", "2.6000 GEL per USD"):
            self.assertIn(text, html)
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("3.1000"))  # later rate change
        self.assertIn("₾854.16", self.get(order, "en").content.decode())  # the invoice never changes

    def test_usd_invoice_and_legal_buyer(self):
        order = make_order(self.customer, currency="USD", items=(("PART-1", D("199.50"), 1),))
        Order.objects.filter(pk=order.pk).update(billing_snapshot={
            "account_type": "legal", "first_name": "Giorgi", "last_name": "M", "company_official_name": "Example LLC",
            "company_identification_code": "404123456", "legal_address": "Tbilisi", "phone": "555123456",
            "email": "info@example.ge"})
        html = self.get(order, "en").content.decode()
        for text in ("$199.50", "Example LLC", "404123456", "Amounts in USD"):
            self.assertIn(text, html)
        self.assertNotIn("₾", html)

    def test_languages(self):
        order = make_order(self.customer)
        en, ru = self.get(order, "en").content.decode(), self.get(order, "ru").content.decode()
        self.assertIn("OOO Lion Parts", ru)
        self.assertIn("IBAN RU-text", ru)
        self.assertNotIn("Balance due", ru)
        self.assertIn('<html lang="ru">', ru)
        fallback = self.get(order, "de", HTTP_ACCEPT_LANGUAGE="en")  # unknown -> the request language
        self.assertIn('<html lang="en">', fallback.content.decode())
        self.assertEqual(self.get(order, HTTP_ACCEPT_LANGUAGE="ka")["Content-Language"], "ka")

    def test_customer_text_is_escaped(self):
        order = make_order(self.customer)
        Order.objects.filter(pk=order.pk).update(delivery_method="delivery", delivery_city="tbilisi",
                                                 delivery_district="vake", delivery_address="Street 1",
                                                 delivery_comment="<script>alert(1)</script>")
        html = self.get(order, "en").content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("Tbilisi, Vake", html)

    def test_only_the_owner(self):
        order = make_order(self.customer)
        other = APIClient()
        other.force_login(make_customer(email="o@example.com", phone="599000000").user)
        self.assertEqual(other.get(f"/api/orders/{order.order_number}/invoice/").status_code, 404)
        self.assertEqual(APIClient().get(f"/api/orders/{order.order_number}/invoice/").status_code, 401)
        self.assertEqual(self.client.get("/api/orders/LP-2026-99999/invoice/").status_code, 404)

    def test_browser_accept_header(self):
        order = make_order(self.customer)
        response = self.get(order, "en", HTTP_ACCEPT="text/html,application/xhtml+xml,*/*;q=0.8")
        self.assertEqual(response.status_code, 200)
