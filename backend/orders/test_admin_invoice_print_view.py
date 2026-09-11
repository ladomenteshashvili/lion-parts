from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from orders.admin import OrderAdmin
from orders.models import Order, OrderItem, Payment


class AdminInvoicePrintViewTests(TestCase):
    def test_order_admin_invoice_print_view_contains_invoice_data(self):
        order = Order.objects.create(
            order_number="LP-PRINT-0001",
            session_id="print-session",
            customer_name="Print Customer",
            customer_phone="555123456",
            vin="PRINTVIN123",
            note="Test note",
            courier_delivery_requested=True,
            courier_delivery_fee_gel=Decimal("12.00"),
            payment_type=Order.PAYMENT_FULL,
            status=Order.STATUS_PROCESSING,
            total_gel=Decimal("162.00"),
        )

        Payment.objects.create(
            order=order,
            payment_reference="PAY-PRINT-0001",
            status=Payment.STATUS_PENDING,
            amount_gel=Decimal("162.00"),
            currency="GEL",
        )

        OrderItem.objects.create(
            order=order,
            cart_item_id="cart-print-1",
            quote_id="quote-print-1",
            part_option_id="option-print-1",
            part_number="PRINTPART123",
            name="Printed invoice part",
            condition="New",
            brand="OEM",
            availability="Available",
            eta_days=10,
            weight_kg=Decimal("2.50"),
            final_price_gel=Decimal("150.00"),
            currency="GEL",
            quantity=1,
        )

        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )

        request = RequestFactory().get("/")
        request.user = user

        admin_model = OrderAdmin(Order, AdminSite())
        response = admin_model.admin_invoice_print_view(request, order.id)

        content = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("INVOICE", content)
        self.assertIn("LP-PRINT-0001", content)
        self.assertIn("Print Customer", content)
        self.assertIn("PRINTVIN123", content)
        self.assertIn("PRINTPART123", content)
        self.assertIn("Printed invoice part", content)
        self.assertIn("კურიერით მიწოდება", content)
        self.assertIn("162.00", content)
        self.assertIn("Print / Save as PDF", content)

    def test_order_admin_invoice_print_link_is_available(self):
        order = Order.objects.create(
            order_number="LP-PRINT-LINK-0001",
            session_id="print-link-session",
            customer_name="Print Link Customer",
            customer_phone="555123456",
            vin="",
            note="",
            payment_type=Order.PAYMENT_FULL,
            status=Order.STATUS_PAYMENT_PENDING,
            total_gel=Decimal("0.00"),
        )

        admin_model = OrderAdmin(Order, AdminSite())
        link = str(admin_model.invoice_print_link(order))

        self.assertIn("Print invoice", link)
        self.assertIn(f"./{order.id}/invoice/", link)
