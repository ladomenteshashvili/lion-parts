"""LP-BE-701: payment model, provider contract, FakeProvider (G§6.6, G§7.10)."""

from decimal import Decimal

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from accounts.testing import make_customer
from config.tests import run_python
from orders.services import recalculate_order
from orders.testing import make_order
from payments.models import Payment
from payments.providers import FakeProvider, get_provider

D = Decimal


class ProviderTests(TestCase):
    def test_fake_provider_by_setting(self):
        self.assertIsInstance(get_provider(), FakeProvider)
        with override_settings(PAYMENT_PROVIDER="nope"), self.assertRaises(ImproperlyConfigured):
            get_provider()

    def test_unknown_provider_stops_the_startup(self):
        result = run_python("import django; django.setup()", {"PAYMENT_PROVIDER": "nope", "DEBUG": "True"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown PAYMENT_PROVIDER", result.stderr)

    @override_settings(FRONTEND_BASE_URL="https://lionparts.ge/")
    def test_fake_start_and_status(self):
        order = make_order(make_customer())
        payment = Payment.objects.create(order=order, kind="initial", provider="fake", amount=order.total,
                                         currency=order.currency)
        self.assertEqual(FakeProvider().start(payment),
                         f"https://lionparts.ge/payment-result?order={order.order_number}&payment={payment.pk}&fake=1")
        self.assertEqual(FakeProvider().fetch_status(payment), "created")


class AmountPaidTests(TestCase):
    def test_only_paid_payments_count(self):
        order = make_order(make_customer(), items=(("P", D("100"), 1),))  # 260.00 GEL
        for status, amount in (("paid", "100.00"), ("pending", "50.00"), ("refunded", "30.00"), ("failed", "20.00")):
            Payment.objects.create(order=order, kind="initial", provider="fake", amount=D(amount), currency="GEL",
                                   status=status)
        recalculate_order(order)
        self.assertEqual((order.amount_paid, order.balance_due), (D("100.00"), D("160.00")))
