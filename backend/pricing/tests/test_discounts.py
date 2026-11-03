"""LP-BE-304: discount engine (G§7.3)."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomerTariff
from accounts.testing import make_customer
from pricing.discounts import best_discount
from pricing.models import Discount

D = Decimal


class BestDiscountTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.customer = make_customer()

    def best(self, customer="default", part="17940-25020", brand="Toyota", price=D("100.00"), currency="GEL",
             rate=D("2.5")):
        customer = self.customer if customer == "default" else customer
        return best_discount(customer, part, brand, price, currency, rate, self.now)

    def rule(self, value, kind="percent", **fields):
        return Discount.objects.create(name=f"r{value}", kind=kind, value=value, **fields)

    def test_no_rules(self):
        self.assertEqual(self.best(), (None, D("0.00")))

    def test_biggest_wins_without_stacking(self):
        self.rule(5, customer=self.customer)
        part_rule = self.rule(10, part_number="17940 25020")
        self.assertEqual(self.best(), (part_rule, D("10.00")))

    def test_fixed_gel_on_a_usd_line(self):
        rule = self.rule(50, kind="fixed")
        self.assertEqual(self.best(currency="USD", price=D("120.00"), rate=D("2.5")), (rule, D("20.00")))
        self.assertEqual(self.best(currency="GEL"), (rule, D("50.00")))

    def test_equal_amounts_lower_id_wins(self):
        first = self.rule(10)
        self.rule(10, brand="toyota")
        self.assertEqual(self.best()[0], first)

    def test_capped_at_the_list_price(self):
        self.rule(500, kind="fixed")
        self.assertEqual(self.best(price=D("80.00"))[1], D("80.00"))

    def test_rounding_half_up(self):
        self.rule(D("12.5"))
        self.assertEqual(self.best(price=D("0.20"))[1], D("0.03"))  # 0.025 -> 0.03

    def test_guests_never_get_customer_or_tariff_rules(self):
        self.rule(30, customer=self.customer)
        self.rule(20, tariff=CustomerTariff.objects.get(is_default=True))
        self.assertEqual(self.best(customer=None), (None, D("0.00")))
        self.assertEqual(self.best()[1], D("30.00"))

    def test_rules_for_another_customer_or_tariff_do_not_match(self):
        other = make_customer(email="o@example.com", phone="599000000")
        self.rule(30, customer=other)
        self.rule(20, tariff=CustomerTariff.objects.create(name="Partner"))
        self.assertEqual(self.best(), (None, D("0.00")))

    def test_customer_tariff_rule_matches(self):
        partner = CustomerTariff.objects.create(name="Partner")
        self.customer.tariff = partner
        self.customer.save()
        rule = self.rule(15, tariff=partner)
        self.assertEqual(self.best(), (rule, D("15.00")))

    def test_part_and_brand_targets(self):
        self.rule(40, part_number="OTHER-1")
        self.rule(35, brand="Denso")
        brand_rule = self.rule(25, brand="TOYOTA")
        self.assertEqual(self.best()[0], brand_rule)

    def test_inactive_and_out_of_window_rules_are_ignored(self):
        self.rule(50, is_active=False)
        self.rule(40, valid_to=self.now - timedelta(seconds=1))
        self.rule(30, valid_from=self.now + timedelta(seconds=1))
        current = self.rule(5, valid_from=self.now - timedelta(days=1), valid_to=self.now + timedelta(days=1))
        self.assertEqual(self.best(), (current, D("5.00")))
