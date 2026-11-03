"""LP-BE-1203: who may call what. Every customer endpoint refuses anonymous users (401) and staff without a customer
(403); every owned resource is 404 for another customer and stays unchanged; public endpoints answer anyone;
demo endpoints need staff with the matching permission (G§8 auth column, G§5.4)."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import make_customer
from cart.models import Cart, CartItem
from orders.models import Notification, Order, OrderItem, SupportTicket
from orders.testing import make_order
from payments.services import create_payment
from pricing.models import ExchangeRate

D = Decimal


@override_settings(PARTS_PROVIDER="demo", FRONTEND_BASE_URL="https://lionparts.ge")
class AccessMatrixTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.owner = make_customer(is_phone_verified=True, is_email_verified=True)
        self.order = make_order(self.owner, status=Order.STATUS_PROCESSING)
        self.item = self.order.items.get()
        OrderItem.objects.filter(pk=self.item.pk).update(action_required=True, action_type="other",
                                                         item_status="action_required")
        self.payment = create_payment(self.order, "invoice")
        self.notification = Notification.objects.create(customer=self.owner, event_type="status_update",
                                                        order=self.order)
        SupportTicket.objects.create(customer=self.owner, context="general", message="mine")
        cart = Cart.objects.create(customer=self.owner, currency="GEL")
        line = self.order.items.values().first()
        self.line = CartItem.objects.create(cart=cart, source="search", offer_id="O-1", part_number="PART-1", name="P",
                                            quantity=1, supplier_price_usd=D("100"), shipping_usd_per_kg=D("8"),
                                            markup_percent=D("20"), vat_percent=D("18"), list_price_usd=D("100"),
                                            discount_usd=D("0"), unit_price_usd=D("100"), exchange_rate=D("2.6"),
                                            list_unit_price=line["list_unit_price"], discount_unit_amount=D("0"),
                                            unit_price=line["unit_price"],
                                            price_valid_until=timezone.now() + timezone.timedelta(days=5))
        self.other = APIClient()
        self.other.force_login(make_customer(email="o@example.com", phone="599000000", is_phone_verified=True,
                                             is_email_verified=True).user)
        self.staff = APIClient()
        self.staff.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))

    def owned(self):
        n, i, p = self.order.order_number, self.item.pk, self.payment.pk
        return [
            ("get", f"/api/orders/{n}/", None), ("get", f"/api/orders/{n}/invoice/", None),
            ("post", f"/api/orders/{n}/cancel/", {"reason": "x"}),
            ("post", f"/api/orders/{n}/courier-fee/accept/", {}), ("post", f"/api/orders/{n}/courier-fee/decline/", {}),
            ("post", f"/api/orders/{n}/payments/", {"method": "invoice"}), ("get", f"/api/orders/{n}/payments/{p}/", None),
            ("post", f"/api/orders/{n}/support/messages/", {"message": "x"}),
            ("post", f"/api/orders/{n}/support/acknowledge/", {}),
            ("post", f"/api/orders/items/{i}/decision/", {"decision": "accept"}),
            ("post", f"/api/notifications/{self.notification.pk}/read/", {}),
        ]

    def cart_lines(self):
        """Carts are public (guests have one per session): someone else's line is 404 for anyone else."""
        return [("patch", f"/api/cart/items/{self.line.pk}/", {"quantity": 5}),
                ("delete", f"/api/cart/items/{self.line.pk}/", None),
                ("post", f"/api/cart/items/{self.line.pk}/refresh/", {})]

    def customer_only(self):
        return self.owned() + [
            ("get", "/api/profile/", None), ("patch", "/api/profile/", {"first_name": "X"}),
            ("delete", "/api/profile/", {"current_password": "x"}),
            ("post", "/api/profile/password/", {}), ("post", "/api/profile/email/", {}), ("post", "/api/profile/phone/", {}),
            ("post", "/api/profile/phone/verify/", {}), ("post", "/api/auth/email/send-verification/", {}),
            ("get", "/api/orders/", None), ("get", "/api/checkout/options/", None),
            ("post", "/api/orders/checkout/", {}), ("get", "/api/notifications/", None),
            ("get", "/api/support/tickets/", None), ("post", "/api/parts/calculate-price/", {}),
            ("get", "/api/parts/history/", None), ("delete", "/api/parts/history/", None),
            ("post", "/api/parts/history/seen/", {}),
            ("post", "/api/parts/quote-requests/", {}), ("post", "/api/auth/logout/", {}),
        ]

    def call(self, client, method, url, body):
        return getattr(client, method)(url, body, format="json") if body is not None else getattr(client, method)(url)

    def snapshot(self):
        order = Order.objects.get(pk=self.order.pk)
        return (order.status, order.balance_due, order.payments.count(), order.support_messages.count(),
                OrderItem.objects.get(pk=self.item.pk).action_required,
                Notification.objects.get(pk=self.notification.pk).read_at, CartItem.objects.filter(pk=self.line.pk)
                .values_list("quantity", flat=True).first())

    def test_anonymous_gets_401_everywhere_a_customer_is_needed(self):
        for method, url, body in self.customer_only():
            with self.subTest(method=method, url=url):
                self.assertEqual(self.call(APIClient(), method, url, body).status_code, 401)

    def test_staff_without_a_customer_gets_403(self):
        for method, url, body in self.customer_only():
            with self.subTest(method=method, url=url):
                self.assertEqual(self.call(self.staff, method, url, body).status_code, 403)

    def test_another_customer_gets_404_and_changes_nothing(self):
        before = self.snapshot()
        for method, url, body in self.owned() + self.cart_lines():
            with self.subTest(method=method, url=url):
                self.assertEqual(self.call(self.other, method, url, body).status_code, 404)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.other.get("/api/orders/").json()["count"], 0)
        self.assertEqual(self.other.get("/api/notifications/").json()["count"], 0)
        self.assertEqual(self.other.get("/api/support/tickets/").json()["count"], 0)
        self.assertEqual(self.other.get("/api/cart/").json()["items"], [])

    def test_cart_lines_of_others_are_404_for_guests_and_staff_too(self):
        before = self.snapshot()
        for client in (APIClient(), self.staff):
            for method, url, body in self.cart_lines():
                with self.subTest(method=method, url=url):
                    self.assertEqual(self.call(client, method, url, body).status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_public_endpoints_answer_anonymous_visitors(self):
        for method, url, body in [("get", "/api/meta/", None), ("get", "/api/health/", None),
                                  ("get", "/api/auth/csrf/", None), ("get", "/api/auth/me/", None),
                                  ("get", "/api/cart/", None),
                                  ("post", "/api/parts/search/", {"part_number": "G9010-60020"}),
                                  ("post", "/api/support/tickets/", {"context": "general", "message": "hi",
                                                                     "contact": "a@b.ge"}),
                                  ("get", f"/api/public/n/{self.notification.token}/", None)]:
            with self.subTest(method=method, url=url):
                self.assertIn(self.call(APIClient(), method, url, body).status_code, (200, 201))

    @override_settings(ENABLE_DEMO_ENDPOINTS=True)
    def test_demo_operator_endpoints_need_staff_with_permission(self):
        import importlib

        from django.urls import clear_url_caches

        import config.urls

        clear_url_caches()
        importlib.reload(config.urls)
        try:
            customer = APIClient()
            customer.force_login(self.owner.user)
            for method, url, body in [
                    ("post", f"/api/demo/orders/{self.order.order_number}/items/{self.item.pk}/status/",
                     {"item_status": "purchased"}),
                    ("post", f"/api/demo/orders/{self.order.order_number}/courier-fee/", {"proposed_fee_gel": "1"}),
                    ("get", "/api/demo/outbox/", None)]:
                with self.subTest(url=url):
                    self.assertEqual(self.call(APIClient(), method, url, body).status_code, 401)
                    self.assertEqual(self.call(customer, method, url, body).status_code, 403)
        finally:
            clear_url_caches()
            importlib.reload(config.urls)
