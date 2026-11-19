"""LPK-248: the Claude and OpenAI research providers, provider fallback and its budget, and the call audit.

The real SDKs run against an in-memory HTTP transport (httpx2.MockTransport): request building, response parsing and the
SDKs' own error mapping are exercised, and nothing leaves this machine. Any other HTTP transport fails the test."""

import csv
import json
import logging
import threading
from decimal import Decimal
from io import StringIO
from unittest import mock, skipUnless

import anthropic
import httpx2
import openai
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from config.tests import run_python
from orders.models import Notification
from parts import ai_providers
from parts.ai_providers import SCHEMA, anthropic_research, estimate, openai_research, prompt, url_key
from parts.management.commands.benchmark_weight_research import DEFAULT_FILE
from parts.models import PartMaster, WeightResearchCall, WeightResearchJob, WeightResearchSpend
from parts.test_weight_research import AGREEING, ON, ResearchTestCase, obs, page
from parts.weight_research import (BUDGET, PROVIDERS, Answer, Observation, ResearchError, _claim, approve_proposal,
                                   research_query,
                                   run_due_jobs, run_job)

D = Decimal
PRICES = {"claude-opus-5-5": {"input_per_mtok": D("4"), "output_per_mtok": D("20"), "search_per_1k": D("10")},
          "gpt-6.2-astra": {"input_per_mtok": D("2"), "output_per_mtok": D("8"), "search_per_1k": D("10")}}
NATIVE = {**ON, "AI_WEIGHT_RESEARCH_PROVIDER": "anthropic", "AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS": [],
          "AI_WEIGHT_RESEARCH_ANTHROPIC_API_KEY": "sk-ant-test-not-real",
          "AI_WEIGHT_RESEARCH_ANTHROPIC_MODEL": "claude-opus-5-5",
          "AI_WEIGHT_RESEARCH_ANTHROPIC_SEARCH_TOOL": "web_search_20260209",
          "AI_WEIGHT_RESEARCH_ANTHROPIC_TIMEOUT_SECONDS": 180, "AI_WEIGHT_RESEARCH_OPENAI_API_KEY": "sk-test-not-real",
          "AI_WEIGHT_RESEARCH_OPENAI_MODEL": "gpt-6.2-astra", "AI_WEIGHT_RESEARCH_OPENAI_SEARCH_TOOL": "web_search",
          "AI_WEIGHT_RESEARCH_OPENAI_TIMEOUT_SECONDS": 180, "AI_WEIGHT_RESEARCH_MAX_SEARCHES": 5,
          "AI_WEIGHT_RESEARCH_MAX_OUTPUT_TOKENS": 8000, "AI_WEIGHT_RESEARCH_PRICES": PRICES}
URLS = [o["url"] for o in AGREEING]
FAKE = "https://invented.example/never-searched"


# --- provider responses, in each API's wire format -------------------------------------------------------------------

