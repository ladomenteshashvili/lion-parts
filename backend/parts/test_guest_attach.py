"""LP-BE-407: guest search logs are attached to the customer on login and registration."""

from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.test_register import PHYSICAL
from accounts.testing import PASSWORD, make_customer
from parts.models import PartSearchLog
from pricing.models import ExchangeRate


@override_settings(PARTS_PROVIDER="demo", SMS_DEMO_MODE=True, ENABLE_DEMO_ENDPOINTS=True)
class AttachGuestSearchesTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=Decimal("2.6000"))
        self.client = APIClient()

    def guest_search(self, part="GUEST-1"):
        return self.client.post("/api/parts/search/", {"part_number": part}, format="json").json()

    def test_login_attaches_the_guest_searches(self):
        customer = make_customer(is_phone_verified=True)
        search = self.guest_search()
        other_guest = APIClient().post("/api/parts/search/", {"part_number": "OTHER"}, format="json").json()
        self.client.post("/api/auth/login/", {"email": "nino@example.com", "password": PASSWORD}, format="json")
        history = self.client.get("/api/parts/history/").json()
        self.assertEqual([i["search_id"] for i in history["results"]], [search["search_id"]])
        self.assertIsNone(PartSearchLog.objects.get(pk=other_guest["search_id"]).customer)  # another browser's
        # the guest search can now be used for a quote request in the new session
        response = self.client.post("/api/parts/quote-requests/", {
            "request_type": "weight_price", "search_id": search["search_id"],
            "offer_id": search["offers"][-1]["offer_id"]}, format="json")
        self.assertEqual(response.status_code, 201, response.content)

    def test_registration_attaches_the_guest_searches(self):
        search = self.guest_search()
        code = self.client.post("/api/auth/register/", PHYSICAL, format="json").json()["demo_code"]
        self.client.post("/api/auth/register/verify-phone/", {"code": code}, format="json")
        self.assertEqual(PartSearchLog.objects.get(pk=search["search_id"]).customer.user.email, "nino@example.com")

    def test_logs_of_another_customer_are_never_moved(self):
        owner = make_customer(email="o@example.com", phone="599000000")
        log = PartSearchLog.objects.create(provider="demo", part_number="X", customer=owner,
                                           session_key=self.client.session.session_key or "s")
        from accounts.services import attach_guest_data
        attach_guest_data(log.session_key, make_customer())
        log.refresh_from_db()
        self.assertEqual(log.customer, owner)
