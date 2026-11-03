"""LP-BE-602: checkout options (G§8.6)."""

from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomerTariff, LegalEntityProfile
from accounts.testing import make_customer
from orders.checkout import payment_options
from pricing.models import ExchangeRate

D = Decimal
KEYS = {"payer", "currency", "payment_terms", "payment_options", "payment_methods", "pickup_points", "delivery",
        "summary", "checkout_blocked", "checkout_blocked_reasons"}


class PaymentOptionsTests(TestCase):
    def test_split_amounts(self):
        split = CustomerTariff(payment_terms="split_70_30", split_upfront_percent=D("70"))
        self.assertEqual(payment_options(D("760.00"), split), [
            {"value": "split_70_30", "due_now": "532.00", "balance": "228.00", "default": True},
            {"value": "full", "due_now": "760.00", "balance": "0.00", "default": False}])
        self.assertEqual(payment_options(D("100.01"), split)[0]["due_now"], "70.01")  # 70.007 -> 70.01

    def test_full_tariff_only_full(self):
        self.assertEqual(payment_options(D("760.00"), CustomerTariff(payment_terms="full")),
                         [{"value": "full", "due_now": "760.00", "balance": "0.00", "default": True}])


@override_settings(PARTS_PROVIDER="demo")
class CheckoutOptionsEndpointTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def add_to_cart(self):
        data = self.client.post("/api/parts/search/", {"part_number": "P-1"}, format="json").json()
        self.client.post("/api/cart/items/", {"source": "search", "search_id": data["search_id"],
                                              "offer_id": data["offers"][0]["offer_id"]}, format="json")

    def test_contract_and_cart_summary(self):
        self.add_to_cart()
        data = self.client.get("/api/checkout/options/", HTTP_ACCEPT_LANGUAGE="en").json()
        self.assertEqual(set(data), KEYS)
        cart = self.client.get("/api/cart/").json()
        self.assertEqual((data["summary"], data["currency"], data["checkout_blocked"]), (cart["summary"], "GEL", False))
        self.assertEqual(data["payer"], {"name": "Nino Beridze", "account_type": "physical", "company_name": None,
                                         "phone": "555123456", "email": "nino@example.com"})
        self.assertEqual((data["payment_methods"], data["pickup_points"][0]["name"]),
                         (["card", "invoice"], "Beliashvili pickup point"))
        self.assertIn({"code": "vake", "city": "tbilisi", "name": "Vake"}, data["delivery"]["districts"])

    def test_split_tariff_and_legal_payer(self):
        partner = CustomerTariff.objects.create(name="Partner", payment_terms="split_70_30")
        self.customer.tariff = partner
        self.customer.account_type = "legal"
        self.customer.save()
        LegalEntityProfile.objects.create(customer=self.customer, company_official_name="Example LLC",
                                          company_identification_code="404123456")
        self.add_to_cart()
        data = self.client.get("/api/checkout/options/").json()
        self.assertEqual([o["value"] for o in data["payment_options"]], ["split_70_30", "full"])
        self.assertEqual(data["payer"]["company_name"], "Example LLC")

    def test_requires_a_fully_verified_customer(self):
        self.assertEqual(APIClient().get("/api/checkout/options/").status_code, 401)
        unverified = APIClient()
        unverified.force_login(make_customer(email="u@example.com", phone="599000000", is_phone_verified=True).user)
        response = unverified.get("/api/checkout/options/")
        self.assertEqual((response.status_code, response.json()["code"]), (403, "verification_required"))

    def test_empty_cart(self):
        data = self.client.get("/api/checkout/options/").json()
        self.assertEqual((data["summary"]["items_count"], data["payment_options"][0]["due_now"]), (0, "0.00"))
