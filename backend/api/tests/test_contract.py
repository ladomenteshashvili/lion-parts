"""LP-BE-1203: every customer-facing endpoint answers with exactly the key sets of the contract (api/tests/contract.py,
G§8). One realistic journey keeps the state consistent: register, search, quote, cart, checkout, pay, order,
decide, courier fee, support, notifications, profile. Removing or renaming any field fails a test here."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomerTariff
from accounts.test_register import PHYSICAL
from accounts.testing import make_customer
from orders.models import Notification, Order, OrderSupportMessage
from orders.services import propose_courier_fee, request_item_action, set_item_status
from parts.models import CarrierService, PartQuoteRequest
from parts.services import mark_quote_ready
from pricing.models import ExchangeRate, PickupPoint

from . import contract as C

D = Decimal


def keys(obj):
    return set(obj)


@override_settings(PARTS_PROVIDER="demo", FRONTEND_BASE_URL="https://lionparts.ge")
class ContractTestCase(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        CarrierService.objects.create(name="Air", min_eta_days=9, max_eta_days=15, is_default=True)
        CustomerTariff.objects.filter(is_default=True).update(can_enter_weight=True, can_request_quote=True,
                                                               payment_terms="split_70_30")
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def assert_keys(self, obj, expected, name=""):
        self.assertEqual(keys(obj), expected, name)

    def assert_page(self, body, item_keys, extra=frozenset()):
        self.assert_keys(body, C.PAGINATED | set(extra))
        self.assertTrue(body["results"], "a representative state needs at least one row")
        for row in body["results"]:
            self.assert_keys(row, item_keys)

    def search(self):
        body = self.client.post("/api/parts/search/", {"part_number": "G9010-60020"}, format="json").json()
        return body

    def ordered(self, method="card"):
        search = self.search()
        self.client.post("/api/cart/items/", {"source": "search", "search_id": search["search_id"],
                                              "offer_id": search["offers"][0]["offer_id"], "quantity": 1},
                         format="json")
        return self.client.post("/api/orders/checkout/", {
            "delivery_method": "pickup", "pickup_point_id": PickupPoint.objects.get().pk,
            "payment_option": "split_70_30", "payment_method": method, "terms_accepted": True}, format="json").json()


class PublicAndAuthContractTests(ContractTestCase):
    def test_meta_health_csrf_me(self):
        meta = APIClient().get("/api/meta/").json()
        self.assert_keys(meta, C.META)
        self.assert_keys(meta["delivery"], C.DELIVERY_AREAS)
        self.assert_keys(meta["delivery"]["cities"][0], C.CITY)
        self.assert_keys(meta["delivery"]["districts"][0], C.DISTRICT)
        self.assert_keys(meta["pickup_points"][0], C.PICKUP_POINT)
        self.assert_keys(APIClient().get("/api/health/").json(), C.HEALTH)
        self.assert_keys(APIClient().get("/api/auth/csrf/").json(), C.CSRF)
        anonymous = APIClient().get("/api/auth/me/").json()
        self.assert_keys(anonymous, C.ME)
        self.assertEqual((anonymous["customer"], anonymous["badges"]), (None, None))
        me = self.client.get("/api/auth/me/").json()
        self.assert_keys(me, C.ME)
        self.assert_keys(me["customer"], C.CUSTOMER)
        self.assert_keys(me["customer"]["tariff"], C.TARIFF)
        self.assert_keys(me["badges"], C.BADGES)

    def test_registration_login_logout(self):
        client = APIClient()
        sent = client.post("/api/auth/register/", {**PHYSICAL, "email": "new@example.com", "phone": "599123123",
                                                   "personal_id": "01234567891"}, format="json").json()
        self.assert_keys(sent, C.OTP_SENT | {"demo_code"})  # demo_code: test settings (SMS_DEMO_MODE) only
        registered = client.post("/api/auth/register/verify-phone/", {"code": sent["demo_code"]}, format="json")
        self.assertEqual(registered.status_code, 201)
        self.assert_keys(registered.json(), C.REGISTERED)
        self.assert_keys(registered.json()["customer"], C.CUSTOMER)
        self.assert_keys(client.post("/api/auth/logout/", {}, format="json").json(), C.CSRF)
        login = client.post("/api/auth/login/", {"email": "new@example.com", "password": PHYSICAL["password"]},
                            format="json").json()
        self.assert_keys(login, C.LOGGED_IN)

    def test_profile_and_email_verification(self):
        self.assert_keys(self.client.get("/api/profile/").json(), C.CUSTOMER)
        self.assert_keys(self.client.patch("/api/profile/", {"first_name": "Nina"}, format="json").json(), C.CUSTOMER)
        unverified = make_customer(email="u@example.com", phone="599000001")
        client = APIClient()
        client.force_login(unverified.user)
        self.assert_keys(client.post("/api/auth/email/send-verification/", {}, format="json").json(), C.EMAIL_SENT)
        self.assert_keys(self.client.post("/api/profile/phone/", {"phone": "599111222"}, format="json").json(),
                         C.OTP_SENT | {"demo_code"})

    def test_error_envelopes(self):
        validation = self.client.post("/api/support/tickets/", {}, format="json").json()
        self.assert_keys(validation, C.ERROR | {"fields"})
        self.assert_keys(validation["fields"]["context"][0], C.FIELD_ERROR)
        self.assert_keys(self.client.get("/api/orders/LP-NOPE/").json(), C.ERROR)
        self.assert_keys(APIClient().get("/api/orders/").json(), C.ERROR)  # 401


class CatalogContractTests(ContractTestCase):
    def test_search_history_weight_quote(self):
        search = self.search()
        self.assert_keys(search, C.SEARCH)
        for offer in search["offers"]:
            self.assert_keys(offer, C.OFFER, offer["offer_id"])
        checking = next(o for o in search["offers"] if o["requires_weight"])
        priced = self.client.post("/api/parts/calculate-price/", {"search_id": search["search_id"],
                                                                  "offer_id": checking["offer_id"],
                                                                  "weight_kg": "2.0", "weight_consent": True},
                                  format="json").json()
        self.assert_keys(priced, C.WEIGHT_PRICED)
        self.assert_keys(priced["offer"], C.OFFER)
        quote = self.client.post("/api/parts/quote-requests/", {"request_type": "manual_search", "part_number": "X-1"},
                                 format="json").json()
        self.assert_keys(quote, C.QUOTE_REQUEST)
        self.assert_page(self.client.get("/api/parts/history/").json(), C.HISTORY_ITEM, {"new_count"})
        PartQuoteRequest.objects.filter(pk=quote["id"]).update(supplier_price_usd=D("100"))
        qr = mark_quote_ready(quote["id"], D("2.0"))
        self.assert_keys(APIClient().get(f"/api/public/q/{qr.notification_token}/").json(), C.PUBLIC_QUOTE)


class OrderContractTests(ContractTestCase):
    def test_cart_checkout_options_and_checkout(self):
        search = self.search()
        cart = self.client.post("/api/cart/items/", {"source": "search", "search_id": search["search_id"],
                                                     "offer_id": search["offers"][0]["offer_id"], "quantity": 1},
                                format="json").json()
        self.assert_keys(cart, C.CART)
        self.assert_keys(cart["summary"], C.CART_SUMMARY)
        self.assert_keys(cart["items"][0], C.CART_ITEM)
        line = cart["items"][0]["id"]
        for response in (self.client.get("/api/cart/"),
                         self.client.patch(f"/api/cart/items/{line}/", {"quantity": 2}, format="json"),
                         self.client.post(f"/api/cart/items/{line}/refresh/", {}, format="json")):
            self.assert_keys(response.json(), C.CART, response.request["PATH_INFO"])
        options = self.client.get("/api/checkout/options/").json()
        self.assert_keys(options, C.CHECKOUT_OPTIONS)
        self.assert_keys(options["payment_options"][0], C.PAYMENT_OPTION)
        self.assert_keys(options["payer"], C.PAYER)
        self.assert_keys(options["pickup_points"][0], C.PICKUP_POINT)
        checkout = self.client.post("/api/orders/checkout/", {
            "delivery_method": "pickup", "pickup_point_id": PickupPoint.objects.get().pk,
            "payment_option": "split_70_30", "payment_method": "card", "terms_accepted": True}, format="json").json()
        self.assert_keys(checkout, C.CHECKOUT)
        self.assert_keys(checkout["order"], C.ORDER)
        self.assert_keys(checkout["payment"], C.PAYMENT)

    def test_order_list_detail_and_nested_objects(self):
        number = self.ordered()["order"]["order_number"]
        order = Order.objects.get(order_number=number)
        Order.objects.filter(pk=order.pk).update(delivery_method="delivery", delivery_city="tbilisi",
                                                 delivery_district="vake")
        item = order.items.get()
        OrderSupportMessage.objects.create(order=order, item=item, sender_type="operator", message="Hi")
        listed = self.client.get("/api/orders/").json()
        self.assert_page(listed, C.ORDER, {"group_counts"})
        self.assert_keys(listed["group_counts"], C.GROUP_COUNTS)
        detail = self.client.get(f"/api/orders/{number}/").json()
        self.assert_keys(detail, C.ORDER_DETAIL)
        self.assert_keys(detail["items"][0], C.ORDER_ITEM)
        self.assert_keys(detail["items"][0]["events"][0], C.ITEM_EVENT)
        self.assert_keys(detail["payments"][0], C.PAYMENT)
        self.assert_keys(detail["courier"], C.COURIER)
        self.assert_keys(detail["delivery"], C.ORDER_DELIVERY)
        self.assert_keys(detail["support_messages"][0], C.SUPPORT_MESSAGE)
        self.assert_keys(detail["progress"], {"step", "of"})
        self.assert_keys(detail["attention"], {"kind", "message"})
        self.assertTrue({"type", "title", "at"} <= keys(detail["timeline"][0]))

    def test_payments_decision_courier_fee_cancel_support(self):
        checkout = self.ordered()
        number, payment_id = checkout["order"]["order_number"], checkout["payment"]["id"]
        self.assert_keys(self.client.get(f"/api/orders/{number}/payments/{payment_id}/").json(), C.PAYMENT)
        in_progress = self.client.post(f"/api/orders/{number}/payments/", {"method": "card"}, format="json").json()
        self.assert_keys(in_progress, C.ERROR | {"payment"})
        self.assert_keys(in_progress["payment"], C.PAYMENT)
        paid = self.client.post(f"/api/payments/fake/{payment_id}/complete/", {"status": "paid"}, format="json")
        self.assert_keys(paid.json(), C.PAYMENT)
        balance = self.client.post(f"/api/orders/{number}/payments/", {"method": "invoice"}, format="json")
        self.assertEqual(balance.status_code, 201)
        self.assert_keys(balance.json(), C.PAYMENT)
        order = Order.objects.get(order_number=number)
        item = order.items.get()
        request_item_action(item.pk, "price_change", "New price", proposed_unit_price=D("300.00"))
        detail = self.client.get(f"/api/orders/{number}/").json()
        self.assert_keys(detail["items"][0]["action"], C.ITEM_ACTION)
        self.assert_keys(self.client.post(f"/api/orders/items/{item.pk}/decision/", {"decision": "accept"},
                                          format="json").json(), C.ORDER_DETAIL)
        Order.objects.filter(pk=order.pk).update(delivery_method="delivery")
        propose_courier_fee(order.pk, D("15"), "Vake")
        self.assert_keys(self.client.post(f"/api/orders/{number}/courier-fee/accept/", {}, format="json").json(),
                         C.ORDER_DETAIL)
        for url, body in ((f"/api/orders/{number}/support/messages/", {"message": "When?", "item_id": item.pk}),
                          (f"/api/orders/{number}/support/acknowledge/", {})):
            self.assert_keys(self.client.post(url, body, format="json").json(), C.ORDER_DETAIL, url)
        unpaid = self.ordered(method="invoice")["order"]["order_number"]
        self.assert_keys(self.client.post(f"/api/orders/{unpaid}/cancel/", {"reason": ""}, format="json").json(),
                         C.ORDER_DETAIL)
        set_item_status(item.pk, "purchased")  # status changes are what the customer follows


class NotificationAndSupportContractTests(ContractTestCase):
    def test_notifications_public_link_and_tickets(self):
        order = Order.objects.get(order_number=self.ordered()["order"]["order_number"])
        n = Notification.objects.create(customer=self.customer, event_type="action_required", order=order,
                                        item=order.items.get())
        self.assert_page(self.client.get("/api/notifications/").json(), C.NOTIFICATION, {"unread_count"})
        public = APIClient().get(f"/api/public/n/{n.token}/").json()
        self.assert_keys(public, C.PUBLIC_NOTIFICATION)
        self.assertEqual(self.client.post(f"/api/notifications/{n.pk}/read/", {}, format="json").status_code, 204)
        created = self.client.post("/api/support/tickets/", {"context": "general", "message": "Hi"}, format="json")
        self.assert_keys(created.json(), C.TICKET_CREATED)
        self.assert_page(self.client.get("/api/support/tickets/").json(), C.TICKET)

    def test_staff_has_no_customer_api(self):
        staff = APIClient()
        staff.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))
        me = staff.get("/api/auth/me/").json()
        self.assertEqual((me["authenticated"], me["customer"]), (False, None))
        self.assertEqual(staff.get("/api/orders/").status_code, 403)
