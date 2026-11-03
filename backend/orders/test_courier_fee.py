"""LP-BE-805: courier fee proposals and accept / decline (G§7.14, owner decision D21)."""

from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.testing import make_customer
from orders.models import CourierFeeProposal, Order, OrderSupportMessage
from orders.services import cancel_order, propose_courier_fee
from orders.testing import make_order
from payments.services import create_payment, mark_payment
from pricing.models import ExchangeRate, PickupPoint
from pricing.rates import RateUnavailable

D = Decimal


class CourierFeeTests(TestCase):
    def setUp(self):
        self.rate = ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        self.customer = make_customer()
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def order(self, currency="GEL", fee=D("26.00")):
        order = make_order(self.customer, currency=currency, status=Order.STATUS_PROCESSING)
        mark_payment(create_payment(order, "invoice"), "paid")
        Order.objects.filter(pk=order.pk).update(delivery_method="delivery", delivery_city="tbilisi")
        if fee is not None:
            propose_courier_fee(order.pk, fee, "Courier to Saburtalo")
        return Order.objects.get(pk=order.pk)

    def post(self, order, answer, client=None):
        return (client or self.client).post(f"/api/orders/{order.order_number}/courier-fee/{answer}/", {},
                                            format="json", headers={"accept-language": "en"})

    def test_proposal_sets_the_action(self):
        order = self.order()
        self.assertEqual((order.status, order.courier_fee_action_required, order.proposed_courier_fee_gel),
                         ("action_required", True, D("26.00")))
        body = self.client.get(f"/api/orders/{order.order_number}/", headers={"accept-language": "en"}).json()
        self.assertEqual((body["courier"]["proposed_fee"], body["attention"]["kind"]), ("26.00", "courier_fee"))

    def test_gel_accept_adds_the_fee_as_a_separate_line(self):
        order = self.order()
        body = self.post(order, "accept").json()
        self.assertEqual((body["courier_fee"], body["total"], body["balance_due"], body["status"]),
                         ("26.00", "286.00", "26.00", "processing"))
        self.assertEqual(body["items"][0]["unit_price"], "260.00")  # item prices untouched
        self.assertIsNotNone(body["courier"]["confirmed_at"])
        self.assertEqual(body["support_messages"][-1]["message"], "Customer accepted the courier fee: ₾26.00.")
        proposal = CourierFeeProposal.objects.get()
        self.assertEqual((proposal.status, proposal.exchange_rate, proposal.amount), ("accepted", None, D("26.00")))

    def test_usd_order_converts_at_the_rate_frozen_with_the_proposal(self):
        order = self.order(currency="USD")  # 26 GEL / 2.6
        before = order.balance_due
        self.rate.rate = D("2.9000")  # the NBG rate changes before the customer answers
        self.rate.save()
        body = self.post(order, "accept").json()
        self.assertEqual(body["courier_fee"], "10.00")
        self.assertEqual(D(body["balance_due"]), before + D("10.00"))
        order.refresh_from_db()
        self.assertEqual((order.exchange_rate, order.items.get().unit_price), (D("1"), D("100.00")))
        proposal = CourierFeeProposal.objects.get()
        self.assertEqual((proposal.fee_gel, proposal.exchange_rate, proposal.amount, proposal.currency),
                         (D("26.00"), D("2.6000"), D("10.00"), "USD"))

    def test_usd_rounding(self):
        order = self.order(currency="USD", fee=D("15.00"))  # 15 / 2.6 = 5.769...
        self.assertEqual(CourierFeeProposal.objects.get(order=order).amount, D("5.77"))

    def test_a_changed_fee_is_a_new_version_and_an_accepted_amount_never_changes(self):
        order = self.order(currency="USD")
        self.rate.rate = D("2.5000")
        self.rate.save()
        propose_courier_fee(order.pk, D("30.00"), "Corrected")  # supersedes the pending one
        first, second = CourierFeeProposal.objects.filter(order=order)
        self.assertEqual((first.status, second.status, second.exchange_rate, second.amount),
                         ("superseded", "pending", D("2.5000"), D("12.00")))
        self.assertEqual(self.post(order, "accept").json()["courier_fee"], "12.00")
        propose_courier_fee(order.pk, D("40.00"), "Another district")
        second.refresh_from_db()
        self.assertEqual((second.status, second.amount), ("accepted", D("12.00")))  # the accepted record stays
        self.assertEqual(Order.objects.get(pk=order.pk).courier_fee, D("12.00"))  # until the new one is accepted

    def test_no_rate_means_no_proposal(self):
        order = self.order(currency="USD", fee=None)
        with mock.patch("pricing.rates.get_usd_rate", side_effect=RateUnavailable()), \
                self.assertRaises(RateUnavailable):
            propose_courier_fee(order.pk, D("26.00"), "")
        self.assertFalse(CourierFeeProposal.objects.exists())

    def test_only_open_delivery_orders(self):
        pickup = make_order(self.customer, status=Order.STATUS_PROCESSING)
        with self.assertRaises(ValueError):
            propose_courier_fee(pickup.pk, D("10"), "")
        with self.assertRaises(ValueError):
            propose_courier_fee(self.order(fee=None).pk, D("-1"), "")

    def test_decline_switches_to_pickup_without_a_fee(self):
        point = PickupPoint.objects.filter(is_active=True).first()
        order = self.order()
        body = self.post(order, "decline").json()
        self.assertEqual((body["delivery_method"], body["pickup_point"]["id"], body["delivery"], body["courier_fee"],
                          body["total"]), ("pickup", point.pk, None, "0.00", "260.00"))
        self.assertIsNotNone(body["courier"]["declined_at"])
        self.assertEqual(body["support_messages"][-1]["message"], "Customer declined the courier fee.")
        self.assertEqual(CourierFeeProposal.objects.get().status, "declined")

    def test_nothing_pending_and_repeats_are_409(self):
        order = self.order()
        self.post(order, "accept")
        for answer in ("accept", "decline"):
            response = self.post(order, answer)
            self.assertEqual((response.status_code, response.json()["code"]), (409, "no_pending_action"))
        self.assertEqual(OrderSupportMessage.objects.filter(order=order).count(), 1)

    def test_foreign_order_is_404_and_a_cancelled_order_closes_the_proposal(self):
        order = self.order()
        other = APIClient()
        other.force_login(make_customer(email="o@example.com", phone="599000000").user)
        self.assertEqual(self.post(order, "accept", other).status_code, 404)
        cancel_order(order.pk, "Supplier failure", actor="operator", operator=True)
        self.assertEqual(self.post(order, "accept").status_code, 409)
        self.assertEqual((Order.objects.get(pk=order.pk).courier_fee, CourierFeeProposal.objects.get().status),
                         (D("0.00"), "cancelled"))
