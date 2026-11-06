"""UAT scenarios that need server-side state a black-box client cannot set: expiry, outages, tariffs, late provider results.
Scenario ids (D1-D4) match docs/uat-matrix.md. No network: supplier, NBG and SMS are fakes."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.utils import timezone

from accounts.models import CustomerTariff, PhoneVerificationCode
from orders.models import NotificationChannelSetting
from parts.models import PartQuoteRequest
from parts.services import mark_quote_ready
from payments.models import Payment
from payments.providers import get_provider
from payments.services import confirm_manual_payment, record_provider_result
from pricing.models import ExchangeRate, SiteSettings
from uat import journeys
from uat.tests import test_journeys
from uat.tests.test_journeys import TestClientContext

class ScenarioTestCase(test_journeys.JourneyTests):
    """The setup of the journeys, without running them again (the loop below)."""

    def setUp(self):
        super().setUp()
        self.ctx = TestClientContext()

    def customer(self, **tariff):
        api, identity = journeys.customer(self.ctx)
        if tariff:
            CustomerTariff.objects.update(**tariff)
        return api, identity


for _name in [name for name in dir(test_journeys.JourneyTests) if name.startswith("test_")]:
    setattr(ScenarioTestCase, _name, None)


class AuthenticationTests(ScenarioTestCase):
    """D1"""

    def registration(self, language="en"):
        api, identity = self.ctx.client(language), journeys.new_identity()
        return api, identity, api.post("/api/auth/register/", {**identity, "preferred_language": language})

    def test_d1_expired_code(self):
        api, _identity, sent = self.registration()
        PhoneVerificationCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        api.post("/api/auth/register/verify-phone/", {"code": sent["demo_code"]}, expect=400, code="otp_expired")

    def test_d1_too_many_wrong_codes_then_the_right_one_fails(self):
        api, _identity, sent = self.registration()
        wrong = "000000" if sent["demo_code"] != "000000" else "111111"
        left = [api.post("/api/auth/register/verify-phone/", {"code": wrong}, expect=400)["attempts_left"] for _ in range(5)]
        self.assertEqual(left, [4, 3, 2, 1, 0])
        api.post("/api/auth/register/verify-phone/", {"code": sent["demo_code"]}, expect=400,
                 code="otp_attempts_exceeded")

    def test_d1_a_code_is_six_digits_and_only_for_its_session(self):
        _api, _identity, sent = self.registration()
        self.assertRegex(sent["demo_code"], r"^\d{6}$")
        self.ctx.client().post("/api/auth/register/verify-phone/", {"code": sent["demo_code"]}, expect=400,
                               code="registration_expired")

    def test_d1_a_used_email_verification_link_is_harmless_and_a_bad_one_refused(self):
        api, identity = journeys.register(self.ctx)
        token = journeys.token_of(self.ctx.email_link(identity["email"], "/verify-email"))
        api.post("/api/auth/email/verify/", {"token": "bad"}, expect=400, code="token_invalid")
        api.post("/api/auth/email/verify/", {"token": token})
        api.post("/api/auth/email/send-verification/", expect=409, code="already_verified")

    def test_d1_email_change_needs_the_password_and_the_new_address_link(self):
        api, identity = self.customer()
        api.post("/api/profile/email/", {"new_email": "new-uat@example.com", "current_password": "wrong"}, expect=400)
        api.post("/api/profile/email/", {"new_email": "new-uat@example.com", "current_password": journeys.PASSWORD})
        link = self.ctx.email_link("new-uat@example.com", "/profile/email/confirm")
        api.post("/api/profile/email/confirm/", {"token": journeys.token_of(link)})
        self.assertEqual(api.get("/api/profile/")["email"], "new-uat@example.com")
        self.assertTrue(any(identity["email"] in message.to and "new-uat@example.com" in message.body
                            for message in mail.outbox), "the old address is told")

    def test_d1_a_password_change_ends_other_sessions(self):
        api, identity = self.customer()
        other = self.ctx.client()
        other.post("/api/auth/login/", {"email": identity["email"], "password": journeys.PASSWORD})
        api.post("/api/profile/password/", {"current_password": journeys.PASSWORD, "new_password": "An0ther!pass"})
        other.get("/api/profile/", expect=401)
        self.assertTrue(api.get("/api/auth/me/")["authenticated"])

    def test_d1_errors_are_localized_and_codes_are_not(self):
        answers = {language: self.ctx.client(language).post(
            "/api/auth/login/", {"email": "x@example.com", "password": "y"}, expect=400) for language in ("ka", "en", "ru")}
        self.assertEqual({answer["code"] for answer in answers.values()}, {"invalid_credentials"})
        self.assertEqual(len({answer["detail"] for answer in answers.values()}), 3)
        self.assertRegex(answers["ka"]["detail"], "[\u10a0-\u10ff]")
        self.assertRegex(answers["ru"]["detail"], "[Ѐ-ӿ]")


class PartsAndCartTests(ScenarioTestCase):
    """D2"""

    @override_settings(PARTS_PROVIDER="amt", AMT_API_LOGIN="u", AMT_API_PASSWORD="p")
    def test_d2_supplier_unavailable_answers_502_in_every_language(self):
        with mock.patch("parts.amt_provider.requests.post", side_effect=requests.ConnectionError()):
            details = {self.ctx.client(language).post(
                "/api/parts/search/", {"part_number": "X-1"}, expect=502, code="supplier_unavailable")["detail"]
                for language in ("ka", "en", "ru")}
        self.assertEqual(len(details), 3)

    def test_d2_no_exchange_rate_answers_502_for_gel(self):
        guest = self.ctx.client()
        ExchangeRate.objects.all().delete()
        with mock.patch("pricing.rates.requests.get", side_effect=requests.ConnectionError()), \
                self.assertLogs("pricing.rates", "WARNING"):
            guest.post("/api/parts/search/", {"part_number": journeys.PART, "currency": "GEL"}, expect=502,
                       code="rate_unavailable")

    def test_d2_currency_conversion_follows_the_rate_and_keeps_usd(self):
        SiteSettings.objects.update(multi_currency_enabled=True)
        guest = self.ctx.client()
        usd = journeys.add_to_cart(guest)
        self.assertEqual(usd["currency"], "USD")
        gel = guest.patch("/api/cart/", {"currency": "GEL"})
        self.assertEqual(gel["currency"], "GEL")
        self.assertEqual(Decimal(gel["items"][0]["unit_price"]),
                         (Decimal(usd["items"][0]["unit_price"]) * Decimal("2.6")).quantize(Decimal("0.01")))
        ExchangeRate.objects.update(rate=Decimal("3.0000"))
        moved = guest.get("/api/cart/")
        self.assertEqual(Decimal(moved["items"][0]["unit_price"]),
                         (Decimal(usd["items"][0]["unit_price"]) * Decimal("3")).quantize(Decimal("0.01")))
        self.assertEqual(guest.patch("/api/cart/", {"currency": "USD"})["items"][0]["unit_price"],
                         usd["items"][0]["unit_price"])

    def test_d2_expired_line_blocks_checkout_until_refreshed(self):
        from cart.models import CartItem

        api, _identity = self.customer()
        line = journeys.add_to_cart(api)["items"][0]
        CartItem.objects.update(price_valid_until=timezone.now() - timedelta(days=1))
        cart = api.get("/api/cart/")
        self.assertEqual((cart["checkout_blocked"], cart["items"][0]["status"]), (True, "expired"))
        options = api.get("/api/checkout/options/")
        answer = api.post("/api/orders/checkout/", {
            "delivery_method": "pickup", "pickup_point_id": options["pickup_points"][0]["id"], "payment_option": "full",
            "payment_method": "card", "terms_accepted": True}, expect=409, code="cart_invalid")
        self.assertEqual(answer["reasons"], [{"item_id": line["id"], "code": "expired"}])
        self.assertFalse(api.post(f"/api/cart/items/{line['id']}/refresh/")["checkout_blocked"])

    def test_d2_an_expired_offer_cannot_be_added(self):
        from parts.models import PartSearchLog

        guest = self.ctx.client()
        found = guest.post("/api/parts/search/", {"part_number": journeys.PART})
        PartSearchLog.objects.update(price_valid_until=timezone.now() - timedelta(days=1))
        guest.post("/api/cart/items/", {"source": "search", "search_id": found["search_id"],
                                        "offer_id": found["offers"][0]["offer_id"]}, expect=409, code="offer_expired")

    def test_d2_a_price_in_the_request_is_ignored(self):
        guest = self.ctx.client()
        found = guest.post("/api/parts/search/", {"part_number": journeys.PART})
        cart = guest.post("/api/cart/items/", {"source": "search", "search_id": found["search_id"], "unit_price": "0.01",
                                               "offer_id": found["offers"][0]["offer_id"]}, expect=201)
        self.assertEqual(cart["items"][0]["unit_price"], found["offers"][0]["unit_price"])

    def test_d2_manual_price_request_through_the_public_link_into_an_order(self):
        api, _identity = self.customer(can_request_quote=True)
        made = api.post("/api/parts/quote-requests/", {"request_type": "manual_search", "part_number": "M-1"}, expect=201)
        api.post("/api/cart/items/", {"source": "prepared_quote", "quote_request_id": made["id"]}, expect=409,
                 code="offer_not_ready")
        mark_quote_ready(made["id"], Decimal("2.00"), "Ready", supplier_price_usd=Decimal("100.00"))
        token = PartQuoteRequest.objects.get(pk=made["id"]).notification_token
        public = self.ctx.client().get(f"/api/public/q/{token}/")
        self.assertEqual((public["part_number"], public["requires_login"]), ("M-1", True))
        self.assertNotIn("customer", public)
        cart = api.post("/api/cart/items/", {"source": "prepared_quote", "quote_request_id": made["id"]}, expect=201)
        self.assertEqual(cart["items"][0]["unit_price"], public["unit_price"])
        order, _payment = journeys.checkout(api)
        self.assertEqual(order["items"][0]["part_number"], "M-1")

    def test_d2_someone_elses_quote_cannot_be_added(self):
        owner, _identity = self.customer(can_request_quote=True)
        made = owner.post("/api/parts/quote-requests/", {"request_type": "manual_search", "part_number": "M-1"}, expect=201)
        mark_quote_ready(made["id"], Decimal("2.00"), "", supplier_price_usd=Decimal("100.00"))
        stranger, _ = journeys.register(self.ctx)
        stranger.post("/api/cart/items/", {"source": "prepared_quote", "quote_request_id": made["id"]}, expect=404)

    def test_d2_weight_quotation_needs_the_tariff_and_consent(self):
        from parts import providers

        rows = [{"offer_id": "D-1", "name": "No weight", "brand": "B", "supplier_price_usd": Decimal("50"),
                 "weight_kg": None, "note": ""}]
        api, _identity = self.customer(can_enter_weight=False)
        with mock.patch.object(providers, "_demo_rows", return_value=([], rows)):
            found = api.post("/api/parts/search/", {"part_number": "W-1"})
            offer = found["offers"][0]
            self.assertTrue(offer["requires_weight"])
            body = {"search_id": found["search_id"], "offer_id": offer["offer_id"], "weight_kg": "2.00",
                    "weight_consent": True}
            api.post("/api/parts/calculate-price/", body, expect=403, code="weight_entry_not_allowed")
            CustomerTariff.objects.update(can_enter_weight=True)
            priced = api.post("/api/parts/calculate-price/", body)
        self.assertEqual(priced["offer"]["weight_kg"], "2.00")


class OrdersAndFakePaymentTests(ScenarioTestCase):
    """D3"""

    def unpaid(self, method="card"):
        api, _identity = self.customer()
        journeys.add_to_cart(api)
        order, payment = journeys.checkout(api, method=method)
        return api, order["order_number"], Payment.objects.get(pk=payment["id"])

    def provider_says(self, payment, status):
        return record_provider_result(payment, status, get_provider(), payload={"uat": True})

    def test_d3_a_duplicate_paid_result_counts_once(self):
        api, number, payment = self.unpaid()
        for _ in range(3):
            self.provider_says(payment, "paid")
        detail = api.get(f"/api/orders/{number}/")
        self.assertEqual((detail["amount_paid"], detail["balance_due"]), (detail["total"], "0.00"))

    def test_d3_unknown_payment_blocks_and_reads_as_being_checked(self):
        api, number, payment = self.unpaid()
        Payment.objects.filter(pk=payment.pk).update(status="unknown")
        blocked = api.post(f"/api/orders/{number}/payments/", {"method": "invoice"}, expect=409, code="payment_in_progress")
        self.assertEqual((blocked["payment"]["status"], blocked["payment"]["checkout_url"]), ("unknown", None))

    def test_d3_a_paid_result_after_expiry_is_counted_and_visible_to_review(self):
        api, number, payment = self.unpaid()
        self.provider_says(payment, "expired")
        with self.assertLogs("payments.services", "ERROR"):
            self.provider_says(payment, "paid")
        self.assertEqual(api.get(f"/api/orders/{number}/")["payment_status"], "paid")
        self.assertIn("late_paid", Payment.objects.get(pk=payment.pk).review_reasons)

    def test_d3_a_paid_result_after_cancellation_is_owed_back(self):
        api, number, payment = self.unpaid()
        api.post(f"/api/orders/{number}/cancel/", {})
        with self.assertLogs("payments.services", "ERROR"):
            self.provider_says(payment, "paid")
        detail = api.get(f"/api/orders/{number}/")
        self.assertEqual((detail["status"], detail["payment_status"]), ("cancelled", "refund_pending"))
        self.assertEqual(Decimal(detail["balance_due"]), -Decimal(detail["amount_paid"]))

    def test_d3_invoice_confirmed_by_the_operator_moves_the_order_on_and_only_once(self):
        api, number, payment = self.unpaid("invoice")
        staff = get_user_model().objects.create_superuser("uat-operator", "op@example.com", "Uat-Staff!pass1")
        self.assertIsNone(confirm_manual_payment(payment, staff))
        self.assertEqual(confirm_manual_payment(payment, staff), "not_pending")
        self.assertEqual(api.get(f"/api/orders/{number}/")["status"], "processing")

    def test_d3_amounts_on_the_invoice_match_the_order(self):
        api, number, _payment = self.unpaid("invoice")
        detail = api.get(f"/api/orders/{number}/")
        self.assertIn(detail["total"], api.get(f"/api/orders/{number}/invoice/?lang=en"))


class NotificationAndAdminTests(ScenarioTestCase):
    """D4"""

    @override_settings(EMAIL_REPLY_TO="Support <support@example.com>", DEFAULT_FROM_EMAIL="Lion Parts <no-reply@example.com>")
    def test_d4_emails_carry_from_reply_to_and_a_footer_in_the_customers_language(self):
        expected = {"ka": "[\u10a0-\u10ff]", "en": "service message", "ru": "[Ѐ-ӿ]"}
        for language, pattern in expected.items():
            _api, identity = journeys.register(self.ctx, language)
            message = next(m for m in reversed(mail.outbox) if identity["email"] in m.to)
            self.assertEqual(message.reply_to, ["Support <support@example.com>"])
            self.assertEqual(message.from_email, "Lion Parts <no-reply@example.com>")
            footer = message.body[message.body.rindex("support@example.com") - 200:]
            self.assertRegex(footer, pattern)
            self.assertNotIn("\n", message.subject)
            html = message.alternatives[0][0]
            self.assertIn("support@example.com", html)

    def test_d4_public_notification_link_shows_nothing_personal(self):
        from orders.models import Notification

        api, identity = self.customer()
        order = journeys.paid_order(api)
        self.ctx.staff().post(f"/api/demo/orders/{order['order_number']}/items/{order['items'][0]['id']}/action/",
                              {"action_type": "other", "message": "Private operator text"})
        token = Notification.objects.filter(event_type="action_required").latest("id").token
        public = self.ctx.client().get(f"/api/public/n/{token}/")
        self.assertEqual((public["message"], public["requires_login"]), ("", True))
        text = str(public)
        for private in (identity["email"], identity["phone"], identity["first_name"], "Private operator text"):
            self.assertNotIn(private, text)
        Notification.objects.update(created_at=timezone.now() - timedelta(days=31))
        self.ctx.client().get(f"/api/public/n/{token}/", expect=404)

    def test_d4_demo_sms_and_email_channels_never_reach_a_provider(self):
        NotificationChannelSetting.objects.update(channel="both")
        with mock.patch("accounts.sms.requests.post") as post:
            api, _identity = self.customer()
            journeys.paid_order(api)
        post.assert_not_called()

    def test_d4_a_customer_is_refused_on_operator_routes_and_a_staff_session_on_customer_routes(self):
        api, _identity = self.customer()
        order = journeys.paid_order(api)
        item = order["items"][0]["id"]
        api.post(f"/api/demo/orders/{order['order_number']}/items/{item}/status/", {"item_status": "purchased"}, expect=403)
        api.get("/api/demo/outbox/", expect=403)
        self.ctx.staff().get("/api/profile/", expect=403)
        self.assertEqual(self.client.get("/admin/orders/order/").status_code, 302)

    def test_d4_staff_without_the_permission_cannot_change_orders(self):
        from django.test import Client

        from uat.tests.test_journeys import transport_for

        api, _identity = self.customer()
        order = journeys.paid_order(api)
        clerk = get_user_model().objects.create_user("clerk", "clerk@example.com", "Uat-Staff!pass1", is_staff=True)
        client = Client(enforce_csrf_checks=True)
        client.force_login(clerk)
        journeys.Api(transport_for(client)).post(
            f"/api/demo/orders/{order['order_number']}/items/{order['items'][0]['id']}/status/",
            {"item_status": "purchased"}, expect=403)
