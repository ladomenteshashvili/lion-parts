"""LPK-238 to LPK-240: parts registry, reusable weights and the missing-weight queue."""

import threading
import unittest
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone, translation
from rest_framework.test import APIClient

from accounts.models import Customer, CustomerTariff
from accounts.testing import make_customer
from orders.models import Notification
from orders.services import OperatorError
from parts.models import (CarrierService, PartMaster, PartQuoteRequest, PartSearchLog, PartSupplierOffer, PartWeight,
                          PartWeightEvent, WeightTask, WeightTaskRequest)
from parts.providers import OFFER_KEYS, PartsProviderError
from parts.registry import InvalidWeight, clean_weight, get_parts, record_weight, review_weight
from parts.services import mark_quote_ready
from parts.weight_tasks import approve_task, cancel_task, queue_missing_weights
from pricing.engine import PriceInput, price_offer
from pricing.models import ExchangeRate
from pricing.rates import RateUnavailable

D = Decimal
PART = "90915-YZZE1"


def amt_row(brand="Toyota", price="40.00", weight="", oem="90915YZZE1", descr="OIL FILTER"):
    return {"brand": brand, "descr": descr, "list_price": price, "oem": oem, "replace": "", "weight": weight}


@override_settings(PARTS_PROVIDER="amt")
class RegistryTestCase(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.6000"))
        CarrierService.objects.create(name="Air", usd_per_kg=D("8.00"), is_default=True)
        self.staff = get_user_model().objects.create_superuser("root", "root@example.com", "x")
        self.supplier = [amt_row()]

    def amt(self):
        return mock.patch("parts.providers.get_price_by_oem", side_effect=lambda *a, **k: list(self.supplier))

    def customer(self, email, phone, tariff=None, **fields):
        customer = make_customer(email=email, phone=phone, is_phone_verified=True, **fields)
        if tariff:
            Customer.objects.filter(pk=customer.pk).update(tariff=tariff)
            customer.refresh_from_db()
        client = APIClient()
        client.force_login(customer.user)
        return customer, client

    def search(self, client=None, part=PART, vin=""):
        with self.amt():
            response = (client or APIClient()).post("/api/parts/search/", {"part_number": part, "vin": vin}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def part(self, brand="Toyota", oem="90915YZZE1"):
        return PartMaster.objects.get(part_number_key=oem.replace("-", "").upper(), brand_key=brand.upper().replace("-", ""))

    def approve(self, task, weight=D("1.50"), source=PartWeight.SOURCE_OPERATOR):
        with self.amt(), self.captureOnCommitCallbacks(execute=True), translation.override("en"):
            return approve_task(task.pk, self.staff, weight, source)


class IdentityTests(RegistryTestCase):
    def test_same_number_from_two_manufacturers_is_two_parts(self):
        self.supplier = [amt_row("Toyota", weight="0.40"), amt_row("Denso", price="30.00")]
        offers = self.search()["offers"]
        self.assertEqual(PartMaster.objects.count(), 2)
        self.assertEqual([o["status"] for o in offers], ["ready", "checking"])
        # The Toyota weight is never lent to the Denso part: Denso waits for its own.
        self.assertEqual(self.part("Toyota").current_weight.weight_kg, D("0.40"))
        self.assertIsNone(self.part("Denso").current_weight)
        self.assertEqual(list(WeightTask.objects.values_list("part__brand", flat=True)), ["Denso"])

    def test_number_and_manufacturer_spellings_are_normalized(self):
        self.supplier = [amt_row("Mercedes-Benz", oem="A 000-180.26.09")]
        self.search()
        self.supplier = [amt_row("MERCEDES BENZ", oem="A0001802609")]
        self.search()
        part = PartMaster.objects.get()
        self.assertEqual((part.part_number_key, part.brand_key, part.search_count), ("A0001802609", "MERCEDESBENZ", 2))

    def test_searches_link_offers_and_count_demand_without_customer_data(self):
        customer, client = self.customer("a@example.com", "555000001")
        self.search(client, vin="JTDBR32E720123456")
        self.search()
        part = PartMaster.objects.get()
        self.assertEqual((part.search_count, part.vin_search_count, part.last_supplier_price_usd), (2, 1, D("40.00")))
        self.assertEqual(PartSupplierOffer.objects.filter(part=part).count(), 2)
        for model in (PartMaster, PartSupplierOffer, PartWeight, PartWeightEvent):
            related = {f.related_model for f in model._meta.get_fields() if f.is_relation and f.many_to_one}
            self.assertNotIn(Customer, related, model)

    def test_quote_requests_link_to_their_part(self):
        customer, client = self.customer("a@example.com", "555000001")
        data = self.search(client)
        client.post("/api/parts/quote-requests/", {"request_type": "weight_price", "search_id": data["search_id"],
                                                   "offer_id": data["offers"][0]["offer_id"]}, format="json")
        self.assertEqual(PartQuoteRequest.objects.get().part, self.part())

    def test_a_registry_failure_never_fails_the_search(self):
        with mock.patch("parts.services.record_search", side_effect=RuntimeError("boom")), \
                self.assertLogs("parts.services", "ERROR"):
            data = self.search()
        self.assertEqual(len(data["offers"]), 1)
        with mock.patch("parts.registry.get_parts", side_effect=RuntimeError("boom")), \
                self.assertLogs("parts.registry", "ERROR"):
            self.supplier = [amt_row(weight="1.00")]
            self.assertEqual(self.search()["offers"][0]["status"], "ready")

    def test_the_offer_contract_is_unchanged(self):
        self.supplier = [amt_row(weight="0.40"), amt_row("Denso")]
        for offer in self.search()["offers"]:
            self.assertEqual(set(offer), set(OFFER_KEYS))


class WeightKnowledgeTests(RegistryTestCase):
    def test_amt_weight_is_recorded_once_and_seen_again(self):
        self.supplier = [amt_row(weight="0.40")]
        self.search()
        self.search()
        weight = PartWeight.objects.get()
        self.assertEqual((weight.source, weight.status, weight.seen_count), ("amt", "approved", 2))
        self.assertEqual(weight.events.count(), 2)  # recorded, approved

    def test_an_approved_weight_is_reused_when_amt_has_none(self):
        part = get_parts([("90915YZZE1", "Toyota", "")])[("90915YZZE1", "TOYOTA")]
        record_weight(part, D("0.45"), "operator", status="approved", actor=self.staff)
        offer = self.search()["offers"][0]
        self.assertEqual((offer["status"], offer["weight_kg"], offer["weight_source"]), ("ready", "0.45", "operator"))
        self.assertFalse(WeightTask.objects.exists())

    def test_an_older_amt_weight_is_reused_as_a_supplier_weight(self):
        self.supplier = [amt_row(weight="0.40")]
        self.search()
        self.supplier = [amt_row(weight="")]
        offer = self.search()["offers"][0]
        self.assertEqual((offer["status"], offer["weight_kg"], offer["weight_source"]), ("ready", "0.40", "api"))

    def test_stronger_evidence_is_never_overwritten(self):
        self.supplier = [amt_row(weight="1.00")]
        self.search()
        part = self.part()
        record_weight(part, D("2.00"), "operator", status="approved", actor=self.staff)
        offer = self.search()["offers"][0]  # AMT still says 1.00
        self.assertEqual((offer["weight_kg"], offer["weight_source"]), ("2.00", "operator"))
        physical = record_weight(part, D("2.50"), "physically_measured", status="approved", actor=self.staff)
        later = record_weight(part, D("3.20"), "operator", status="approved", actor=self.staff)
        part.refresh_from_db()
        self.assertEqual(part.current_weight, physical)  # measured beats a later operator entry
        self.assertTrue(later.conflict)
        self.supplier = [amt_row(weight="0.90")]
        self.search()
        part.refresh_from_db()
        self.assertEqual(part.current_weight, physical)
        self.assertTrue(PartWeight.objects.get(source="amt", weight_kg=D("0.90")).conflict)

    def test_ai_estimates_wait_for_approval(self):
        part = get_parts([("90915YZZE1", "Toyota", "")])[("90915YZZE1", "TOYOTA")]
        estimate = record_weight(part, D("0.50"), "ai", status="pending")
        self.assertEqual(self.search()["offers"][0]["status"], "checking")
        self.assertTrue(review_weight(estimate.pk, "approved", self.staff))
        self.assertFalse(review_weight(estimate.pk, "approved", self.staff))  # a repeated click changes nothing
        offer = self.search()["offers"][0]
        self.assertEqual((offer["status"], offer["weight_kg"]), ("ready", "0.50"))
        self.assertEqual(list(estimate.events.values_list("action", flat=True)), ["recorded", "approved"])

    def test_invalid_weights_are_refused(self):
        for value in (0, "0", -1, "-0.5", "abc", None, "NaN", "Infinity", "0.004", "1e9"):
            with self.subTest(value=value), self.assertRaises(InvalidWeight):
                clean_weight(value)
        self.assertEqual(clean_weight("1.255"), D("1.26"))
        part = get_parts([("X1", "Toyota", "")])[("X1", "TOYOTA")]
        with self.assertRaises(IntegrityError), transaction.atomic():
            PartWeight.objects.create(part=part, weight_kg=0, source="operator")

    def test_zero_and_implausible_amt_weights_are_not_priced(self):
        self.supplier = [amt_row(weight="0")]
        self.assertEqual(self.search()["offers"][0]["status"], "checking")
        self.assertFalse(PartWeight.objects.exists())
        self.supplier = [amt_row(weight="900")]
        self.assertEqual(self.search()["offers"][0]["status"], "checking")
        weight = PartWeight.objects.get()
        self.assertEqual((weight.status, weight.implausible), ("pending", True))
        self.assertEqual(WeightTask.objects.get().request_count, 2)

    def test_a_rejected_amt_weight_stays_rejected_when_seen_again(self):
        self.supplier = [amt_row(weight="7.00")]
        self.search()
        weight = PartWeight.objects.get()
        review_weight(weight.pk, "rejected", self.staff, "a filter is not 7 kg")
        self.search()
        weight.refresh_from_db()
        self.assertEqual((weight.status, weight.seen_count, PartWeight.objects.count()), ("rejected", 2, 1))
        self.assertEqual(self.search()["offers"][0]["status"], "checking")

    def test_the_operator_quote_weight_becomes_registry_knowledge(self):
        customer, client = self.customer("a@example.com", "555000001")
        data = self.search(client)
        client.post("/api/parts/quote-requests/", {"request_type": "weight_price", "search_id": data["search_id"],
                                                   "offer_id": data["offers"][0]["offer_id"]}, format="json")
        with self.amt():
            mark_quote_ready(PartQuoteRequest.objects.get().pk, D("0.60"), actor=self.staff)
        weight = self.part().current_weight
        self.assertEqual((weight.weight_kg, weight.source, weight.reviewed_by), (D("0.60"), "operator", self.staff))


class MissingWeightQueueTests(RegistryTestCase):
    def setUp(self):
        super().setUp()
        self.partner = CustomerTariff.objects.create(name="Partner", markup_percent=D("5"))
        self.nino, self.nino_client = self.customer("nino@example.com", "555000001")
        self.giorgi, self.giorgi_client = self.customer("giorgi@example.com", "555000002", tariff=self.partner)

    def test_one_task_for_many_customers(self):
        self.search(self.nino_client)
        self.search(self.nino_client)
        self.search(self.giorgi_client)
        self.search()  # a guest is counted, not linked
        task = WeightTask.objects.get()
        self.assertEqual((task.status, task.request_count, task.supplier_price_usd), ("awaiting_weight", 4, D("40.00")))
        self.assertEqual({r.customer for r in task.requests.all()}, {self.nino, self.giorgi})

    def test_approval_prices_each_customer_with_their_tariff_and_notifies_once(self):
        nino_search = self.search(self.nino_client)
        self.search(self.giorgi_client)
        self.supplier = [amt_row(price="44.00")]  # the supplier's price moved since the searches
        task = self.approve(WeightTask.objects.get(), D("0.50"))
        self.assertEqual(task.status, "resolved")
        for customer in (self.nino, self.giorgi):
            quote = PartQuoteRequest.objects.get(customer=customer)
            expected = price_offer(PriceInput(D("44.00"), D("0.50"), D("8.00")), customer=customer,
                                   part_number=quote.part_number, brand="Toyota")
            self.assertEqual((quote.status, quote.unit_price_usd, quote.supplier_price_usd),
                             ("resolved", expected.unit_price_usd, D("44.00")))
            self.assertEqual(Notification.objects.filter(customer=customer, event_type="price_ready").count(), 1)
        nino, giorgi = (PartQuoteRequest.objects.get(customer=c).unit_price_usd for c in (self.nino, self.giorgi))
        self.assertNotEqual(nino, giorgi)  # never one customer's price for another
        quote = PartQuoteRequest.objects.get(customer=self.nino)
        self.assertEqual((quote.search_log_id, quote.part), (nino_search["search_id"], self.part()))
        history = self.nino_client.get("/api/parts/history/").json()
        self.assertEqual((history["results"][0]["status"], history["new_count"]), ("price_ready", 1))
        # A later search prices directly with the approved weight: no new task.
        offer = self.search(self.giorgi_client)["offers"][0]
        self.assertEqual((offer["status"], offer["weight_source"]), ("ready", "operator"))
        self.assertEqual(WeightTask.objects.count(), 1)

    def test_a_repeated_approval_changes_nothing(self):
        self.search(self.nino_client)
        task = self.approve(WeightTask.objects.get())
        before = PartQuoteRequest.objects.get().price_ready_at
        with self.assertRaisesMessage(OperatorError, "already resolved"):
            self.approve(task, D("9.00"))
        self.assertEqual(PartQuoteRequest.objects.get().price_ready_at, before)
        self.assertEqual(Notification.objects.filter(event_type="price_ready").count(), 1)
        self.assertEqual(PartWeight.objects.count(), 1)

    def test_an_open_quote_request_is_priced_not_duplicated(self):
        data = self.search(self.nino_client)
        self.nino_client.post("/api/parts/quote-requests/", {"request_type": "weight_price", "search_id": data["search_id"],
                                                             "offer_id": data["offers"][0]["offer_id"]}, format="json")
        self.approve(WeightTask.objects.get())
        quote = PartQuoteRequest.objects.get()
        self.assertTrue(quote.is_price_ready)

    def test_who_is_not_priced(self):
        self.search(self.nino_client)
        self.search(self.giorgi_client)
        Customer.objects.filter(pk=self.giorgi.pk).update(is_phone_verified=False)
        task = WeightTask.objects.get()
        # The operator priced Nino's own request by hand after the task opened.
        PartQuoteRequest.objects.create(customer=self.nino, request_type="weight_price", part_number=PART, brand="Toyota",
                                        part=self.part(), status="resolved", price_ready_at=timezone.now(),
                                        unit_price_usd=D("50"), price_valid_until=timezone.now())
        self.approve(task)
        outcomes = dict(task.requests.values_list("customer", "outcome"))
        self.assertEqual(outcomes, {self.nino.pk: "already_priced", self.giorgi.pk: "not_eligible"})
        self.assertFalse(Notification.objects.filter(event_type="price_ready").exists())
        self.assertEqual(PartQuoteRequest.objects.count(), 1)

    def test_supplier_unavailable_keeps_the_weight_and_retries(self):
        self.search(self.nino_client)
        task = WeightTask.objects.get()
        with mock.patch("parts.providers.get_price_by_oem", side_effect=PartsProviderError()), \
                translation.override("en"), self.assertRaisesMessage(OperatorError, "supplier is unavailable"):
            approve_task(task.pk, self.staff, D("1.50"))
        task.refresh_from_db()
        self.assertEqual((task.status, task.approved_weight.weight_kg), ("awaiting_pricing", D("1.50")))
        self.assertIn("unavailable", task.last_error)
        self.assertFalse(PartQuoteRequest.objects.exists())
        self.approve(task, weight=None)  # retry: the weight is already approved
        task.refresh_from_db()
        self.assertEqual((task.status, task.last_error, PartWeight.objects.count()), ("resolved", "", 1))
        self.assertTrue(PartQuoteRequest.objects.get().is_price_ready)

    def test_a_part_the_supplier_dropped_is_not_priced(self):
        self.search(self.nino_client)
        self.supplier = [amt_row("Denso")]
        with self.assertRaisesMessage(OperatorError, "no longer offers"):
            self.approve(WeightTask.objects.get())
        self.assertFalse(PartQuoteRequest.objects.exists())

    def test_missing_exchange_rate_does_not_block_pricing(self):
        self.search(self.nino_client)
        ExchangeRate.objects.all().delete()
        self.approve(WeightTask.objects.get())  # quotes are priced in canonical USD; GEL is converted when shown
        self.assertTrue(PartQuoteRequest.objects.get().is_price_ready)
        with mock.patch("pricing.rates.fetch_nbg_usd", side_effect=RateUnavailable()):
            self.assertEqual(self.nino_client.get("/api/parts/quote-requests/").status_code, 502)  # as before
        ExchangeRate.objects.create(date=timezone.localdate(), rate=D("2.7000"))
        self.assertEqual(self.nino_client.get("/api/parts/quote-requests/").status_code, 200)

    def test_invalid_operator_weights(self):
        self.search(self.nino_client)
        task = WeightTask.objects.get()
        for weight, message in ((D("0"), "greater than zero"), (D("-1"), "greater than zero"), (D("600"), "between 0.01")):
            with self.subTest(weight=weight), self.assertRaisesMessage(OperatorError, message):
                self.approve(task, weight)
        with self.assertRaisesMessage(OperatorError, "Enter the weight"):
            self.approve(task, None)
        with self.assertRaisesMessage(OperatorError, "operator or physically measured"):
            self.approve(task, D("1"), source="amt")
        self.assertFalse(PartWeight.objects.exists())

    def test_cancel(self):
        self.search(self.nino_client)
        task = WeightTask.objects.get()
        self.assertTrue(cancel_task(task.pk, self.staff))
        self.assertFalse(cancel_task(task.pk, self.staff))
        with self.assertRaisesMessage(OperatorError, "cancelled"):
            self.approve(task)
        self.search(self.nino_client)  # a new search opens a new task
        self.assertEqual(WeightTask.objects.filter(status="awaiting_weight").count(), 1)

    def test_one_open_task_per_part_in_the_database(self):
        self.search()
        with self.assertRaises(IntegrityError), transaction.atomic():
            WeightTask.objects.create(part=self.part())

    def test_queue_admin(self):
        self.search(self.nino_client)
        task = WeightTask.objects.get()
        self.client.force_login(self.staff)
        url = "/admin/parts/weighttask/"
        page = self.client.get(url, {"queue": "open", "q": "90915"}, headers={"accept-language": "en"})
        self.assertContains(page, "90915YZZE1 · Toyota")
        self.assertEqual(self.client.get(f"{url}{task.pk}/change/").status_code, 200)
        WeightTask.objects.filter(pk=task.pk).update(proposed_weight_kg=D("0.70"), proposed_source="physically_measured")
        with self.amt(), self.captureOnCommitCallbacks(execute=True):
            page = self.client.post(url, {"action": "approve_and_price", "_selected_action": [task.pk]}, follow=True,
                                    headers={"accept-language": "en"})
        self.assertContains(page, "1 customers priced and notified")
        self.assertEqual(self.part().current_weight.source, "physically_measured")
        with self.amt():
            page = self.client.post(url, {"action": "approve_and_price", "_selected_action": [task.pk]}, follow=True,
                                    headers={"accept-language": "en"})
        self.assertContains(page, "already resolved")
        for path in ("partmaster/", f"partmaster/{self.part().pk}/change/", "partweight/", "partweight/add/"):
            self.assertEqual(self.client.get(f"/admin/parts/{path}").status_code, 200, path)
        self.assertNotContains(self.client.get(f"/admin/parts/partmaster/{self.part().pk}/change/"), "nino@example.com")

    def test_weight_admin_add_approve_and_reject(self):
        self.search()
        self.client.force_login(self.staff)
        part = self.part()
        self.client.post("/admin/parts/partweight/add/", {"part": part.pk, "weight_kg": "0.55", "source": "operator",
                                                          "evidence": "scale photo"})
        weight = PartWeight.objects.get()
        self.assertEqual((weight.status, weight.reviewed_by, part.__class__.objects.get().current_weight),
                         ("approved", self.staff, weight))
        page = self.client.post("/admin/parts/partweight/add/", {"part": part.pk, "weight_kg": "0", "source": "operator"})
        self.assertEqual(page.status_code, 200)  # the form refuses it
        self.client.post("/admin/parts/partweight/add/", {"part": part.pk, "weight_kg": "0.80", "source": "ai"})
        estimate = PartWeight.objects.get(source="ai")
        self.assertEqual(estimate.status, "pending")
        self.client.post("/admin/parts/partweight/", {"action": "reject", "_selected_action": [weight.pk]})
        self.assertIsNone(PartMaster.objects.get().current_weight)
        self.client.post("/admin/parts/partweight/", {"action": "approve", "_selected_action": [estimate.pk]})
        self.assertEqual(PartMaster.objects.get().current_weight, estimate)


class BackfillTests(RegistryTestCase):
    def log(self, offers, provider="amt", **fields):
        return PartSearchLog.objects.create(provider=provider, part_number=PART, normalized_response={"offers": offers},
                                            found_count=len(offers), **fields)

    def offer(self, index, brand, weight=None, source="api", oem="90915YZZE1"):
        return {"offer_id": f"AMT-{index}-{oem}", "name": "OIL FILTER", "brand": brand, "supplier_price_usd": "40.00",
                "weight_kg": weight, "weight_source": source if weight else "", "status": "ready" if weight else "checking",
                "snapshot": {"unit_price_usd": "70.00"} if weight else None}

    def run_backfill(self, *args):
        out = StringIO()
        call_command("backfill_part_registry", *args, stdout=out)
        return out.getvalue()

    def test_backfill_is_idempotent_and_keeps_snapshots(self):
        customer = make_customer(is_phone_verified=True)
        first = self.log([self.offer(1, "Toyota", "0.40"), self.offer(2, "Denso")], vin="JTDBR32E720123456")
        self.log([self.offer(1, "Toyota", "0.40")])
        self.log([self.offer(1, "Toyota", "2.40", source="customer")], kind="weight_calc")
        self.log([], status="failed")
        quote = PartQuoteRequest.objects.create(customer=customer, request_type="weight_price", part_number=PART,
                                                search_log=first, offer_id="AMT-2-90915YZZE1", brand="Denso")
        stored = list(PartSearchLog.objects.order_by("id").values_list("normalized_response", flat=True))

        self.assertIn("Dry run", self.run_backfill())
        self.assertFalse(PartMaster.objects.exists())
        report = self.run_backfill("--apply")
        self.assertIn("logs linked: 3", report)
        toyota = self.part("Toyota")
        self.assertEqual((toyota.search_count, toyota.vin_search_count), (2, 1))
        self.assertEqual(toyota.first_seen_at, first.created_at)
        weight = PartWeight.objects.get()  # one AMT weight seen twice; the customer weight is not knowledge
        self.assertEqual((weight.part, weight.weight_kg, weight.seen_count), (toyota, D("0.40"), 2))
        quote.refresh_from_db()
        self.assertEqual(quote.part, self.part("Denso"))
        counts = (PartMaster.objects.count(), PartSupplierOffer.objects.count(), PartWeight.objects.count())

        report = self.run_backfill("--apply")
        self.assertIn("logs linked: 0", report)
        self.assertIn("logs already linked: 3", report)
        self.assertEqual((PartMaster.objects.count(), PartSupplierOffer.objects.count(), PartWeight.objects.count()), counts)
        self.assertEqual(self.part("Toyota").search_count, 2)
        self.assertEqual(list(PartSearchLog.objects.order_by("id").values_list("normalized_response", flat=True)), stored)
        self.assertFalse(WeightTask.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_demo_weights_are_not_knowledge(self):
        self.log([{**self.offer(1, "OEM", "2.00"), "offer_id": "DEMO-1-P-1"}], provider="demo")
        self.run_backfill("--apply")
        self.assertEqual(PartMaster.objects.get().part_number_key, "P1")
        self.assertFalse(PartWeight.objects.exists())


@unittest.skipUnless(connection.vendor == "postgresql", "concurrency needs real row locks")
@override_settings(PARTS_PROVIDER="amt")
class ConcurrentSearchTests(TransactionTestCase):
    serialized_rollback = True  # keep the seeded data for later tests, as the other parallel tests do

    def test_parallel_searches_create_one_part_and_one_task(self):
        customers = [make_customer(email=f"c{i}@example.com", phone=f"55500000{i}") for i in range(4)]
        log = PartSearchLog.objects.create(provider="amt", part_number=PART)
        barrier, errors = threading.Barrier(4), []

        def search(customer):
            try:
                barrier.wait()
                parts = get_parts([("90915YZZE1", "Toyota", "OIL FILTER")])
                row = {"part": parts[("90915YZZE1", "TOYOTA")], "weight_kg": None, "offer_id": "AMT-1-90915YZZE1",
                       "supplier_price_usd": D("40.00")}
                queue_missing_weights(log, [row], customer)
            except Exception as error:  # pragma: no cover - reported below
                errors.append(error)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=search, args=(c,)) for c in customers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(PartMaster.objects.count(), 1)
        task = WeightTask.objects.get()
        self.assertEqual((task.request_count, task.requests.count()), (4, 4))
