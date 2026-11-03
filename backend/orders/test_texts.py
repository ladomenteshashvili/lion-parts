"""LP-BE-1304: system texts are stored as code + params and rendered in the request language (G§5.8)."""

import string
from decimal import Decimal

from django.test import TestCase
from django.utils import translation
from rest_framework.test import APIRequestFactory

from accounts.testing import make_customer
from orders.models import Order, OrderItemEvent
from orders.serializers import order_data
from orders.services import create_item_event
from orders.testing import make_order
from orders.texts import EVENT_TEXTS, render_text
from payments.services import create_payment, mark_payment

PARAMS = {"amount": "348.00", "currency": "GEL", "eta_min_days": 5, "eta_max_days": 9, "part_number": "X-1",
          "weight_kg": "2.50", "percent": 70}


def placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class CatalogTests(TestCase):
    def test_every_code_renders_in_every_language_with_the_same_placeholders(self):
        for code, text in EVENT_TEXTS.items():
            with translation.override("en"):
                english = placeholders(str(text))
            for language in ("ka", "en", "ru"):
                with self.subTest(code=code, language=language), translation.override(language):
                    self.assertEqual(placeholders(str(text)), english)
                    rendered = render_text(code, **PARAMS)
                    self.assertNotIn("{", rendered)
                    self.assertNotEqual(rendered, code)

    def test_money_is_formatted_from_amount_and_currency(self):
        with translation.override("en"):
            self.assertEqual(render_text("courier_fee_accepted", amount=Decimal("15"), currency="USD"),
                             "Customer accepted the courier fee: $15.00.")

    def test_unknown_code_or_missing_param_never_breaks_a_response(self):
        with self.assertLogs("orders.texts", "WARNING"):
            self.assertEqual(render_text("no_such_code"), "no_such_code")
        with self.assertLogs("orders.texts", "WARNING"):
            self.assertEqual(render_text("action_requested_price_change"), "action_requested_price_change")


class OrderLanguageTests(TestCase):
    def setUp(self):
        self.order = make_order(make_customer())
        for item in self.order.items.all():
            create_item_event(item, "created", text_code="order_item_added", visible=True)
        mark_payment(create_payment(self.order, "invoice"), "paid")
        self.order = Order.objects.get(pk=self.order.pk)

    def render(self, language):
        request = APIRequestFactory().get("/")
        with translation.override(language):
            data = order_data(request, self.order)
        return [e["title"] for e in data["items"][0]["events"]]

    def test_the_same_order_renders_in_each_language(self):
        self.assertEqual(self.render("en"), ["The part was added to the order.", "Payment confirmed."])
        self.assertEqual(self.render("ru")[1], "Оплата подтверждена.")
        titles = {language: tuple(self.render(language)) for language in ("ka", "en", "ru")}
        self.assertEqual(len(set(titles.values())), 3)

    def test_no_rendered_system_text_is_stored(self):
        events = OrderItemEvent.objects.filter(actor_type=OrderItemEvent.ACTOR_SYSTEM)
        self.assertTrue(events.exists())
        self.assertEqual(set(events.values_list("message", flat=True)), {""})
        self.assertTrue(all(e.text_code in EVENT_TEXTS for e in events))
        paid = events.get(text_code="payment_confirmed")
        self.assertEqual(paid.text_params, {"amount": f"{self.order.total:.2f}", "currency": "GEL"})
