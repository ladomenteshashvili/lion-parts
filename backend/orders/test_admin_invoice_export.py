from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from orders.admin import OrderAdmin
from orders.models import Order, OrderItem, Payment


class AdminInvoiceExportTests(TestCase):
    def test_order_admin_exports_invoice_csv(self):
        order = Order.objects.create(
            order_number="LP-INVOICE-0001",
            session_id="invoice-session",
            customer_name="Invoice Customer",
            customer_phone="555123456",
            vin="TESTVIN123",
            note="",
            courier_delivery_requested=True,
            courier_delivery_fee_gel=Decimal("12.00"),
            payment_type=Order.PAYMENT_FULL,
            status=Order.STATUS_PROCESSING,
            total_gel=Decimal("162.00"),
        )

        Payment.objects.create(
            order=order,
            payment_reference="PAY-INVOICE-0001",
            status=Payment.STATUS_PENDING,
            amount_gel=Decimal("162.00"),
            currency="GEL",
        )

        OrderItem.objects.create(
            order=order,
            cart_item_id="cart-invoice-1",
            quote_id="quote-invoice-1",
            part_option_id="option-invoice-1",
            part_number="PART123",
            name="Front bumper",
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

        request = RequestFactory().post("/")
        request.user = user

        admin_model = OrderAdmin(Order, AdminSite())

        with self.settings(INVOICE_NUMBER_PREFIX="INV"):
            response = admin_model.export_selected_orders_invoice_csv(
                request,
                Order.objects.filter(id=order.id),
            )

        content = response.content.decode("utf-8-sig")

        self.assertEqual(response.status_code, 200)
        self.assertIn("orders_invoice_export.csv", response["Content-Disposition"])
        self.assertIn(f"INV-{order.created_at.year}-{order.id:06d}", content)
        self.assertIn("Invoice number", content)
        self.assertIn("LP-INVOICE-0001", content)
        self.assertIn("Invoice Customer", content)
        self.assertIn("TESTVIN123", content)
        self.assertIn("PART123", content)
        self.assertIn("Front bumper", content)
        self.assertIn("Courier delivery", content)
        self.assertIn("162.00", content)
