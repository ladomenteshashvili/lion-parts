from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Customer, CustomerSession, LegalEntityProfile
from cart.models import Cart, CartItem
from orders.models import Order, Payment


def create_customer(session_id, verified=True, phone="555123456"):
    customer = Customer.objects.create(
        session_id=session_id,
        name="Checkout Customer",
        phone=phone,
        is_phone_verified=verified,
    )
    CustomerSession.objects.create(customer=customer, session_id=session_id)
    return customer


def create_cart_with_item(session_id):
    cart = Cart.objects.create(session_id=session_id)

    CartItem.objects.create(
        cart=cart,
        cart_item_id="legal-checkout-cart-item",
        quote_id="legal-checkout-quote",
        part_option_id="legal-checkout-option",
        part_number="LEGAL123",
        name="Legal Checkout Test Part",
        condition="New",
        brand="OEM",
        availability="Available",
        eta_days=10,
        weight_kg=Decimal("2.50"),
        final_price_gel=Decimal("150.00"),
        currency="GEL",
        quantity=2,
    )

    return cart


def create_legal_entity(customer, verified=True, active=True):
    return LegalEntityProfile.objects.create(
        customer=customer,
        company_identification_code="405834094",
        company_official_name="EZShop LLC",
        legal_address="Tbilisi, Georgia",
        contact_first_name="Lado",
        contact_last_name="Menteshashvili",
        email="legal@example.com",
        mobile_phone="555123456",
        is_mobile_verified=verified,
        is_active=active,
    )


class CheckoutLegalEntityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "checkout-legal-session"

    def test_checkout_can_use_verified_legal_entity_profile(self):
        customer = create_customer(self.session_id)
        legal_entity = create_legal_entity(customer, verified=True)
        create_cart_with_item(self.session_id)

        response = self.client.post(
            "/api/orders/checkout/",
            {
                "session_id": self.session_id,
                "customer_name": "Submitted Name",
                "customer_phone": "+995555123456",
                "use_legal_entity_billing": True,
                "vin": "LEGALVIN123456789",
                "note": "Legal checkout note",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        order = Order.objects.select_related("legal_entity_profile", "payment").get()

        self.assertEqual(order.billing_type, Order.BILLING_LEGAL_ENTITY)
        self.assertEqual(order.legal_entity_profile_id, legal_entity.id)
        self.assertEqual(
            order.legal_entity_company_identification_code,
            "405834094",
        )
        self.assertEqual(order.legal_entity_company_official_name, "EZShop LLC")
        self.assertEqual(order.legal_entity_legal_address, "Tbilisi, Georgia")
        self.assertEqual(order.legal_entity_contact_first_name, "Lado")
        self.assertEqual(order.legal_entity_contact_last_name, "Menteshashvili")
        self.assertEqual(order.legal_entity_email, "legal@example.com")
        self.assertEqual(order.legal_entity_mobile_phone, "555123456")
        self.assertEqual(order.total_gel, Decimal("300.00"))

        self.assertEqual(order.payment.status, Payment.STATUS_PENDING)
        self.assertEqual(order.payment.amount_gel, Decimal("300.00"))

        self.assertEqual(response.data["billing_type"], Order.BILLING_LEGAL_ENTITY)
        self.assertEqual(response.data["legal_entity_profile_id"], legal_entity.id)
        self.assertEqual(
            response.data["legal_entity_company_identification_code"],
            "405834094",
        )
        self.assertEqual(
            response.data["legal_entity_company_official_name"],
            "EZShop LLC",
        )

    def test_checkout_without_legal_entity_stays_personal(self):
        create_customer(self.session_id)
        create_cart_with_item(self.session_id)

        response = self.client.post(
            "/api/orders/checkout/",
            {
                "session_id": self.session_id,
                "customer_name": "Checkout Customer",
                "customer_phone": "+995555123456",
                "use_legal_entity_billing": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        order = Order.objects.get()

        self.assertEqual(order.billing_type, Order.BILLING_PERSONAL)
        self.assertIsNone(order.legal_entity_profile)
        self.assertEqual(order.legal_entity_company_identification_code, "")
        self.assertEqual(order.legal_entity_company_official_name, "")

        self.assertEqual(response.data["billing_type"], Order.BILLING_PERSONAL)
        self.assertIsNone(response.data["legal_entity_profile_id"])

    def test_checkout_rejects_legal_billing_without_profile(self):
        create_customer(self.session_id)
        cart = create_cart_with_item(self.session_id)

        response = self.client.post(
            "/api/orders/checkout/",
            {
                "session_id": self.session_id,
                "customer_name": "Checkout Customer",
                "customer_phone": "+995555123456",
                "use_legal_entity_billing": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "legal entity profile is required for legal checkout",
        )
        self.assertEqual(Order.objects.count(), 0)

        cart.refresh_from_db()
        self.assertEqual(cart.items.count(), 1)

    def test_checkout_rejects_unverified_legal_entity_mobile(self):
        customer = create_customer(self.session_id)
        create_legal_entity(customer, verified=False)
        cart = create_cart_with_item(self.session_id)

        response = self.client.post(
            "/api/orders/checkout/",
            {
                "session_id": self.session_id,
                "customer_name": "Checkout Customer",
                "customer_phone": "+995555123456",
                "use_legal_entity_billing": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["detail"],
            "legal entity mobile must be verified",
        )
        self.assertEqual(Order.objects.count(), 0)

        cart.refresh_from_db()
        self.assertEqual(cart.items.count(), 1)

    def test_checkout_rejects_inactive_legal_entity_profile(self):
        customer = create_customer(self.session_id)
        create_legal_entity(customer, verified=True, active=False)
        cart = create_cart_with_item(self.session_id)

        response = self.client.post(
            "/api/orders/checkout/",
            {
                "session_id": self.session_id,
                "customer_name": "Checkout Customer",
                "customer_phone": "+995555123456",
                "use_legal_entity_billing": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "legal entity profile is inactive")
        self.assertEqual(Order.objects.count(), 0)

        cart.refresh_from_db()
        self.assertEqual(cart.items.count(), 1)