def claude_message(observations=AGREEING, results=URLS, stop="tool_use", searches=2, report=True, usage=None,
                   search_error=None):
    content = [{"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
                "input": {"query": "Toyota 90915-YZZE1 weight"}}]
    if search_error:
        content.append({"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                        "content": {"type": "web_search_tool_result_error", "error_code": search_error}})
    else:
        content.append({"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
            {"type": "web_search_result", "url": url, "title": "Oil filter", "encrypted_content": "e", "page_age": None}
            for url in results]})
        content.append({"type": "text", "text": "Shipping weight 0.40 kg", "citations": [
            {"type": "web_search_result_location", "url": results[0], "title": "Oil filter", "encrypted_index": "i",
             "cited_text": "Shipping weight: 0.40 kg"}] if results else None})
    if report:
        content.append({"type": "tool_use", "id": "toolu_1", "name": "report_weights",
                        "input": report if isinstance(report, dict) else {"observations": list(observations)}})
    return {"id": "msg_test_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": usage or {"input_tokens": 20000, "output_tokens": 1500, "cache_read_input_tokens": 0,
                               "cache_creation_input_tokens": 0,
                               "server_tool_use": {"web_search_requests": searches, "web_fetch_requests": 0}}}


def openai_response(observations=AGREEING, results=URLS, status="completed", arguments=None, search_status="completed",
                    usage=True, incomplete=None):
    output = [{"type": "web_search_call", "id": "ws_1", "status": search_status,
               "action": {"type": "search", "query": "Toyota 90915-YZZE1 weight",
                          "sources": [{"type": "url", "url": url} for url in results]}},
              {"type": "web_search_call", "id": "ws_2", "status": search_status,
               "action": {"type": "open_page", "url": results[0] if results else None}}]
    if results:
        text = "Shipping weight 0.40 kg (shop-a)"
        output.append({"type": "message", "id": "msg_1", "role": "assistant", "status": "completed", "content": [
            {"type": "output_text", "text": text, "annotations": [
                {"type": "url_citation", "url": results[0] + "?utm_source=openai", "title": "Oil filter",
                 "start_index": 0, "end_index": 23}]}]})
    if arguments is not False:
        output.append({"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "report_weights",
                       "status": "completed",
                       "arguments": arguments if arguments is not None else json.dumps(
                           {"observations": list(observations)})})
    return {"id": "resp_test_1", "object": "response", "created_at": 1763500000, "model": "gpt-6.2-astra",
            "status": status, "output": output, "error": None, "incomplete_details": incomplete,
            "parallel_tool_calls": True, "tool_choice": "auto", "tools": [], "store": False,
            "usage": {"input_tokens": 10000, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                      "output_tokens": 1000, "output_tokens_details": {"reasoning_tokens": 600},
                      "total_tokens": 11000} if usage else None}


def error_body(status):
    kind = {429: "rate_limit_error", 500: "api_error", 401: "authentication_error"}[status]
    return {"type": "error", "error": {"type": kind, "message": "details the audit must not keep"}}


@override_settings(**NATIVE)
class ProviderTestCase(ResearchTestCase):
    def setUp(self):
        super().setUp()
        self.sent = {"anthropic": [], "openai": []}
        self.replies = {"anthropic": [], "openai": []}  # each: a dict (200 JSON), (status, dict), or an exception
        for name, cls in (("anthropic", anthropic.Anthropic), ("openai", openai.OpenAI)):
            transport = httpx2.MockTransport(lambda request, name=name: self.reply(name, request))
            patcher = mock.patch(f"parts.ai_providers.{name}.{cls.__name__}",
                                 lambda cls=cls, transport=transport, **kw: cls(
                                     **kw, http_client=httpx2.Client(transport=transport)))
            patcher.start()
            self.addCleanup(patcher.stop)
        real_network = mock.patch("httpx2.HTTPTransport.handle_request", side_effect=AssertionError("a real call"))
        real_network.start()
        self.addCleanup(real_network.stop)
        self.search(self.nino_client, vin="JTDBR32E720123456")  # queues the part's job
        httpx_log = logging.getLogger("httpx2")  # the SDKs log every request line at INFO
        httpx_log.setLevel(logging.WARNING)
        self.addCleanup(httpx_log.setLevel, logging.NOTSET)

    def reply(self, name, request):
        self.sent[name].append({"url": str(request.url), "headers": {k.lower(): v for k, v in request.headers.items()},
                                "body": json.loads(request.content)})
        if not self.replies[name]:
            raise AssertionError(f"unexpected {name} call")
        found = self.replies[name].pop(0)
        if isinstance(found, Exception):
            raise found
        status, body = found if isinstance(found, tuple) else (200, found)
        return httpx2.Response(status, json=body, headers={"request-id": "req_test"})

    def query(self):
        return research_query(self.part())

    def calls(self):
        return list(WeightResearchCall.objects.order_by("id"))


class AnthropicProviderTests(ProviderTestCase):
    def test_request_carries_the_part_only_with_search_limits_and_strict_report_tool(self):
        self.replies["anthropic"].append(claude_message())
        anthropic_research(self.query())
        request = self.sent["anthropic"][0]
        body = request["body"]
        self.assertEqual(request["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(request["headers"]["authorization"], "Bearer sk-ant-test-not-real")  # SDK 2.x
        self.assertEqual((body["model"], body["max_tokens"]), ("claude-opus-5-5", 8000))
        search, report = body["tools"]
        self.assertEqual(search, {"type": "web_search_20260209", "name": "web_search", "max_uses": 5,
                                  "user_location": {"type": "approximate", "country": "US"}})
        self.assertEqual((report["name"], report["strict"], report["input_schema"]), ("report_weights", True, SCHEMA))
        self.assertEqual(body["messages"], [{"role": "user", "content": prompt(self.query())}])
        self.assertIn("Part number: 90915YZZE1\n", body["messages"][0]["content"])

    def test_verified_answer_with_citations_usage_and_estimated_cost(self):
        self.replies["anthropic"].append(claude_message(observations=[*AGREEING, obs(FAKE)]))
        answer = anthropic_research(self.query())
        self.assertEqual(([o.url for o in answer.observations], answer.unsourced, answer.error), (URLS, 1, ""))
        self.assertEqual((answer.model, answer.request_id, answer.searches), ("claude-opus-5-5", "msg_test_1", 2))
        self.assertEqual(answer.citations, [{"url": URLS[0], "title": "Oil filter",
                                             "cited_text": "Shipping weight: 0.40 kg"}])
        # 20000 x $4/M + 1500 x $20/M + 2 x $10/1000
        self.assertEqual(answer.cost, D("0.1300"))
        self.assertEqual(answer.usage["server_tool_use"], {"web_search_requests": 2, "web_fetch_requests": 0})
        self.assertIn("reconcile with the provider's billing", answer.cost_note)

    def test_unpriced_model_or_cache_writes_leave_the_cost_unknown(self):
        with override_settings(AI_WEIGHT_RESEARCH_PRICES={}):
            self.replies["anthropic"].append(claude_message())
            self.assertIsNone(anthropic_research(self.query()).cost)
        self.replies["anthropic"].append(claude_message(usage={
            "input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 5000,
            "server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 0}}))
        answer = anthropic_research(self.query())
        self.assertEqual((answer.cost, answer.cost_note[:12]), (None, "cost unknown"))

    def test_unusable_answers_are_errors_with_their_cost(self):
        cases = ((claude_message(stop="refusal", report=False), "anthropic: refused"),
                 (claude_message(stop="max_tokens", report=False), "anthropic: answer cut off at max tokens"),
                 (claude_message(stop="pause_turn", report=False), "anthropic: search did not finish"),
                 (claude_message(stop="end_turn", report=False), "anthropic: no report_weights call"),
                 (claude_message(report={"observations": "none"}), "anthropic: malformed answer"),
                 (claude_message(report=False, search_error="unavailable", stop="end_turn"),
                  "anthropic: web search unavailable (unavailable)"))
        for message, error in cases:
            with self.subTest(error=error):
                self.replies["anthropic"].append(message)
                answer = anthropic_research(self.query())
                self.assertEqual((answer.error, answer.observations), (error, []))
                self.assertIsNotNone(answer.cost)  # answered: billed, so its cost is kept

    def test_transport_failures_raise_without_the_error_body(self):
        for reply, text in (((429, error_body(429)), "anthropic: rate limited (HTTP 429)"),
                            ((500, error_body(500)), "anthropic: HTTP 500"),
                            ((401, error_body(401)), "anthropic: HTTP 401"),
                            (httpx2.ReadTimeout("slow"), "anthropic: timed out"),
                            (httpx2.ConnectError("refused"), "anthropic: APIConnectionError")):
            with self.subTest(text=text):
                self.replies["anthropic"].append(reply)
                with self.assertRaises(ResearchError) as caught:
                    anthropic_research(self.query())
                self.assertEqual((str(caught.exception), caught.exception.sent), (text, True))
        self.assertEqual(len(self.sent["anthropic"]), 5)  # max_retries=0: one request each, the job retries later

    def test_missing_key_sends_nothing(self):
        with override_settings(AI_WEIGHT_RESEARCH_ANTHROPIC_API_KEY=""), self.assertRaises(ResearchError) as caught:
            anthropic_research(self.query())
        self.assertEqual((str(caught.exception), caught.exception.sent),
                         ("anthropic: API key or model not configured", False))
        self.assertEqual(self.sent["anthropic"], [])


@override_settings(AI_WEIGHT_RESEARCH_PROVIDER="openai")
class OpenAIProviderTests(ProviderTestCase):
    def test_request_carries_the_part_only_is_not_stored_and_bounds_searches(self):
        self.replies["openai"].append(openai_response())
        openai_research(self.query())
        request = self.sent["openai"][0]
        body = request["body"]
        self.assertEqual(request["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(request["headers"]["authorization"], "Bearer sk-test-not-real")
        self.assertEqual((body["model"], body["store"], body["max_tool_calls"], body["max_output_tokens"]),
                         ("gpt-6.2-astra", False, 5, 8000))
        self.assertEqual(body["include"], ["web_search_call.action.sources"])
        search, report = body["tools"]
        self.assertEqual(search, {"type": "web_search", "user_location": {"type": "approximate", "country": "US"}})
        self.assertEqual((report["type"], report["name"], report["strict"], report["parameters"]),
                         ("function", "report_weights", True, SCHEMA))
        self.assertEqual(body["input"], prompt(self.query()))

    def test_verified_answer_with_citations_usage_and_estimated_cost(self):
        self.replies["openai"].append(openai_response(observations=[*AGREEING, obs(FAKE)]))
        answer = openai_research(self.query())
        self.assertEqual(([o.url for o in answer.observations], answer.unsourced, answer.error), (URLS, 1, ""))
        self.assertEqual((answer.model, answer.request_id, answer.searches), ("gpt-6.2-astra", "resp_test_1", 2))
        self.assertEqual(answer.citations, [{"url": URLS[0] + "?utm_source=openai", "title": "Oil filter",
                                             "cited_text": "Shipping weight 0.40 kg"}])
        # 10000 x $2/M + 1000 x $8/M + 2 calls x $10/1000
        self.assertEqual(answer.cost, D("0.0480"))
        self.assertEqual(answer.usage["output_tokens_details"], {"reasoning_tokens": 600})

    def test_unusable_answers_are_errors(self):
        cases = ((openai_response(status="incomplete", incomplete={"reason": "max_output_tokens"}, arguments=False),
                  "openai: answer max_output_tokens"),
                 (openai_response(arguments="{not json"), "openai: malformed answer"),
                 (openai_response(arguments=False), "openai: no report_weights call"),
                 (openai_response(results=[], search_status="failed", arguments=False),
                  "openai: web search unavailable"))
        for response, error in cases:
            with self.subTest(error=error):
                self.replies["openai"].append(response)
                answer = openai_research(self.query())
                self.assertEqual((answer.error, answer.observations), (error, []))

    def test_missing_usage_leaves_the_cost_unknown(self):
        self.replies["openai"].append(openai_response(usage=False))
        answer = openai_research(self.query())
        self.assertEqual((answer.cost, answer.usage, len(answer.observations)), (None, {}, 3))

    def test_transport_failures_raise(self):
        for reply, text in (((429, error_body(429)), "openai: rate limited (HTTP 429)"),
                            (httpx2.ReadTimeout("slow"), "openai: timed out")):
            with self.subTest(text=text):
                self.replies["openai"].append(reply)
                with self.assertRaisesMessage(ResearchError, text):
                    openai_research(self.query())


class JobTests(ProviderTestCase):
    """Whole jobs through run_job: verification, fallback, budgets, audit, approval."""

    def test_claude_answer_is_verified_and_waits_for_the_operator(self):
        self.replies["anthropic"].append(claude_message(observations=[*AGREEING, obs(FAKE)]))
        self.assertEqual(run_due_jobs()["proposed"], 1)
        job = self.job()
        self.assertEqual((job.status, job.provider, job.cost_usd, self.spent()), ("done", "anthropic", D("0.13"),
                                                                                  D("0.13")))
        self.assertEqual((job.proposed_weight.status, job.proposed_weight.weight_kg), ("pending", D("0.40")))
        self.assertIn("1 more observation(s) dropped: their URL was never returned", job.explanation)
        self.assertTrue(job.conflicting)  # an invented citation makes the provider's answer suspect
        self.assertNotIn(FAKE, self.sources())  # never fetched, never stored as a source
        self.assertFalse(Notification.objects.exists())
        call, = self.calls()
        self.assertEqual((call.provider, call.model, call.outcome, call.request_id, call.searches, call.unsourced,
                          call.reserved_usd, call.cost_usd), ("anthropic", "claude-opus-5-5", "answered",
                                                              "msg_test_1", 2, 1, D("0.50"), D("0.13")))
        approve_proposal(job.pk, self.staff)
        job.proposed_weight.refresh_from_db()
        self.assertEqual(job.proposed_weight.status, "approved")
        self.assertFalse(Notification.objects.exists())  # approving alone prices and notifies nobody

    def test_fabricated_citations_never_count(self):
        # The search did return these URLs, but the pages show another weight or nothing: nothing is proposed.
        self.web.update({URLS[0]: page("Shipping weight: 2.5 kg"), URLS[1]: page("No weight here")})
        self.web.pop(URLS[2])
        self.replies["anthropic"].append(claude_message())
        run_due_jobs()
        job = self.job()
        self.assertEqual((job.status, job.proposed_weight), ("no_result", None))
        self.assertEqual({u: s.verification for u, s in self.sources().items()},
                         {URLS[0]: "contradicted", URLS[1]: "not_confirmed", URLS[2]: "inaccessible"})

    def test_no_customer_vin_contact_or_price_reaches_a_provider(self):
        self.replies["anthropic"].append(claude_message())
        run_due_jobs()
        sent = json.dumps(self.sent["anthropic"][0]["body"])
        for secret in ("JTDBR32E720123456", "nino@example.com", "555000001", "40.00", "tariff"):
            self.assertNotIn(secret, sent)
        self.assertEqual(set(self.query()), {"part_number", "manufacturer", "name", "max_cost_usd", "min_sources"})

    @override_settings(AI_WEIGHT_RESEARCH_PROVIDER="openai")
    def test_switching_the_provider_is_a_setting(self):
        self.replies["openai"].append(openai_response())
        run_due_jobs()
        self.assertEqual((self.job().status, self.job().provider), ("done", "openai"))
        self.assertEqual(self.sent["anthropic"], [])

    @override_settings(AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS=["openai"])
    def test_fallback_after_a_failure_both_calls_audited_and_settled(self):
        self.replies["anthropic"].append((429, error_body(429)))
        self.replies["openai"].append(openai_response())
        with self.assertLogs("parts.weight_research", "WARNING") as logs:
            run_due_jobs()
        job = self.job()
        # the failed call may have been billed: its reservation stands; OpenAI's estimate replaces its own
        self.assertEqual((job.status, job.provider, job.cost_usd, self.spent()), ("done", "openai", D("0.548"),
                                                                                  D("0.548")))
        self.assertEqual([(c.provider, c.outcome, c.error, c.cost_usd) for c in self.calls()],
                         [("anthropic", "failed", "anthropic: rate limited (HTTP 429)", None),
                          ("openai", "answered", "", D("0.048"))])
        self.assertNotIn("details the audit must not keep", "\n".join(logs.output) + self.calls()[0].error)

    @override_settings(AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS=["openai"])
    def test_an_unused_fallback_gives_its_reservation_back(self):
        self.replies["anthropic"].append(claude_message())
        run_due_jobs()
        self.assertEqual((self.job().cost_usd, self.spent(), len(self.calls())), (D("0.13"), D("0.13"), 1))

    @override_settings(AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS=["openai"], AI_WEIGHT_RESEARCH_ANTHROPIC_API_KEY="")
    def test_a_call_never_sent_costs_nothing(self):
        self.replies["openai"].append(openai_response())
        with self.assertLogs("parts.weight_research", "WARNING"):
            run_due_jobs()
        self.assertEqual(self.spent(), D("0.048"))
        self.assertEqual([(c.provider, c.outcome, c.cost_usd) for c in self.calls()],
                         [("anthropic", "not_sent", D("0")), ("openai", "answered", D("0.048"))])

    @override_settings(AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS=["openai"], AI_WEIGHT_RESEARCH_DAILY_BUDGET_USD=D("0.75"))
    def test_no_fallback_unless_the_budget_covers_it(self):
        self.replies["anthropic"].append((500, error_body(500)))
        now = timezone.now()
        with self.assertLogs("parts.weight_research", "WARNING"):
            self.assertEqual(run_job(self.job().pk, now), "queued")  # retried later, never on OpenAI today
        self.assertEqual(self.sent["openai"], [])
        self.assertEqual((self.job().cost_usd, self.spent()), (D("0.50"), D("0.50")))
        self.assertEqual(run_job(self.job().pk, now), BUDGET)

    @override_settings(AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS=["openai"],
                       AI_WEIGHT_RESEARCH_MAX_COST_PER_JOB_USD=D("0.50"))
    def test_no_fallback_beyond_the_job_limit(self):
        job = _claim(self.job().pk, timezone.now())
        self.assertEqual((job.chain, job.cost_usd, self.spent()), (["anthropic"], D("0.50"), D("0.50")))

    @override_settings(AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS=["openai"])
    def test_every_provider_failing_is_one_failed_attempt(self):
        self.replies["anthropic"].append(claude_message(stop="refusal", report=False))
        self.replies["openai"].append(httpx2.ReadTimeout("slow"))
        with self.assertLogs("parts.weight_research", "WARNING"):
            self.assertEqual(run_job(self.job().pk), "queued")
        job = self.job()
        self.assertEqual((job.attempts, job.last_error), (1, "anthropic: refused; openai: timed out"))
        self.assertEqual(self.spent(), D("0.6300"))  # Claude's estimate (answered) + OpenAI's reservation (unknown)

    @override_settings(AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS=["openai"])
    def test_an_unforeseen_answer_shape_is_one_failed_call(self):
        broken = claude_message()
        broken["content"][1]["content"] = [{"type": "web_search_result"}]  # no url
        self.replies["anthropic"].append(broken)
        self.replies["openai"].append(openai_response())
        with self.assertLogs("parts.weight_research", "WARNING"):
            run_due_jobs()
        self.assertEqual([(c.provider, c.outcome, c.error[:31]) for c in self.calls()],
                         [("anthropic", "failed", "anthropic: unexpected Attribute"), ("openai", "answered", "")])
        self.assertEqual(self.job().status, "done")

    def test_an_estimate_over_the_per_call_limit_is_not_used(self):
        self.replies["anthropic"].append(claude_message(usage={
            "input_tokens": 200000, "output_tokens": 1000, "server_tool_use": {"web_search_requests": 5,
                                                                               "web_fetch_requests": 0}}))
        run_due_jobs()
        job = self.job()
        self.assertEqual((job.status, job.last_error, job.proposed_weight), ("failed", "cost limit exceeded", None))
        self.assertEqual(self.spent(), D("0.87"))  # what it really cost counts against the day


class SettingsTests(SimpleTestCase):
    def test_provider_names_match_the_registry(self):
        from django.conf import settings
        self.assertEqual(set(settings.AI_RESEARCH_PROVIDERS), set(PROVIDERS))

    def test_defaults_are_off_and_unpriced(self):
        result = run_python("import config.settings as s; print(s.AI_WEIGHT_RESEARCH_ENABLED, "
                            "s.AI_WEIGHT_RESEARCH_PRICES, s.AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS)",
                            {"AI_WEIGHT_RESEARCH_ENABLED": ""})
        self.assertEqual(result.stdout.strip(), "False {} []", result.stderr)

    def test_native_providers_need_their_keys_and_valid_prices(self):
        result = run_python("import config.settings", {
            "AI_WEIGHT_RESEARCH_ENABLED": "True", "AI_WEIGHT_RESEARCH_PROVIDER": "anthropic",
            "AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS": "anthropic", "AI_WEIGHT_RESEARCH_MAX_SEARCHES": "50",
            "AI_WEIGHT_RESEARCH_PRICES": '{"m": {"input_per_mtok": "-1", "output_per_mtok": 1, "search_per_1k": 1}}'})
        self.assertNotEqual(result.returncode, 0)
        for text in ("ANTHROPIC_API_KEY and _MODEL must be set", "must name different providers",
                     "MAX_SEARCHES must be 1 to 20", "PRICES is not valid: prices must be non-negative"):
            self.assertIn(text, result.stderr)

    def test_a_configured_native_setup_starts(self):
        result = run_python("import config.settings as s; print(s.AI_WEIGHT_RESEARCH_PRICES['m']['search_per_1k'])", {
            "AI_WEIGHT_RESEARCH_ENABLED": "True", "AI_WEIGHT_RESEARCH_PROVIDER": "anthropic",
            "AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS": "openai", "AI_WEIGHT_RESEARCH_ANTHROPIC_API_KEY": "k1",
            "AI_WEIGHT_RESEARCH_OPENAI_API_KEY": "k2",
            "AI_WEIGHT_RESEARCH_PRICES": '{"m": {"input_per_mtok": "4", "output_per_mtok": 20, "search_per_1k": 10}}'})
        self.assertEqual(result.stdout.strip(), "10", result.stderr)

    def test_url_key_and_estimate(self):
        self.assertEqual(url_key("https://www.Shop.example/p/?utm_source=openai&id=2"),
                         url_key("https://shop.example/p?id=2"))
        self.assertNotEqual(url_key("https://shop.example/p?id=2"), url_key("https://shop.example/p?id=3"))
        with override_settings(AI_WEIGHT_RESEARCH_PRICES=PRICES):
            self.assertEqual(estimate(("snapshot-x", "gpt-6.2-astra"), 1_000_000, 0, 0), D("2.0000"))
            self.assertIsNone(estimate(("claude-opus-5-5",), 1, 1, None))  # search count unknown
            self.assertIsNone(estimate(("unpriced",), 1, 1, 1))

    def test_the_provider_module_imports_only_the_official_sdks(self):
        self.assertEqual((ai_providers.anthropic.__name__, ai_providers.openai.__name__), ("anthropic", "openai"))


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
@override_settings(**{**NATIVE, "AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS": ["openai"],
                      "AI_WEIGHT_RESEARCH_DAILY_BUDGET_USD": D("1.50")})
class ConcurrentFallbackTests(TransactionTestCase):
    """Overlapping runs with a fallback chain: the day's budget is never over-reserved (claims only, no calls)."""

    serialized_rollback = True

    def test_parallel_claims_reserve_whole_chains_then_partial_then_none(self):
        ids = [WeightResearchJob.objects.create(part=PartMaster.objects.create(
            part_number=f"P{i}", brand="Bosch")).pk for i in range(4)]
        barrier, results, now = threading.Barrier(len(ids)), [], timezone.now()

        def worker(job_id):
            try:
                barrier.wait()
                results.append(_claim(job_id, now))
            finally:
                connection.close()
        threads = [threading.Thread(target=worker, args=(job_id,)) for job_id in ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        chains = sorted(len(r.chain) for r in results if isinstance(r, WeightResearchJob))
        self.assertEqual((chains, results.count(BUDGET)), ([1, 2], 2))  # $1.50 = one chain of 2 + one primary only
        self.assertEqual(WeightResearchSpend.objects.get().reserved_usd, D("1.50"))


class BenchmarkTests(ResearchTestCase):
    """The benchmark command: a dry run by default, an explicit spend confirmation, and no database writes."""

    def setUp(self):
        super().setUp()
        for name in ("anthropic_research", "openai_research"):
            patcher = mock.patch(f"parts.ai_providers.{name}", side_effect=AssertionError("a paid call"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_command(self, *args):
        out, err = StringIO(), StringIO()
        call_command("benchmark_weight_research", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_the_list_has_50_to_100_distinct_us_market_parts(self):
        with open(DEFAULT_FILE, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(50 <= len(rows) <= 100)
        self.assertEqual(len({(r["manufacturer"], r["part_number"]) for r in rows}), len(rows))
        self.assertTrue(all(r["manufacturer"] and r["part_number"] and r["name"] for r in rows))

    def test_dry_run_by_default_sends_nothing_and_states_the_worst_case(self):
        out, err = self.run_command("--providers", "anthropic,openai")
        self.assertEqual(out, "")
        self.assertIn("62 parts x 2 provider(s) (anthropic, openai): 124 calls, worst case $62.00", err)
        self.assertIn("Dry run, nothing sent.", err)

    def test_a_run_needs_a_spend_confirmation_covering_the_worst_case(self):
        for extra in ((), ("--confirm-spend", "0.99"), ("--confirm-spend", "lots")):
            with self.subTest(extra=extra), self.assertRaisesMessage(CommandError, "--confirm-spend of at least 1.00"):
                self.run_command("--run", "--limit", "2", "--providers", "anthropic", *extra)
        with self.assertRaisesMessage(CommandError, "unknown provider(s): gemini"):
            self.run_command("--providers", "gemini")

    def test_a_confirmed_run_verifies_each_answer_and_saves_nothing(self):
        answers = [Answer([Observation(**o) for o in AGREEING], D("0.13"), model="claude-opus-5-5", searches=2),
                   ResearchError("anthropic: timed out")]
        with mock.patch("parts.ai_providers.anthropic_research", side_effect=answers):
            out, err = self.run_command("--run", "--limit", "2", "--providers", "anthropic", "--confirm-spend", "1")
        first, second = list(csv.DictReader(StringIO(out)))
        self.assertEqual((first["part_number"], first["verified"], first["proposal_kg"], first["measurement"],
                          first["cost_usd"]), ("90915-YZZE1", "3", "0.40", "shipping", "0.13"))
        self.assertEqual((second["part_number"], second["error"]), ("90915-YZZN1", "anthropic: timed out"))
        self.assertIn("Done: about $0.63", err)
        self.assertEqual((WeightResearchJob.objects.count(), WeightResearchCall.objects.count()), (0, 0))
        self.assertFalse(PartMaster.objects.filter(part_number="90915-YZZN1").exists())
