"""LPK-247: AI part-weight research. Every test mocks the research service; nothing is sent anywhere."""

import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

import requests
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone, translation
from rest_framework.test import APIClient

from config.tests import run_python
from orders.models import Notification
from orders.services import OperatorError
from parts.models import (PartQuoteRequest, PartWeight, PartWeightEvent, WeightResearchJob, WeightResearchSource,
                          WeightTask)
from parts.registry import record_weight
from parts.test_registry import PART, RegistryTestCase, amt_row
from parts.weight_research import (MAX_OBSERVATIONS, Observation, ResearchError, approve_proposal,
                                   call_research_service, check, research_query, run_due_jobs, run_job, score, site,
                                   to_kg)

D = Decimal
ON = {"AI_WEIGHT_RESEARCH_ENABLED": True, "AI_WEIGHT_RESEARCH_URL": "https://research.example/v1/weight",
      "AI_WEIGHT_RESEARCH_API_KEY": "test-key-not-real", "AI_WEIGHT_RESEARCH_MAX_ATTEMPTS": 3,
      "AI_WEIGHT_RESEARCH_MAX_COST_PER_JOB_USD": D("0.50"), "AI_WEIGHT_RESEARCH_DAILY_BUDGET_USD": D("5.00")}


def obs(url="https://shop-a.example/p", value="0.40", unit="kg", measurement="shipping", part_number="90915-YZZE1",
        manufacturer="TOYOTA", **extra):
    return {"url": url, "title": "Oil filter", "part_number": part_number, "manufacturer": manufacturer,
            "value": value, "unit": unit, "measurement": measurement, "excerpt": "Shipping weight 0.40 kg", **extra}


def answer(*observations, cost="0.04"):
    return [Observation(**o) for o in observations], (None if cost is None else D(cost))


EN = {"accept-language": "en"}
AGREEING = (obs(), obs("https://www.shop-b.example/x", "400", "g"), obs("https://parts.shop-c.example/y", "14.1", "oz"))


class FakeResponse:
    def __init__(self, status=200, body=b"{}"):
        self.status_code, self.raw = status, mock.Mock()
        self.raw.read.side_effect = lambda n, decode_content: body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@override_settings(**ON)
class ResearchTestCase(RegistryTestCase):
    def setUp(self):
        super().setUp()
        self.nino, self.nino_client = self.customer("nino@example.com", "555000001")

    def service(self, *observations, cost="0.04", error=None):
        if error:
            return mock.patch("parts.weight_research.call_research_service", side_effect=error)
        return mock.patch("parts.weight_research.call_research_service", return_value=answer(*observations, cost=cost))

    def job(self):
        return WeightResearchJob.objects.get()


