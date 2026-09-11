from decimal import Decimal

from django.test import TestCase

from accounts.models import Customer, CustomerSession
from orders.models import Order, OrderItem, Payment


class CustomerInvoiceViewTests(TestCase):
    def test_verified_customer_can_open_own_invoice(self):
        customer = Customer.objects.create(
            session_id="invoice-session",
            name="Invoice Customer",
            phone="555123456",
            is_phone_verified=True,
        )
        CustomerSession.objects.create(
            customer=customer,
            session_id="invoice-session",
        )

        order = Order.objects.create(
            order_number="LP-CUSTOMER-INVOICE-0001",
            session_id="invoice-session",
            customer=customer,
            customer_name="Invoice Customer",
            customer_phone="555123456",
            vin="CUSTOMERVIN123",
            note="Customer invoice note",
            courier_delivery_requested=True,
            courier_delivery_fee_gel=Decimal("12.00"),
            payment_type=Order.PAYMENT_FULL,
            status=Order.STATUS_PROCESSING,
            total_gel=Decimal("162.00"),
        )

        Payment.objects.create(
            order=order,
            payment_reference="PAY-CUSTOMER-INVOICE-0001",
            status=Payment.STATUS_PENDING,
            amount_gel=Decimal("162.00"),
            currency="GEL",
        )

        OrderItem.objects.create(
            order=order,
            cart_item_id="cart-customer-invoice-1",
            quote_id="quote-customer-invoice-1",
            part_option_id="option-customer-invoice-1",
            part_number="CUSTOMERPART123",
            name="Customer invoice part",
            condition="New",
            brand="OEM",
            availability="Available",
            eta_days=10,
            weight_kg=Decimal("2.50"),
            final_price_gel=Decimal("150.00"),
            currency="GEL",
            quantity=1,
        )

        with self.settings(INVOICE_NUMBER_PREFIX="CINV"):
            response = self.client.get(
                f"/api/orders/{order.order_number}/invoice/",
                {"session_id": "invoice-session"},
            )

        content = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("text/html", response["Content-Type"])
        self.assertIn(f"CINV-{order.created_at.year}-{order.id:06d}", content)
        self.assertIn("LP-CUSTOMER-INVOICE-0001", content)
        self.assertIn("Invoice Customer", content)
        self.assertIn("CUSTOMERVIN123", content)
        self.assertIn("CUSTOMERPART123", content)
        self.assertIn("Customer invoice part", content)
        self.assertIn("162.00", content)

    def test_wrong_session_cannot_open_invoice(self):
        customer = Customer.objects.create(
            session_id="invoice-session",
            name="Invoice Customer",
            phone="555123456",
            is_phone_verified=True,
        )
        CustomerSession.objects.create(
            customer=customer,
            session_id="invoice-session",
        )

        order = Order.objects.create(
            order_number="LP-CUSTOMER-INVOICE-0002",
            session_id="invoice-session",
            customer=customer,
            customer_name="Invoice Customer",
            customer_phone="555123456",
            vin="",
            note="",
            payment_type=Order.PAYMENT_FULL,
            status=Order.STATUS_PROCESSING,
            total_gel=Decimal("0.00"),
        )

        response = self.client.get(
            f"/api/orders/{order.order_number}/invoice/",
            {"session_id": "wrong-session"},
        )

        self.assertEqual(response.status_code, 404)
