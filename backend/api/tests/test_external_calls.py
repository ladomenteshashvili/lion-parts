"""LPK-220: supplier and NBG calls run with no database transaction open, on every path that later takes a row lock.
TransactionTestCase, so in_atomic_block is the application's own state, not the test's. No network: the calls are fakes."""

import threading
import unittest
from decimal import Decimal as D
from unittest import mock

from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import PASSWORD, make_customer
from orders.models import Order
from orders.services import propose_courier_fee
from orders.testing import make_order
from parts.models import PartQuoteRequest
from parts.services import mark_quote_ready
from pricing.models import ExchangeRate, PickupPoint


class Recorder:
    def __init__(self):
        self.in_transaction = []

    def nbg(self, *_args, **_kwargs):
        self.in_transaction.append(connection.in_atomic_block)
        response = mock.Mock()
        response.json.return_value = [{"currencies": [{"code": "USD", "rate": "2.6000", "quantity": 1,
                                                       "validFromDate": timezone.localdate().isoformat()}]}]
        return response

    def amt(self, _part_number):
        self.in_transaction.append(connection.in_atomic_block)
        return [{"oem": "P-1", "descr": "Part", "brand": "OEM", "list_price": "100", "weight": "2"},
                {"oem": "P-2", "descr": "Part", "brand": "OEM", "list_price": "90", "weight": "2"}]


class NoCallInTransactionTests(TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        self.recorder = Recorder()
        for patch in (mock.patch("pricing.rates.requests.get", side_effect=self.recorder.nbg),
                      mock.patch("parts.providers.get_price_by_oem", side_effect=self.recorder.amt)):
            patch.start()
            self.addCleanup(patch.stop)
        self.rate = ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.customer = make_customer(is_phone_verified=True, is_email_verified=True)
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def add_line(self, client=None):
        client = client or self.client
        found = client.post("/api/parts/search/", {"part_number": "P-1"}, format="json").json()
        return client.post("/api/cart/items/", {"source": "search", "search_id": found["search_id"],
                                                "offer_id": found["offers"][0]["offer_id"]}, format="json")

    def rate_goes_stale(self):
        ExchangeRate.objects.all().delete()
        self.recorder.in_transaction.clear()

    def assert_called_outside_transactions(self):
        self.assertTrue(self.recorder.in_transaction, "the external call was expected to run")
        self.assertNotIn(True, self.recorder.in_transaction)

    def test_adding_to_the_cart_with_a_stale_rate(self):
        found = self.client.post("/api/parts/search/", {"part_number": "P-1"}, format="json").json()
        self.rate_goes_stale()
        response = self.client.post("/api/cart/items/", {"source": "search", "search_id": found["search_id"],
                                                         "offer_id": found["offers"][0]["offer_id"]}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assert_called_outside_transactions()

    def test_checkout_with_a_stale_rate(self):
        self.add_line()
        self.rate_goes_stale()
        response = self.client.post("/api/orders/checkout/", {
            "delivery_method": "pickup", "pickup_point_id": PickupPoint.objects.first().pk, "payment_option": "full",
            "payment_method": "invoice", "terms_accepted": True}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assert_called_outside_transactions()

    def test_guest_cart_merge_at_login_with_a_stale_rate(self):
        guest = APIClient()
        self.add_line(guest)
        self.rate_goes_stale()
        response = guest.post("/api/auth/login/", {"email": self.customer.user.email, "password": PASSWORD},
                              format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assert_called_outside_transactions()

    def test_courier_fee_proposal_with_a_stale_rate(self):
        order = make_order(self.customer, currency="USD", status=Order.STATUS_PROCESSING)
        Order.objects.filter(pk=order.pk).update(delivery_method="delivery")
        self.rate_goes_stale()
        propose_courier_fee(order.pk, D("26.00"), "")
        self.assert_called_outside_transactions()

    @override_settings(PARTS_PROVIDER="amt")
    def test_cart_refresh_calls_the_supplier_outside_a_transaction(self):
        line = self.add_line().json()["items"][0]
        self.recorder.in_transaction.clear()
        self.assertEqual(self.client.post(f"/api/cart/items/{line['id']}/refresh/").status_code, 200)
        self.assert_called_outside_transactions()

    @override_settings(PARTS_PROVIDER="amt")
    def test_quote_approval_calls_the_supplier_outside_a_transaction(self):
        qr = PartQuoteRequest.objects.create(customer=self.customer, request_type="weight_price", part_number="P-1",
                                             offer_id="AMT-1-P-1", supplier_price_usd=D("90.00"))
        mark_quote_ready(qr.pk, D("2.00"))
        self.assert_called_outside_transactions()
        qr.refresh_from_db()
        self.assertEqual(qr.supplier_price_usd, D("100"))


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
class SlowSupplierHoldsNoLockTests(TransactionTestCase):
    serialized_rollback = True

    def test_the_quote_row_stays_lockable_during_a_slow_lookup(self):
        customer = make_customer()
        qr = PartQuoteRequest.objects.create(customer=customer, request_type="weight_price", part_number="P-1",
                                             offer_id="AMT-1", supplier_price_usd=D("90.00"))
        locked = []

        def try_the_lock():
            from django.db import transaction
            try:
                with transaction.atomic():
                    locked.append(PartQuoteRequest.objects.select_for_update(nowait=True).get(pk=qr.pk).pk)
            finally:
                connections.close_all()

        def slow_supplier(_part_number):
            thread = threading.Thread(target=try_the_lock)  # another connection, while the lookup is running
            thread.start()
            thread.join()
            return ("amt", {}, [{"offer_id": "AMT-1", "supplier_price_usd": D("100.00")}])

        with mock.patch("parts.providers.fetch_rows", side_effect=slow_supplier):
            mark_quote_ready(qr.pk, D("2.00"))
        self.assertEqual(locked, [qr.pk])