class QueueingTests(ResearchTestCase):
    def test_off_by_default_nothing_is_queued_or_run(self):
        with override_settings(AI_WEIGHT_RESEARCH_ENABLED=False):
            self.search(self.nino_client)
            self.assertEqual(run_due_jobs(), {"ran": 0, "proposed": 0, "budget_reached": False})
            out = StringIO()
            call_command("run_weight_research", stdout=out)
        self.assertIn("off", out.getvalue())
        self.assertFalse(WeightResearchJob.objects.exists())
        self.assertEqual(WeightTask.objects.count(), 1)  # the existing queue is unchanged

    def test_settings_default_is_off(self):
        result = run_python("import config.settings as s; print(s.AI_WEIGHT_RESEARCH_ENABLED)",
                            {"AI_WEIGHT_RESEARCH_ENABLED": ""})
        self.assertEqual(result.stdout.strip(), "False", result.stderr)

    def test_enabled_without_https_or_key_is_refused(self):
        result = run_python("import config.settings", {"AI_WEIGHT_RESEARCH_ENABLED": "True",
                                                       "AI_WEIGHT_RESEARCH_URL": "http://research.example"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("https://", result.stderr)
        self.assertIn("AI_WEIGHT_RESEARCH_API_KEY must be set", result.stderr)

    def test_one_job_per_part_however_many_searches(self):
        giorgi, giorgi_client = self.customer("giorgi@example.com", "555000002")
        self.search(self.nino_client)
        self.search(giorgi_client)
        self.search()  # a guest
        self.assertEqual(self.job().status, "queued")
        self.assertEqual(self.job().part, self.part())

    def test_no_second_search_within_the_cooldown_whatever_the_outcome(self):
        self.search(self.nino_client)
        WeightResearchJob.objects.update(status="failed")
        self.search(self.nino_client)
        self.assertEqual(WeightResearchJob.objects.count(), 1)
        WeightResearchJob.objects.update(created_at=timezone.now() - timedelta(days=31))
        self.search(self.nino_client)
        self.assertEqual(WeightResearchJob.objects.count(), 2)

    def test_one_active_job_per_part_in_the_database(self):
        self.search(self.nino_client)
        with self.assertRaises(IntegrityError), transaction.atomic():
            WeightResearchJob.objects.create(part=self.part())

    def test_only_amt_parts_without_a_usable_weight_with_a_manufacturer(self):
        self.supplier = [amt_row(weight="0.40"), amt_row("", price="30.00", oem="X1")]
        self.search(self.nino_client)  # Toyota has AMT's weight; the unbranded row cannot be checked
        self.assertFalse(WeightResearchJob.objects.exists())
        with override_settings(PARTS_PROVIDER="demo"):
            APIClient().post("/api/parts/search/", {"part_number": "DEMO1"}, format="json")
        self.assertFalse(WeightResearchJob.objects.exists())  # demo rows are invented

    def test_the_service_gets_only_part_identity_never_customer_or_vin(self):
        self.search(self.nino_client, vin="JTDBR32E720123456")
        query = research_query(self.job().part)
        self.assertEqual(set(query), {"part_number", "manufacturer", "name", "max_cost_usd", "min_sources"})
        self.assertEqual((query["part_number"], query["manufacturer"], query["name"]),
                         ("90915YZZE1", "Toyota", "OIL FILTER"))
        sent = json.dumps(query)
        for private in ("JTDBR32E720123456", "nino@example.com", "555000001", "40.00"):
            self.assertNotIn(private, sent)


class AdapterTests(ResearchTestCase):
    def post(self, response):
        return mock.patch("parts.weight_research.requests.post", return_value=response)

    def test_request_shape_auth_timeout_and_parsing(self):
        body = json.dumps({"cost_usd": "0.03", "observations": [obs(), "junk", {"no_url": 1}]}).encode()
        with self.post(FakeResponse(body=body)) as post:
            found, cost = call_research_service({"part_number": "X"})
        args, kwargs = post.call_args
        self.assertEqual(args, ("https://research.example/v1/weight",))
        self.assertEqual(kwargs["json"], {"part_number": "X"})
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer test-key-not-real"})
        self.assertEqual(kwargs["timeout"], (10, 60))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual((len(found), found[0].url, cost), (1, "https://shop-a.example/p", D("0.03")))

    def test_failures_become_research_errors(self):
        for response, reason in ((FakeResponse(500), "HTTP 500"), (FakeResponse(302), "HTTP 302"),
                                 (FakeResponse(body=b"not json"), "JSONDecodeError"),
                                 (FakeResponse(body=b'{"observations": 1}'), "unexpected response shape"),
                                 (FakeResponse(body=b" " * (600 * 1024)), "response too large")):
            with self.subTest(reason=reason), self.post(response), self.assertRaisesMessage(ResearchError, reason):
                call_research_service({})
        with mock.patch("parts.weight_research.requests.post", side_effect=requests.Timeout("secret detail")), \
                self.assertRaisesMessage(ResearchError, "Timeout"):
            call_research_service({})

    def test_observations_are_capped_and_cost_may_be_missing(self):
        body = json.dumps({"observations": [obs()] * 50}).encode()
        with self.post(FakeResponse(body=body)):
            found, cost = call_research_service({})
        self.assertEqual((len(found), cost), (MAX_OBSERVATIONS, None))


class EvidenceTests(ResearchTestCase):
    def setUp(self):
        super().setUp()
        self.search(self.nino_client)
        self.p = self.part()

    def test_checks_on_each_observation(self):
        cases = [(obs(), D("0.400"), "valid"), (obs(part_number="90915 yzze1"), D("0.400"), "valid"),
                 (obs(part_number="90915-YZZE2"), None, "names a different part number"),
                 (obs(manufacturer="Denso"), None, "names a different or no manufacturer"),
                 (obs(manufacturer=""), None, "names a different or no manufacturer"),
                 (obs(url="http://shop-a.example/p"), None, "not an https URL"),
                 (obs(measurement=""), None, "measurement is not net or shipping weight"),
                 (obs(measurement="volumetric"), None, "measurement is not net or shipping weight"),
                 (obs(unit="stone"), None, "weight or unit not understood"),
                 (obs(value="about 1"), None, "weight or unit not understood"),
                 (obs(value="900"), None, "outside 0.01 to 500 kg"),
                 (obs(value="400", unit="g"), D("0.400"), "valid"), (obs(value="0,4"), D("0.400"), "valid"),
                 (obs(value="2", unit="lbs"), D("0.907"), "valid"), (obs(measurement="Net"), D("0.400"), "valid")]
        for observation, weight, reason in cases:
            with self.subTest(observation=observation):
                got = check(self.p, Observation(**observation))
                self.assertEqual((got[0], got[3]), (weight, reason))

    def test_sites_and_units(self):
        self.assertEqual(site("https://www.Shop.example.co.uk/a?b"), "example.co.uk")
        self.assertEqual(site("https://parts.rockauto.com/x"), "rockauto.com")
        self.assertEqual(site("ftp://a.com"), "")
        self.assertEqual(to_kg("1 200", "g"), D("1.200"))
        self.assertIsNone(to_kg("-1", "kg"))
        self.assertIsNone(to_kg("Infinity", "kg"))

    def test_score_is_explained(self):
        proposal, confidence, agreeing, lines = score({"a.com": D("0.40"), "b.com": D("0.40")})
        self.assertEqual((proposal, confidence, agreeing), (D("0.40"), D("0.65"), {"a.com", "b.com"}))
        self.assertIn("Base 0.55", "\n".join(lines))
        self.assertIn("+0.10: the agreeing sites are within 2 %", "\n".join(lines))
        # two agree (0.55, not within 2 %: no bonus), one disagrees (-0.15): 0.40 is below 0.50, nothing proposed
        proposal, confidence, _a, lines = score({"a.com": D("0.40"), "b.com": D("0.42"), "c.com": D("0.80")})
        self.assertEqual((proposal, confidence), (None, D("0.40")))
        self.assertIn("-0.15 for each site that disagrees (c.com)", "\n".join(lines))
        self.assertIn("Confidence 0.40 is below 0.50: no weight proposed.", "\n".join(lines))
        proposal, confidence, _a, _l = score({"a.com": D("0.40"), "b.com": D("0.42"), "c.com": D("0.41"),
                                              "d.com": D("0.90")})
        self.assertEqual((proposal, confidence), (D("0.41"), D("0.50")))  # 0.55 + 0.10 (3 sites) - 0.15
        self.assertIsNone(score({"a.com": D("0.40")})[0])
        self.assertIsNone(score({"a.com": D("0.40"), "b.com": D("0.90")})[0])

    def test_one_site_saying_it_twice_is_one_source(self):
        with self.service(obs(), obs("https://shop-a.example/other", "0.40")):
            self.assertEqual(run_job(self.job().pk), "no_result")
        self.assertIn("At least 2 independent sites must agree", self.job().explanation)
        self.assertFalse(PartWeight.objects.filter(source="ai").exists())


class RunTests(ResearchTestCase):
    def setUp(self):
        super().setUp()
        self.search(self.nino_client)

    def test_a_proposal_is_pending_with_its_evidence_and_prices_nothing(self):
        bad = obs("https://shop-d.example/z", part_number="OTHER")
        with self.service(*AGREEING, bad):
            self.assertEqual(run_due_jobs(), {"ran": 1, "proposed": 1, "budget_reached": False})
        job = self.job()
        weight = job.proposed_weight
        self.assertEqual((job.status, job.measurement, job.cost_usd, job.attempts), ("done", "shipping", D("0.04"), 1))
        self.assertEqual((weight.source, weight.status, weight.weight_kg), ("ai", "pending", D("0.40")))
        self.assertEqual(job.confidence, D("0.75"))  # 3 agreeing sites (0.65), within 2 % of each other (+0.10)
        for text in ("https://shop-a.example/p", "https://parts.shop-c.example/y (14.1 oz, shipping)",
                     "confidence 0.75", "1 observation(s) did not count"):
            self.assertIn(text, weight.evidence)
        self.assertNotIn("shop-d", weight.evidence.split("Sources:")[1])
        sources = {s.domain: s for s in WeightResearchSource.objects.filter(job=job)}
        self.assertEqual(len(sources), 4)
        self.assertEqual((sources["shop-d.example"].accepted, sources["shop-d.example"].reason),
                         (False, "names a different part number"))
        self.assertTrue(sources["shop-c.example"].agrees)
        self.assertEqual(sources["shop-c.example"].weight_kg, D("0.400"))
        self.assertEqual(list(weight.events.values_list("action", flat=True)), ["recorded"])
        # No price until an operator approves: the offer is still `checking`, the task still waits.
        offer = self.search(self.nino_client)["offers"][0]
        self.assertEqual((offer["status"], offer["weight_kg"]), ("checking", None))
        self.assertEqual(WeightTask.objects.get().status, "awaiting_weight")
        self.assertFalse(PartQuoteRequest.objects.exists())
        self.assertFalse(Notification.objects.filter(event_type="price_ready").exists())

    def test_operator_approval_prices_the_waiting_customers_once(self):
        with self.service(*AGREEING):
            run_due_jobs()
        with self.amt(), self.captureOnCommitCallbacks(execute=True), translation.override("en"):
            task = approve_proposal(self.job().pk, self.staff)
        self.assertEqual(task.status, "resolved")
        quote = PartQuoteRequest.objects.get(customer=self.nino)
        self.assertEqual((quote.prepared_weight_kg, quote.status), (D("0.40"), "resolved"))
        self.assertEqual(Notification.objects.filter(customer=self.nino, event_type="price_ready").count(), 1)
        weight = self.job().proposed_weight
        self.assertEqual((weight.status, weight.reviewed_by), ("approved", self.staff))
        offer = self.search(self.nino_client)["offers"][0]
        self.assertEqual((offer["status"], offer["weight_kg"], offer["weight_source"]), ("ready", "0.40", "operator"))
        with self.assertRaisesMessage(OperatorError, "already approved; nothing changed"), translation.override("en"):
            approve_proposal(self.job().pk, self.staff)
        self.assertEqual(Notification.objects.filter(event_type="price_ready").count(), 1)

    def test_a_stronger_weight_is_never_replaced(self):
        with self.service(*AGREEING):
            run_due_jobs()
        operator = record_weight(self.part(), D("0.55"), PartWeight.SOURCE_OPERATOR, status="approved",
                                 actor=self.staff)
        with self.amt(), self.captureOnCommitCallbacks(execute=True):
            approve_proposal(self.job().pk, self.staff)
        part = self.part()
        self.assertEqual(part.current_weight, operator)
        self.assertEqual(PartQuoteRequest.objects.get().prepared_weight_kg, D("0.55"))

    def test_a_weight_known_before_the_run_skips_the_search(self):
        record_weight(self.part(), D("0.55"), PartWeight.SOURCE_OPERATOR, status="approved", actor=self.staff)
        with self.service(*AGREEING) as service:
            self.assertEqual(run_job(self.job().pk), "skipped")
        service.assert_not_called()
        self.assertEqual(self.job().cost_usd, 0)

    def test_a_weight_approved_during_the_search_wins(self):
        def meanwhile(query):
            record_weight(self.part(), D("0.55"), PartWeight.SOURCE_OPERATOR, status="approved", actor=self.staff)
            return answer(*AGREEING)
        with mock.patch("parts.weight_research.call_research_service", side_effect=meanwhile):
            self.assertEqual(run_job(self.job().pk), "skipped")
        self.assertFalse(PartWeight.objects.filter(source="ai").exists())
        self.assertEqual(WeightResearchSource.objects.count(), 3)  # the evidence is kept

    def test_reject_and_research_again_in_the_admin(self):
        with self.service(*AGREEING):
            run_due_jobs()
        client = self.client
        client.force_login(self.staff)
        job = self.job()
        url = "/admin/parts/weightresearchjob/"
        for page in (url, f"{url}{job.pk}/change/", "/admin/parts/weighttask/"):
            response = client.get(page)
            self.assertEqual(response.status_code, 200, page)
        self.assertContains(client.get("/admin/parts/weighttask/", headers=EN), "0.40 kg · confidence 0.75")
        self.assertContains(client.get(f"{url}{job.pk}/change/"), "shop-c.example")
        client.post(url, {"action": "reject", "_selected_action": [job.pk]})
        self.assertEqual(self.job().proposed_weight.status, "rejected")
        with self.assertRaisesMessage(OperatorError, "rejected"), translation.override("en"):
            approve_proposal(job.pk, self.staff)
        client.post(url, {"action": "retry", "_selected_action": [job.pk]})
        self.assertEqual(WeightResearchJob.objects.filter(status="queued").count(), 1)
        client.post(url, {"action": "retry", "_selected_action": [job.pk]})  # already queued: no second job
        self.assertEqual(WeightResearchJob.objects.count(), 2)
        self.assertEqual(client.post(f"{url}{job.pk}/delete/").status_code, 403)

    def test_approve_and_price_from_the_admin(self):
        with self.service(*AGREEING):
            run_due_jobs()
        client = self.client
        client.force_login(self.staff)
        with self.amt(), self.captureOnCommitCallbacks(execute=True):
            response = client.post("/admin/parts/weightresearchjob/",
                                   {"action": "approve_and_price", "_selected_action": [self.job().pk]}, follow=True,
                                   headers=EN)
        self.assertContains(response, "resolved")
        self.assertEqual(WeightTask.objects.get().status, "resolved")


class LimitTests(ResearchTestCase):
    def setUp(self):
        super().setUp()
        self.search(self.nino_client)

    def test_bounded_retries_with_backoff(self):
        now = timezone.now()
        with self.service(error=ResearchError("Timeout")), self.assertLogs("parts.weight_research", "WARNING") as logs:
            self.assertEqual(run_job(self.job().pk, now), "queued")
            job = self.job()
            self.assertEqual((job.attempts, job.last_error, job.next_attempt_at), (1, "Timeout",
                                                                                 now + timedelta(minutes=10)))
            self.assertEqual(run_due_jobs(now=now)["ran"], 0)  # not due yet
            self.assertEqual(run_job(job.pk, now), "queued")
            self.assertEqual(self.job().next_attempt_at, now + timedelta(minutes=20))
            self.assertEqual(run_job(job.pk, now), "failed")
        self.assertEqual((self.job().attempts, self.job().cost_usd), (3, D("1.50")))  # each failure counted at the cap
        output = "\n".join(logs.output)
        self.assertNotIn("test-key-not-real", output)
        self.assertNotIn(PART, output)

    def test_a_job_another_run_claimed_is_not_run_twice(self):
        WeightResearchJob.objects.update(status="running", started_at=timezone.now())
        with self.service(*AGREEING) as service:
            self.assertIsNone(run_job(self.job().pk))
            self.assertEqual(run_due_jobs()["ran"], 0)
        service.assert_not_called()

    def test_a_run_that_died_gives_the_job_back(self):
        WeightResearchJob.objects.update(status="running", attempts=1, started_at=timezone.now() - timedelta(hours=1))
        run_due_jobs()
        self.assertEqual((self.job().status, self.job().last_error, self.job().cost_usd),
                         ("queued", "interrupted", D("0.50")))

    def test_a_cost_over_the_job_limit_discards_the_answer(self):
        with self.service(*AGREEING, cost="0.75"):
            self.assertEqual(run_job(self.job().pk), "failed")
        self.assertEqual((self.job().last_error, self.job().cost_usd), ("cost limit exceeded", D("0.75")))
        self.assertFalse(PartWeight.objects.filter(source="ai").exists())

    def test_an_unreported_cost_counts_as_the_limit(self):
        with self.service(*AGREEING, cost=None):
            run_job(self.job().pk)
        self.assertEqual(self.job().cost_usd, D("0.50"))

    @override_settings(AI_WEIGHT_RESEARCH_DAILY_BUDGET_USD=D("1.00"))
    def test_the_daily_budget_stops_the_run(self):
        part = self.part()
        WeightResearchJob.objects.create(part=part, status="done", cost_usd=D("0.60"), started_at=timezone.now())
        with self.service(*AGREEING) as service:
            self.assertEqual(run_due_jobs(), {"ran": 0, "proposed": 0, "budget_reached": True})
            out = StringIO()
            call_command("run_weight_research", stdout=out)
        service.assert_not_called()
        self.assertIn("daily budget reached", out.getvalue())
        self.assertEqual(WeightResearchJob.objects.filter(status="queued").count(), 1)
