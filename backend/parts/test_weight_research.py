"""LPK-247: AI part-weight research. Every test mocks the research provider and every web page; nothing is sent
anywhere and no paid API is called."""

import json
import socket
import threading
import time
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock, skipUnless

import requests
import urllib3
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone, translation
from rest_framework.test import APIClient

from config.tests import run_python
from orders.models import Notification
from orders.services import OperatorError
from parts import evidence
from parts.evidence import (Unverifiable, check_page, fetch_page, page_text, parse_page, public_address,
                            registrable_domain)
from parts.models import (PartMaster, PartQuoteRequest, PartWeight, WeightResearchJob, WeightResearchSource,
                          WeightResearchSpend, WeightTask)
from parts.registry import record_weight
from parts.test_registry import PART, RegistryTestCase, amt_row
from parts.weight_research import (BUDGET, MAX_OBSERVATIONS, Observation, ResearchError, Rules, _claim,
                                   approve_and_price, approve_proposal, call_research_service, check, credibility,
                                   research_query, rules, run_due_jobs, run_job, score, site)
from parts.weight_tasks import approve_task

D = Decimal
ON = {"AI_WEIGHT_RESEARCH_ENABLED": True, "AI_WEIGHT_RESEARCH_PROVIDER": "http",
      "AI_WEIGHT_RESEARCH_URL": "https://research.example/v1/weight", "AI_WEIGHT_RESEARCH_API_KEY": "test-key-not-real",
      "AI_WEIGHT_RESEARCH_MAX_ATTEMPTS": 3, "AI_WEIGHT_RESEARCH_MAX_COST_PER_ATTEMPT_USD": D("0.50"),
      "AI_WEIGHT_RESEARCH_MAX_COST_PER_JOB_USD": D("1.50"), "AI_WEIGHT_RESEARCH_DAILY_BUDGET_USD": D("5.00")}
FILLER = "Genuine part. Fits many models; check the fitment table before ordering. Delivery in 3 to 5 days. " * 3


def page(weight_text="Shipping weight: 0.40 kg", number="90915-YZZE1", brand="Toyota"):
    """A single-product page whose one heading names the part, long enough to count as readable."""
    return f"<h1>{brand} oil filter {number}</h1><p>{weight_text}.</p><p>{FILLER}</p>"


def obs(url="https://shop-a.example/p", value="0.40", unit="kg", measurement="shipping", part_number="90915-YZZE1",
        manufacturer="TOYOTA"):
    return {"url": url, "title": "Oil filter", "part_number": part_number, "manufacturer": manufacturer,
            "value": value, "unit": unit, "measurement": measurement, "excerpt": "Shipping weight 0.40 kg"}


def answer(*observations, cost="0.04"):
    return [Observation(**o) for o in observations], (None if cost is None else D(cost))


EN = {"accept-language": "en"}
AGREEING = (obs(), obs("https://www.shop-b.example/x", "400", "g"), obs("https://parts.shop-c.example/y", "14.1", "oz"))
AGREEING_PAGES = {"https://shop-a.example/p": page(), "https://www.shop-b.example/x": page("Shipping weight 400 g"),
                  "https://parts.shop-c.example/y": page("Package weight: 14.1 oz")}


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
        self.web = dict(AGREEING_PAGES)  # url -> visible text, (final url, text), or an Unverifiable to raise
        patcher = mock.patch("parts.weight_research.fetch_page", side_effect=self.fetch)
        self.fetched = patcher.start()
        self.addCleanup(patcher.stop)
        no_network = mock.patch("parts.weight_research.requests.post", side_effect=AssertionError("a real call"))
        no_network.start()  # a test that forgets to mock the provider fails instead of calling anything
        self.addCleanup(no_network.stop)

    def fetch(self, url, deadline, robots=None):
        found = self.web.get(url, Unverifiable(evidence.INACCESSIBLE, "the page does not exist (HTTP 404)"))
        if isinstance(found, Exception):
            raise found
        final, html = found if isinstance(found, tuple) else (url, found)
        return final, parse_page(html)

    def service(self, *observations, cost="0.04", error=None):
        if error:
            return mock.patch("parts.weight_research.call_research_service", side_effect=error)
        return mock.patch("parts.weight_research.call_research_service", return_value=answer(*observations, cost=cost))

    def job(self):
        return WeightResearchJob.objects.get()

    def sources(self):
        return {s.url: s for s in WeightResearchSource.objects.filter(job=self.job())}

    def spent(self):
        return WeightResearchSpend.objects.get(day=timezone.localdate()).reserved_usd


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

    def test_enabled_without_https_or_key_or_with_unclear_limits_is_refused(self):
        result = run_python("import config.settings", {"AI_WEIGHT_RESEARCH_ENABLED": "True",
                                                       "AI_WEIGHT_RESEARCH_URL": "http://research.example",
                                                       "AI_WEIGHT_RESEARCH_PROVIDER": "openai",
                                                       "AI_WEIGHT_RESEARCH_MAX_COST_PER_ATTEMPT_USD": "2.00",
                                                       "AI_WEIGHT_RESEARCH_MAX_COST_PER_JOB_USD": "1.00"})
        self.assertNotEqual(result.returncode, 0)
        for text in ("https://", "AI_WEIGHT_RESEARCH_API_KEY must be set", "AI_WEIGHT_RESEARCH_PROVIDER must be http",
                     "at most the per-job limit"):
            self.assertIn(text, result.stderr)

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

    def test_the_provider_gets_only_part_identity_never_customer_or_vin(self):
        self.search(self.nino_client, vin="JTDBR32E720123456")
        query = research_query(self.job().part)
        self.assertEqual(set(query), {"part_number", "manufacturer", "name", "max_cost_usd", "min_sources"})
        self.assertEqual((query["part_number"], query["manufacturer"], query["name"], query["max_cost_usd"]),
                         ("90915YZZE1", "Toyota", "OIL FILTER", "0.50"))
        sent = json.dumps(query)
        for private in ("JTDBR32E720123456", "nino@example.com", "555000001", "40.00"):
            self.assertNotIn(private, sent)


class AdapterTests(ResearchTestCase):
    def post(self, response):
        return mock.patch("parts.weight_research.requests.post", return_value=response)

    def test_request_shape_auth_timeout_and_parsing(self):
        claimed = {**obs(), "verified": True}
        body = json.dumps({"cost_usd": "0.03", "observations": [claimed, "junk", {"no_url": 1}]}).encode()
        with self.post(FakeResponse(body=body)) as post:
            found, cost = call_research_service({"part_number": "X"})
        args, kwargs = post.call_args
        self.assertEqual(args, ("https://research.example/v1/weight",))
        self.assertEqual(kwargs["json"], {"part_number": "X"})
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer test-key-not-real"})
        self.assertEqual(kwargs["timeout"], (10, 60))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual((len(found), found[0].url, cost), (1, "https://shop-a.example/p", D("0.03")))
        self.assertFalse(hasattr(found[0], "verified"))  # a provider's own "verified" claim is dropped

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
        body = json.dumps({"observations": [obs()] * 50, "cost_usd": "NaN"}).encode()
        with self.post(FakeResponse(body=body)):
            found, cost = call_research_service({})
        self.assertEqual((len(found), cost), (MAX_OBSERVATIONS, None))


# --- parts.evidence: no database, no network --------------------------------------------------------------------------

def addrinfo(*addresses):
    return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 443)) for a in addresses]


class FakeHTTP:
    def __init__(self, status=200, body=b"", content_type="text/html; charset=utf-8", location=None, chunks=None):
        self.status, self.headers = status, {"Content-Type": content_type}
        if location:
            self.headers["Location"] = location
        self.chunks = chunks if chunks is not None else [body]
        self.release_conn = mock.Mock()

    def stream(self, size, decode_content):
        yield from self.chunks


class PublicSuffixTests(SimpleTestCase):
    def test_registrable_domains(self):
        for host, expected in (("www.Example.co.uk", "example.co.uk"), ("co.uk", ""),
                               ("a.b.example.com", "example.com"),
                               ("foo.bar.github.io", "bar.github.io"), ("test.ck", ""), ("www.ck", "www.ck"),
                               ("www.city.kobe.jp", "city.kobe.jp"), ("a.b.c.kobe.jp", "b.c.kobe.jp"),
                               ("shop.example.com.", "example.com"), ("127.0.0.1", ""), ("com", ""), ("", ""),
                               ("parts.shop-c.example", "shop-c.example")):
            with self.subTest(host=host):
                self.assertEqual(registrable_domain(host), expected)
        self.assertEqual(site("https://www.Shop.example.co.uk/a?b"), "example.co.uk")
        self.assertEqual(site("http://shop.example.com/"), "")


class SafeFetchTests(SimpleTestCase):
    def fetch(self, url="https://shop.example.com/p?id=1", responses=(), dns=None, deadline=60, robots=None,
              cache=None):
        """responses: the page hops in order; robots: host -> robots.txt response (default 404: no robots.txt)."""
        dns, robots, calls, pages = dns or {}, robots or {}, [], iter(responses)

        def pool(address, **kwargs):
            def urlopen(method, path, **options):
                calls.append((address, kwargs, method, path, options))
                if path == "/robots.txt":
                    return robots.get(kwargs["server_hostname"], FakeHTTP(404))
                return next(pages)
            fake = mock.Mock()
            fake.urlopen.side_effect = urlopen
            return fake
        with mock.patch("parts.evidence.socket.getaddrinfo",
                        side_effect=lambda host, *a, **k: addrinfo(*dns.get(host, ["93.184.215.14"]))) as resolver, \
                mock.patch("parts.evidence.urllib3.HTTPSConnectionPool", side_effect=pool):
            result = fetch_page(url, time.monotonic() + deadline, cache)
        return result, [c for c in calls if c[3] != "/robots.txt"], resolver, calls

    def refused(self, status, note, **kwargs):
        with self.assertRaises(Unverifiable) as raised:
            self.fetch(**kwargs)
        self.assertEqual((raised.exception.status, raised.exception.note), (status, note))

    def test_connects_to_the_checked_address_with_tls_for_the_name(self):
        body = f"<html><head><title>x</title><script>var w='9 kg'</script></head><body>{page()}</body></html>"
        (url, found), pages, resolver, calls = self.fetch(responses=[FakeHTTP(body=body.encode())])
        self.assertEqual([c[3] for c in calls], ["/robots.txt", "/p?id=1"])  # robots.txt first
        self.assertEqual(resolver.call_count, 2)  # once per request; each request uses the address it checked
        address, kwargs, method, path, options = pages[0]
        self.assertEqual(address, "93.184.215.14")
        self.assertEqual((kwargs["port"], kwargs["server_hostname"], kwargs["assert_hostname"], kwargs["cert_reqs"],
                          kwargs["retries"]), (443, "shop.example.com", "shop.example.com", "CERT_REQUIRED", False))
        self.assertEqual((method, path, options["headers"]["Host"], options["redirect"]),
                         ("GET", "/p?id=1", "shop.example.com", False))
        self.assertEqual(url, "https://shop.example.com/p?id=1")
        self.assertIn("Shipping weight: 0.40 kg", found.text)
        self.assertNotIn("9 kg", found.text)  # scripts are not page text

    def test_robots_txt_is_respected(self):
        ok = [FakeHTTP(body=page().encode())]
        for robots, status, note in (
                ("User-agent: LionPartsWeightCheck\nDisallow: /p", evidence.DISALLOWED,
                 "robots.txt does not allow checking this page; not fetched"),
                ("User-agent: *\nDisallow: /", evidence.DISALLOWED,
                 "robots.txt does not allow checking this page; not fetched")):
            with self.subTest(robots=robots):
                self.refused(status, note, responses=ok,
                             robots={"shop.example.com": FakeHTTP(body=robots.encode(), content_type="text/plain")})
        for response, status, note in (
                (FakeHTTP(403), evidence.DISALLOWED,
                 "the site refuses access to its robots.txt (HTTP 403); not fetched"),
                (FakeHTTP(401), evidence.DISALLOWED,
                 "the site refuses access to its robots.txt (HTTP 401); not fetched"),
                (FakeHTTP(500), evidence.INACCESSIBLE, "robots.txt could not be checked (HTTP 500); not fetched"),
                (FakeHTTP(chunks=[b"x" * 600 * 1024], content_type="text/plain"), evidence.INACCESSIBLE,
                 "robots.txt could not be checked: the page is too large")):
            with self.subTest(note=note):
                self.refused(status, note, responses=ok, robots={"shop.example.com": response})
        allowed = FakeHTTP(body=b"User-agent: *\nDisallow: /private\n", content_type="text/plain")
        (_url, found), pages, _r, _c = self.fetch(responses=ok, robots={"shop.example.com": allowed})
        self.assertEqual(len(pages), 1)

    def test_a_disallowed_page_is_never_requested_and_robots_is_read_once_per_host(self):
        robots = {"shop.example.com": FakeHTTP(body=b"User-agent: *\nDisallow: /p", content_type="text/plain")}
        with self.assertRaises(Unverifiable):
            _result, pages, _r, calls = self.fetch(robots=robots)
        cache = {}
        self.fetch("https://shop.example.com/a", responses=[FakeHTTP(body=page().encode())], cache=cache)
        _res, _p, _r, calls = self.fetch("https://shop.example.com/b", responses=[FakeHTTP(body=page().encode())],
                                         cache=cache)
        self.assertEqual([c[3] for c in calls], ["/b"])  # robots.txt came from this job's cache

    def test_redirects_are_checked_against_the_new_hosts_robots_txt(self):
        hop = FakeHTTP(302, location="https://other.example.org/p")
        self.refused(evidence.DISALLOWED, "robots.txt does not allow checking this page; not fetched",
                     responses=[hop], robots={"other.example.org": FakeHTTP(body=b"User-agent: *\nDisallow: /",
                                                                            content_type="text/plain")})

    def test_unsafe_addresses_are_never_requested(self):
        for url, note in (("http://shop.example.com/", "not an https URL"),
                          ("https://shop.example.com:8443/", "a port other than 443"),
                          ("https://user:pw@shop.example.com/", "the URL carries a user name or password"),
                          ("https://127.0.0.1/", "an IP address instead of a site name"),
                          ("https://[::1]/", "an IP address instead of a site name"),
                          ("https://localhost/", "not a public host name"),
                          ("https://shop.example.com:99999/", "not a valid URL")):
            with self.subTest(url=url):
                self.refused(evidence.REFUSED, note, url=url)

    def test_names_resolving_to_private_or_reserved_addresses_are_refused(self):
        for addresses in (["127.0.0.1"], ["10.0.0.5"], ["192.168.1.1"], ["169.254.169.254"], ["100.64.0.1"],
                          ["::1"], ["::ffff:127.0.0.1"], ["fc00::1"], ["fe80::1%eth0"], ["0.0.0.0"],
                          ["2002:a00:1::1"], ["2001:0:4136:e378:8000:63bf:f5ff:fffe"],  # 6to4 / Teredo: 10.0.0.1
                          ["93.184.215.14", "10.0.0.5"]):  # one private answer among public ones is enough
            with self.subTest(addresses=addresses):
                self.refused(evidence.REFUSED, "the site resolves to a private or reserved address",
                             dns={"shop.example.com": addresses}, responses=[FakeHTTP()])
        with mock.patch("parts.evidence.socket.getaddrinfo", side_effect=socket.gaierror("nope")), \
                self.assertRaisesMessage(Unverifiable, "does not resolve"):
            public_address("missing.example.com")

    def test_redirects_are_checked_again_and_bounded(self):
        internal = FakeHTTP(302, location="https://intranet.example.com/admin")
        self.refused(evidence.REFUSED, "the site resolves to a private or reserved address", responses=[internal],
                     dns={"intranet.example.com": ["10.1.2.3"]})
        downgrade = FakeHTTP(301, location="http://shop.example.com/")
        self.refused(evidence.REFUSED, "not an https URL", responses=[downgrade])
        loop = FakeHTTP(302, location="/again")
        self.refused(evidence.INACCESSIBLE, "too many redirects", responses=[loop] * 5)
        (url, _page), pages, _r, _c = self.fetch(responses=[FakeHTTP(301, location="https://www.other.example.org/q"),
                                                            FakeHTTP(body=page().encode())])
        self.assertEqual((url, pages[1][1]["server_hostname"]), ("https://www.other.example.org/q",
                                                                  "www.other.example.org"))

    def test_status_type_size_and_time_limits(self):
        for response, status, note in (
                (FakeHTTP(403), evidence.BLOCKED, "the site refused the request (HTTP 403)"),
                (FakeHTTP(429), evidence.BLOCKED, "the site refused the request (HTTP 429)"),
                (FakeHTTP(404), evidence.INACCESSIBLE, "the page does not exist (HTTP 404)"),
                (FakeHTTP(500), evidence.INACCESSIBLE, "the page could not be loaded (HTTP 500)"),
                (FakeHTTP(content_type="application/pdf"), evidence.UNREADABLE, "not an HTML or text page"),
                (FakeHTTP(chunks=[b"x" * 1024 * 1024] * 3), evidence.INACCESSIBLE, "the page is too large")):
            with self.subTest(note=note):
                self.refused(status, note, responses=[response])
        slow = mock.Mock()
        slow.urlopen.side_effect = urllib3.exceptions.ReadTimeoutError(None, "/", "slow")
        with mock.patch("parts.evidence.socket.getaddrinfo", return_value=addrinfo("93.184.215.14")), \
                mock.patch("parts.evidence.urllib3.HTTPSConnectionPool", return_value=slow), \
                self.assertRaisesMessage(Unverifiable, "ReadTimeoutError"):  # robots.txt cannot be read: no page
            fetch_page("https://shop.example.com/", time.monotonic() + 60)
        self.refused(evidence.NOT_CHECKED, "time limit for checking pages reached", deadline=-1,
                     responses=[FakeHTTP()])


class PageCheckTests(SimpleTestCase):
    def check(self, html, claimed="0.400", measurement="shipping"):
        return check_page(parse_page(html), "90915YZZE1", "TOYOTA", D(claimed), measurement)

    def test_verified_only_when_the_page_shows_part_manufacturer_and_weight(self):
        self.assertEqual(self.check(page())[:3], (evidence.VERIFIED, D("0.400"), "shipping"))
        self.assertEqual(self.check(page("Net weight 0.40 kg"))[:3], (evidence.VERIFIED, D("0.400"), "net"))
        self.assertEqual(self.check(page("Weight: 400 g"))[:3], (evidence.VERIFIED, D("0.400"), ""))
        self.assertEqual(self.check(page("Weight 0.88 lb"))[:2], (evidence.VERIFIED, D("0.399")))
        self.assertEqual(self.check(page(number="90915 yzze1"))[0], evidence.VERIFIED)
        self.assertEqual(self.check(page("\u10ec\u10dd\u10dc\u10d0: 0.40 kg"))[0], evidence.VERIFIED)  # Georgian

    def test_false_citations_are_not_verified(self):
        for text, outcome, note in (
                (page(number="90915-YZZE12"), evidence.NOT_CONFIRMED, "the page does not show this part number"),
                (page(number="X90915-YZZE1"), evidence.NOT_CONFIRMED, "the page does not show this part number"),
                (page(brand="Denso"), evidence.NOT_CONFIRMED, "the page does not name the manufacturer"),
                (page("Ships fast"), evidence.NOT_CONFIRMED, "the page states no weight"),
                (page("Shipping weight: 0.80 kg"), evidence.CONTRADICTED, "the page states 0.800 kg, not the claimed"),
                ("<p>Loading…</p>", evidence.UNREADABLE, "no readable text on the page")):
            with self.subTest(note=note):
                got = self.check(text)
                self.assertEqual((got[0], got[1]), (outcome, None))
                self.assertIn(note, got[3])

    def test_several_weights_need_the_claimed_one_labelled(self):
        both = page("Net weight 0.35 kg. Shipping weight 0.40 kg")
        self.assertEqual(self.check(both)[:3], (evidence.VERIFIED, D("0.400"), "shipping"))
        self.assertEqual(self.check(both, "0.35", "net")[:3], (evidence.VERIFIED, D("0.350"), "net"))
        unlabelled = page("Weight 0.35 kg; weight 0.40 kg")
        self.assertEqual(self.check(unlabelled)[0], evidence.AMBIGUOUS)
        listing = page(" ".join(f"Weight {n} kg" for n in ("0.4", "1.2", "2.5", "3.1", "4.9")))
        self.assertEqual(self.check(listing)[0], evidence.AMBIGUOUS)
        self.assertIn("5 different weights", self.check(listing)[3])

    def test_page_text_ignores_scripts_and_styles(self):
        html = "<p>Weight <b>0.4</b> kg</p><script>weight 9 kg</script><style>p{}</style><noscript>x</noscript>"
        self.assertEqual(page_text(html), "Weight 0.4 kg")
        js_only = f"<html><body><div id=root></div><script>render({json.dumps(page())})</script></body></html>"
        self.assertEqual(self.check(js_only)[0], evidence.UNREADABLE)

    def test_the_weight_must_belong_to_the_same_product_record(self):
        cases = {
            "a spec table under the product's heading": (evidence.VERIFIED, f"<h1>Toyota oil filter 90915-YZZE1</h1>"
                f"<table><tr><th>Brand</th><td>Toyota</td></tr><tr><th>Shipping weight</th><td>0.40 kg</td></tr>"
                f"</table><p>{FILLER}</p>"),
            "a spec list with the labelled part number": (evidence.VERIFIED, "<h1>Toyota oil filter</h1><ul>"
                "<li>Part number: 90915-YZZE1</li><li>Brand: Toyota</li><li>Shipping weight: 0.40 kg</li></ul>"
                f"{FILLER}"),
            "its own card on a listing": (evidence.VERIFIED, "<h1>Oil filters</h1><ul><li>Toyota 90915-YZZE1. "
                "Shipping weight 0.40 kg. In stock</li><li>Bosch 0451103316. Shipping weight 0.60 kg.</li>"
                f"</ul><p>{FILLER}</p>"),
            "a schema.org Product": (evidence.VERIFIED, "<div itemscope itemtype='https://schema.org/Product'>Toyota "
                "<b>90915-YZZE1</b><div itemprop=weight itemscope itemtype='https://schema.org/QuantitativeValue'>"
                "Shipping weight 0.40 kg</div></div><div itemscope itemtype='https://schema.org/Product'>Bosch "
                f"0451103316</div><p>{FILLER}</p>"),
            "another card's weight on a listing": (evidence.AMBIGUOUS, "<h1>Oil filters</h1><ul><li>Toyota "
                "90915-YZZE1 oil filter, in stock, 12.50 GEL</li><li>Bosch 0451103316 oil filter. Shipping weight "
                f"0.40 kg. In stock</li></ul><p>{FILLER}</p>"),
            "another row's weight in a category table": (evidence.AMBIGUOUS, "<h1>Filters</h1><table><tr><td>Toyota"
                "</td><td>90915-YZZE1</td><td>12 GEL</td></tr><tr><td>Bosch</td><td>0451103316</td><td>Weight 0.40 kg"
                f"</td></tr></table><p>{FILLER}</p>"),
            "the manufacturer only in another product's card": (evidence.AMBIGUOUS, "<h1>Filters</h1><p>Part "
                "90915-YZZE1. Shipping weight 0.40 kg</p><ul><li>Toyota 04152-YZZA1 air filter, in stock, 9 GEL, "
                f"delivery tomorrow</li><li>Denso 123, in stock</li></ul><p>{FILLER}</p>"),
            "a page with two product headings": (evidence.AMBIGUOUS, "<h1>Toyota 90915-YZZE1</h1><h1>Bosch "
                f"0451103316</h1><p>Shipping weight 0.40 kg</p><p>{FILLER}</p>"),
        }
        for name, (outcome, html) in cases.items():
            with self.subTest(name):
                self.assertEqual(self.check(html)[0], outcome, self.check(html)[3])
        plain = check_page(f"Toyota 90915-YZZE1 Shipping weight 0.40 kg {FILLER}", "90915YZZE1", "TOYOTA", D("0.4"),
                           "shipping")
        self.assertEqual(plain[0], evidence.AMBIGUOUS)
        self.assertIn("a plain-text page: the weight cannot be tied to the part", plain[3])


# --- scoring and runs -------------------------------------------------------------------------------------------------

class EvidenceTests(ResearchTestCase):
    def setUp(self):
        super().setUp()
        self.search(self.nino_client)
        self.p = self.part()

    def test_claims_checked_before_any_page_is_fetched(self):
        cases = [(obs(), D("0.400"), "claim valid"), (obs(part_number="90915 yzze1"), D("0.400"), "claim valid"),
                 (obs(part_number="90915-YZZE2"), None, "names a different part number"),
                 (obs(manufacturer="Denso"), None, "names a different or no manufacturer"),
                 (obs(manufacturer=""), None, "names a different or no manufacturer"),
                 (obs(url="http://shop-a.example/p"), None, "not an https URL on a public site"),
                 (obs(measurement=""), D("0.400"), "claim valid"),
                 (obs(measurement="volumetric"), None, "measurement is not net, shipping or unspecified weight"),
                 (obs(unit="stone"), None, "weight or unit not understood"),
                 (obs(value="about 1"), None, "weight or unit not understood"),
                 (obs(value="900"), None, "outside 0.01 to 500 kg"),
                 (obs(value="400", unit="g"), D("0.400"), "claim valid"), (obs(value="0,4"), D("0.400"), "claim valid"),
                 (obs(value="2", unit="lbs"), D("0.907"), "claim valid")]
        for observation, weight, reason in cases:
            with self.subTest(observation=observation):
                got = check(self.p, Observation(**observation))
                self.assertEqual((got[0], got[3]), (weight, reason))

    def test_score_is_explained_and_not_a_probability(self):
        std = "standard"
        proposal, value, agreeing, disagreeing, lines = score({"a.com": (D("0.40"), std), "b.com": (D("0.40"), std)})
        self.assertEqual((proposal, value, agreeing, disagreeing), (D("0.40"), D("0.60"), {"a.com", "b.com"}, set()))
        text = "\n".join(lines)
        self.assertIn("0.50 for 2 agreeing verified sites", text)
        self.assertIn("+0.10: the agreeing sites are within 2 % of each other.", text)
        self.assertIn("evidence score 0.60 (moderate)", text)
        # three agree, one verified site disagrees: 0.50 + 0.10 + 0.10 - 0.20 = 0.50, below 0.60: needs review
        proposal, value, _a, disagreeing, lines = score({"a.com": (D("0.40"), std), "b.com": (D("0.40"), std),
                                                         "c.com": (D("0.40"), std), "d.com": (D("0.80"), std)})
        self.assertEqual((proposal, value, disagreeing), (None, D("0.50"), {"d.com"}))
        self.assertIn("Conflicting evidence: d.com shows 0.80 kg.", "\n".join(lines))
        self.assertIn("below 0.60: no weight proposed; needs review", "\n".join(lines))
        self.assertIsNone(score({"a.com": (D("0.40"), std)})[0])
        self.assertIsNone(score({"a.com": (D("0.40"), std), "b.com": (D("0.90"), std)})[0])

    def test_credibility_and_measurement_change_the_score(self):
        sites = {"toyota.com": (D("0.40"), "manufacturer"), "b.com": (D("0.40"), "standard")}
        self.assertEqual(score(sites)[1], D("0.70"))  # +0.10, the manufacturer's site agrees
        self.assertEqual(score(sites, "")[1], D("0.60"))  # -0.10, net or shipping not stated
        self.assertEqual(score(sites, "shipping", misquoted=2)[1], D("0.60"))  # -0.05 per misquoted page
        market = {"ebay.com": (D("0.40"), "marketplace"), "b.com": (D("0.40"), "standard")}
        proposal, _v, _a, _d, lines = score(market)
        self.assertIsNone(proposal)  # a marketplace listing is shown, never counted
        self.assertIn("Marketplace listings shown but not counted: ebay.com.", lines)

    def test_manufacturer_credibility_only_from_the_reviewed_allowlist(self):
        self.assertEqual(credibility("toyota.com", self.p, rules()), "standard")  # looking like the brand is not enough
        with override_settings(AI_WEIGHT_RESEARCH_RULES={"manufacturer_sites": {"Toyota": ["toyota.com"]},
                                                         "trusted_sites": ["rockauto.com"]}):
            r = rules()
            self.assertEqual((credibility("toyota.com", self.p, r), credibility("toyota-parts.com", self.p, r),
                              credibility("rockauto.com", self.p, r), credibility("ebay.co.uk", self.p, r)),
                             ("manufacturer", "standard", "trusted", "marketplace"))
            denso = PartMaster(part_number="X", brand="Denso", part_number_key="X", brand_key="DENSO")
            self.assertEqual(credibility("toyota.com", denso, r), "standard")  # Toyota's site, not Denso's
        for bad in ({"manufacturer_sites": {"Toyota": ["www.toyota.com"]}}, {"trusted_sites": ["co.uk"]}):
            with self.subTest(bad=bad), override_settings(AI_WEIGHT_RESEARCH_RULES=bad), \
                    self.assertRaisesMessage(ValueError, "is not a registrable domain"):
                rules()

    def test_rules_are_configurable(self):
        self.assertEqual(rules(), Rules())
        with override_settings(AI_WEIGHT_RESEARCH_RULES={"min_sources": "3", "min_score": "0.4",
                                                         "trusted_sites": ["rockauto.com"]}):
            r = rules()
            self.assertEqual((r.min_sources, r.min_score, r.trusted_sites), (3, D("0.4"), ("rockauto.com",)))
            two = {"a.com": (D("0.40"), "standard"), "b.com": (D("0.40"), "standard")}
            self.assertIsNone(score(two, r=r)[0])
        with override_settings(AI_WEIGHT_RESEARCH_RULES={"min_soruces": 1}), self.assertRaisesMessage(
                ValueError, "min_soruces"):
            rules()

    def test_one_site_saying_it_twice_is_one_source(self):
        self.web.update({"https://shop-a.example/other": page(), "https://www.shop-a.example/p": page(),
                         "https://shop-b.example/r": ("https://shop-a.example/final", page())})  # redirected
        with self.service(obs(), obs("https://shop-a.example/other"), obs("https://www.shop-a.example/p"),
                          obs("https://shop-b.example/r")):
            self.assertEqual(run_job(self.job().pk), "no_result")
        self.assertIn("At least 2 verified independent sites must agree", self.job().explanation)
        self.assertEqual({s.domain for s in self.sources().values()}, {"shop-a.example"})
        self.assertFalse(PartWeight.objects.filter(source="ai").exists())

    def test_the_same_page_cited_twice_is_fetched_once(self):
        with self.service(obs(), obs(), obs("https://www.shop-b.example/x", "400", "g")):
            run_job(self.job().pk)
        self.assertEqual(self.fetched.call_count, 2)


class RunTests(ResearchTestCase):
    def setUp(self):
        super().setUp()
        self.search(self.nino_client)

    def test_a_proposal_is_pending_with_its_evidence_and_prices_nothing(self):
        bad = obs("https://shop-d.example/z", part_number="OTHER")
        script = obs("javascript:alert(1)")
        with self.service(*AGREEING, bad, script):
            self.assertEqual(run_due_jobs(), {"ran": 1, "proposed": 1, "budget_reached": False})
        job = self.job()
        weight = job.proposed_weight
        self.assertEqual((job.status, job.measurement, job.cost_usd, job.attempts, job.provider, job.conflicting),
                         ("done", "shipping", D("0.04"), 1, "http", False))
        self.assertEqual((weight.source, weight.status, weight.weight_kg), ("ai", "pending", D("0.40")))
        self.assertEqual(job.confidence, D("0.70"))  # 3 verified sites (0.60), within 2 % of each other (+0.10)
        for text in ("https://shop-a.example/p (0.400 kg on the page)", "evidence score 0.70 (moderate)",
                     "checked on their pages: 2 not checked, 3 verified.", "not a probability"):
            self.assertIn(text, weight.evidence)
        self.assertNotIn("shop-d", weight.evidence.split("Verified sources:")[1])
        sources = self.sources()
        self.assertEqual(len(sources), 5)
        self.assertEqual(sources["javascript:alert(1)"].reason, "not an https URL on a public site")
        self.assertEqual((sources["https://shop-d.example/z"].accepted, sources["https://shop-d.example/z"].reason),
                         (False, "names a different part number"))
        shop_c = sources["https://parts.shop-c.example/y"]
        self.assertEqual((shop_c.verification, shop_c.agrees, shop_c.weight_kg, shop_c.domain, shop_c.measurement),
                         ("verified", True, D("0.400"), "shop-c.example", "shipping"))
        self.assertIn("Package weight: 14.1 oz", shop_c.page_excerpt)
        self.assertEqual(list(weight.events.values_list("action", flat=True)), ["recorded"])
        # No AI result prices or notifies anybody: the offer is still `checking`, the task still waits.
        offer = self.search(self.nino_client)["offers"][0]
        self.assertEqual((offer["status"], offer["weight_kg"]), ("checking", None))
        self.assertEqual(WeightTask.objects.get().status, "awaiting_weight")
        self.assertFalse(PartQuoteRequest.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_false_citations_and_invented_pages_never_count(self):
        self.web = {"https://shop-a.example/p": page(number="12345-ABCDE"),  # the page is about another part
                    "https://www.shop-b.example/x": page("Shipping weight 0.80 kg"),  # a different weight
                    "https://js.example/app": "Loading…",  # JavaScript-only
                    "https://blocked.example/p": Unverifiable(evidence.BLOCKED, "the site refused the request"),
                    "https://robots.example/p": Unverifiable(evidence.DISALLOWED, "robots.txt does not allow it"),
                    "https://listing.example/c": "<h1>Oil filters</h1><ul><li>Toyota 90915-YZZE1, in stock, 12 GEL"
                                                 "</li><li>Bosch 0451103316. Shipping weight 0.40 kg</li></ul>"
                                                 f"<p>{FILLER}</p>"}  # the weight is another product's
        with self.service(obs(), obs("https://www.shop-b.example/x"), obs("https://js.example/app"),
                          obs("https://blocked.example/p"), obs("https://invented.example/p"),
                          obs("https://robots.example/p"), obs("https://listing.example/c")):
            self.assertEqual(run_job(self.job().pk), "no_result")
        job = self.job()
        outcomes = {url: s.verification for url, s in self.sources().items()}
        self.assertEqual(outcomes, {"https://shop-a.example/p": "not_confirmed",
                                    "https://www.shop-b.example/x": "contradicted",
                                    "https://js.example/app": "unreadable", "https://blocked.example/p": "blocked",
                                    "https://invented.example/p": "inaccessible",
                                    "https://robots.example/p": "disallowed",
                                    "https://listing.example/c": "ambiguous"})
        self.assertFalse(WeightResearchSource.objects.filter(accepted=True).exists())
        self.assertTrue(job.conflicting)  # a misquoted page is shown as conflicting evidence
        self.assertIn("No observation was verified on its page: needs review.", job.explanation)
        with translation.override("en"):
            self.assertEqual(job.get_status_display(), "needs review: no reliable evidence")
        self.assertFalse(PartWeight.objects.filter(source="ai").exists())

    def test_conflicting_verified_evidence_is_visible_and_lowers_the_score(self):
        self.web.update({"https://d.example/p": page(), "https://e.example/p": page("Shipping weight 0.80 kg")})
        with self.service(*AGREEING, obs("https://d.example/p"), obs("https://e.example/p", "0.80")):
            self.assertEqual(run_job(self.job().pk), "done")
        job = self.job()
        self.assertTrue(job.conflicting)
        self.assertEqual(job.confidence, D("0.60"))  # 4 agree: 0.50 + 0.20 + 0.10 - 0.20 for e.example
        self.assertIn("Conflicting evidence: e.example shows 0.800 kg.", job.explanation)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get("/admin/parts/weighttask/", headers=EN), "conflicting evidence")

    def test_net_and_shipping_weights_are_never_mixed(self):
        self.web = {"https://a.example/p": page("Net weight 0.35 kg"), "https://b.example/p": page("Net weight 350 g"),
                    "https://c.example/p": page("Shipping weight 0.40 kg")}
        with self.service(obs("https://a.example/p", "0.35", measurement="net"),
                          obs("https://b.example/p", "350", "g", measurement="shipping"),  # the page says net
                          obs("https://c.example/p")):
            self.assertEqual(run_job(self.job().pk), "done")
        job = self.job()
        self.assertEqual((job.measurement, job.proposed_weight.weight_kg), ("net", D("0.35")))
        self.assertIn("Also verified, not proposed: shipping weight.", job.explanation)

    def test_a_stronger_weight_is_never_replaced(self):
        with self.service(*AGREEING):
            run_due_jobs()
        operator = record_weight(self.part(), D("0.55"), PartWeight.SOURCE_OPERATOR, status="approved",
                                 actor=self.staff)
        with self.amt(), self.captureOnCommitCallbacks(execute=True):
            approve_and_price(self.job().pk, self.staff)
        self.assertEqual(self.part().current_weight, operator)
        self.assertEqual(PartQuoteRequest.objects.get().prepared_weight_kg, D("0.55"))

    def test_a_weight_known_before_the_run_skips_the_search(self):
        record_weight(self.part(), D("0.55"), PartWeight.SOURCE_OPERATOR, status="approved", actor=self.staff)
        with self.service(*AGREEING) as service:
            self.assertEqual(run_job(self.job().pk), "skipped")
        service.assert_not_called()
        self.assertEqual((self.job().cost_usd, self.spent()), (0, 0))

    def test_a_weight_approved_during_the_search_wins(self):
        def meanwhile(query):
            record_weight(self.part(), D("0.55"), PartWeight.SOURCE_OPERATOR, status="approved", actor=self.staff)
            return answer(*AGREEING)
        with mock.patch("parts.weight_research.call_research_service", side_effect=meanwhile):
            self.assertEqual(run_job(self.job().pk), "skipped")
        self.assertFalse(PartWeight.objects.filter(source="ai").exists())
        self.assertEqual(WeightResearchSource.objects.count(), 3)  # the evidence is kept


class ApprovalTests(ResearchTestCase):
    URL = "/admin/parts/weightresearchjob/"

    def setUp(self):
        super().setUp()
        self.search(self.nino_client)
        with self.service(*AGREEING, obs("javascript:alert(1)")):
            run_due_jobs()

    def price_ready(self):
        return Notification.objects.filter(customer=self.nino, event_type="price_ready").count()

    def test_approve_only_prices_and_notifies_nobody(self):
        with self.captureOnCommitCallbacks(execute=True):
            approve_proposal(self.job().pk, self.staff)
        weight = self.job().proposed_weight
        self.assertEqual((weight.status, weight.reviewed_by), ("approved", self.staff))
        self.assertEqual(list(weight.events.values_list("action", flat=True)), ["recorded", "approved"])
        self.assertEqual(WeightTask.objects.get().status, "awaiting_weight")
        self.assertFalse(PartQuoteRequest.objects.exists())
        self.assertEqual(self.price_ready(), 0)
        with self.assertRaisesMessage(OperatorError, "already approved; nothing changed"), translation.override("en"):
            approve_proposal(self.job().pk, self.staff)
        # the existing queue workflow then prices the waiting customers with the approved weight
        with self.amt(), self.captureOnCommitCallbacks(execute=True):
            approve_task(WeightTask.objects.get().pk, self.staff)
        self.assertEqual((PartQuoteRequest.objects.get().prepared_weight_kg, self.price_ready()), (D("0.40"), 1))

    def test_approve_and_price_prices_the_waiting_customers_once(self):
        with self.amt(), self.captureOnCommitCallbacks(execute=True), translation.override("en"):
            task = approve_and_price(self.job().pk, self.staff)
        self.assertEqual(task.status, "resolved")
        quote = PartQuoteRequest.objects.get(customer=self.nino)
        self.assertEqual((quote.prepared_weight_kg, quote.status, self.price_ready()), (D("0.40"), "resolved", 1))
        offer = self.search(self.nino_client)["offers"][0]
        self.assertEqual((offer["status"], offer["weight_kg"], offer["weight_source"]), ("ready", "0.40", "operator"))
        for again in (approve_and_price, approve_proposal):
            with self.assertRaisesMessage(OperatorError, "already approved; nothing changed"), \
                    translation.override("en"):
                again(self.job().pk, self.staff)
        self.assertEqual(Notification.objects.filter(event_type="price_ready").count(), 1)

    def test_admin_pricing_needs_explicit_confirmation(self):
        self.client.force_login(self.staff)
        data = {"action": "approve_and_price", "_selected_action": [self.job().pk]}
        response = self.client.post(self.URL, data, headers=EN)
        self.assertContains(response, "price 1 waiting customer(s)")
        self.assertContains(response, "notified once")
        self.client.post(self.URL, {**data, "apply": "1"}, headers=EN)  # submitted without ticking the box
        self.assertEqual(self.job().proposed_weight.status, "pending")
        with self.amt(), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.URL, {**data, "apply": "1", "confirm": "on"}, follow=True, headers=EN)
        self.assertContains(response, "1 customer(s) priced and notified")
        self.assertEqual((WeightTask.objects.get().status, self.price_ready()), ("resolved", 1))

    def test_admin_approve_only(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.URL, {"action": "approve_only", "_selected_action": [self.job().pk]},
                                    follow=True, headers=EN)
        self.assertContains(response, "1 waiting customer(s) are not priced yet")
        self.assertEqual((self.job().proposed_weight.status, WeightTask.objects.get().status, self.price_ready()),
                         ("approved", "awaiting_weight", 0))

    def test_actions_need_their_permissions(self):
        clerk = get_user_model().objects.create_user("clerk", "clerk@example.com", "x", is_staff=True)
        clerk.user_permissions.add(*Permission.objects.filter(codename__in=["view_weightresearchjob",
                                                                            "change_partweight"]))
        self.client.force_login(clerk)
        listing = self.client.get(self.URL, headers=EN)
        self.assertContains(listing, 'value="approve_only"')
        self.assertNotContains(listing, 'value="approve_and_price"')
        self.assertNotContains(listing, 'value="retry"')
        self.client.post(self.URL, {"action": "approve_and_price", "_selected_action": [self.job().pk], "apply": "1",
                                    "confirm": "on"})
        self.assertEqual((self.job().proposed_weight.status, WeightTask.objects.get().status),
                         ("pending", "awaiting_weight"))

    def test_reject_and_research_again_in_the_admin(self):
        client = self.client
        client.force_login(self.staff)
        job = self.job()
        for url in (self.URL, f"{self.URL}{job.pk}/change/", "/admin/parts/weighttask/",
                    "/admin/parts/weightresearchspend/"):
            self.assertEqual(client.get(url).status_code, 200, url)
        self.assertContains(client.get("/admin/parts/weighttask/", headers=EN), "0.40 kg · evidence score 0.70")
        change = client.get(f"{self.URL}{job.pk}/change/", headers=EN)
        for text in ("shop-c.example", "verified on the page", "Package weight: 14.1 oz", "not a probability"):
            self.assertContains(change, text)
        self.assertContains(change, "javascript:alert(1)")  # shown as text ...
        self.assertNotContains(change, 'href="javascript:')  # ... never as a link
        client.post(self.URL, {"action": "reject", "_selected_action": [job.pk]})
        self.assertEqual(self.job().proposed_weight.status, "rejected")
        for approve in (approve_proposal, approve_and_price):
            with self.assertRaisesMessage(OperatorError, "rejected"), translation.override("en"):
                approve(job.pk, self.staff)
        client.post(self.URL, {"action": "retry", "_selected_action": [job.pk]})
        self.assertEqual(WeightResearchJob.objects.filter(status="queued").count(), 1)
        client.post(self.URL, {"action": "retry", "_selected_action": [job.pk]})  # already queued: no second job
        self.assertEqual(WeightResearchJob.objects.count(), 2)
        self.assertEqual(client.post(f"{self.URL}{job.pk}/delete/").status_code, 403)


class LimitTests(ResearchTestCase):
    def setUp(self):
        super().setUp()
        self.search(self.nino_client)

    def other_job(self, number):
        return WeightResearchJob.objects.create(part=PartMaster.objects.create(
            part_number=number, brand="Bosch", part_number_key=number, brand_key="BOSCH"))

    def test_bounded_retries_with_backoff_each_counted_at_the_attempt_limit(self):
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
        self.assertEqual((self.job().attempts, self.job().cost_usd, self.spent()), (3, D("1.50"), D("1.50")))
        output = "\n".join(logs.output)
        self.assertNotIn("test-key-not-real", output)
        self.assertNotIn(PART, output)

    @override_settings(AI_WEIGHT_RESEARCH_MAX_COST_PER_JOB_USD=D("1.00"), AI_WEIGHT_RESEARCH_MAX_ATTEMPTS=5)
    def test_the_job_limit_stops_retries_before_a_paid_call(self):
        now = timezone.now()
        with self.service(error=ResearchError("HTTP 500")) as service, self.assertLogs("parts.weight_research"):
            run_job(self.job().pk, now)
            run_job(self.job().pk, now)
            self.assertEqual(run_job(self.job().pk, now), "failed")
        self.assertEqual(service.call_count, 2)
        job = self.job()
        self.assertEqual((job.attempts, job.cost_usd, job.last_error), (2, D("1.00"), "job cost limit reached"))

    def test_a_job_another_run_claimed_is_not_run_twice(self):
        WeightResearchJob.objects.update(status="running", started_at=timezone.now())
        with self.service(*AGREEING) as service:
            self.assertIsNone(run_job(self.job().pk))
            self.assertEqual(run_due_jobs()["ran"], 0)
        service.assert_not_called()

    def test_a_crashed_run_is_recovered_and_its_reservation_counted_once(self):
        now = timezone.now()
        with self.service(error=RuntimeError("worker killed")), self.assertRaises(RuntimeError):
            run_job(self.job().pk, now)
        self.assertEqual((self.job().status, self.job().cost_usd, self.spent()), ("running", D("0.50"), D("0.50")))
        later = now + timedelta(minutes=31)
        with self.service(*AGREEING) as service:
            self.assertEqual(run_due_jobs(now=later)["ran"], 0)  # requeued with a backoff, not run at once
        service.assert_not_called()
        job = self.job()
        self.assertEqual((job.status, job.last_error, job.cost_usd, self.spent()),
                         ("queued", "interrupted", D("0.50"), D("0.50")))
        with self.service(*AGREEING):
            self.assertEqual(run_due_jobs(now=job.next_attempt_at)["proposed"], 1)
        self.assertEqual((self.job().attempts, self.job().cost_usd, self.spent()), (2, D("0.54"), D("0.54")))

    def test_a_cost_over_the_attempt_limit_discards_the_answer(self):
        with self.service(*AGREEING, cost="0.75"):
            self.assertEqual(run_job(self.job().pk), "failed")
        self.assertEqual((self.job().last_error, self.job().cost_usd, self.spent()),
                         ("cost limit exceeded", D("0.75"), D("0.75")))
        self.assertFalse(PartWeight.objects.filter(source="ai").exists())

    def test_reported_costs_replace_the_reservation_unknown_ones_keep_it(self):
        with self.service(*AGREEING, cost=None):
            run_job(self.job().pk)
        self.assertEqual((self.job().cost_usd, self.spent()), (D("0.50"), D("0.50")))
        with self.service(*AGREEING, cost="0.04"):
            run_job(self.other_job("A1").pk)
        self.assertEqual(self.spent(), D("0.54"))

    @override_settings(AI_WEIGHT_RESEARCH_DAILY_BUDGET_USD=D("1.00"))
    def test_the_daily_budget_stops_the_run_before_a_paid_call(self):
        WeightResearchSpend.objects.create(day=timezone.localdate(), reserved_usd=D("0.60"))
        with self.service(*AGREEING) as service:
            self.assertEqual(run_due_jobs(), {"ran": 0, "proposed": 0, "budget_reached": True})
            out = StringIO()
            call_command("run_weight_research", stdout=out)
        service.assert_not_called()
        self.assertIn("daily budget reached", out.getvalue())
        self.assertEqual((self.job().status, self.job().attempts, self.spent()), ("queued", 0, D("0.60")))

    @override_settings(AI_WEIGHT_RESEARCH_DAILY_BUDGET_USD=D("1.00"))
    def test_an_attempt_still_running_elsewhere_holds_its_reservation(self):
        mine, busy, third = self.job(), self.other_job("A1"), self.other_job("B2")
        now = timezone.now()
        self.assertIsInstance(_claim(busy.pk, now), WeightResearchJob)  # another worker, mid-call: 0.50 reserved
        with self.service(*AGREEING) as service:
            self.assertEqual(run_job(mine.pk, now), "done")  # 0.50 + 0.50 fits; then settled to the reported 0.04
            self.assertEqual(self.spent(), D("0.54"))
            self.assertEqual(run_job(third.pk, now), BUDGET)  # 0.54 + 0.50 would exceed 1.00
        self.assertEqual(service.call_count, 1)


@skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL")
@override_settings(**{**ON, "AI_WEIGHT_RESEARCH_DAILY_BUDGET_USD": D("1.00")})
class ConcurrentWorkerTests(TransactionTestCase):
    """Overlapping cron runs: real concurrent transactions, never a paid call (only the claim runs)."""

    serialized_rollback = True  # keep the migrations' data for the test classes that run after this one

    def jobs(self, count):
        return [WeightResearchJob.objects.create(part=PartMaster.objects.create(
            part_number=f"P{i}", brand="Bosch", part_number_key=f"P{i}", brand_key="BOSCH")).pk for i in range(count)]

    def race(self, job_ids):
        barrier, results, now = threading.Barrier(len(job_ids)), [], timezone.now()

        def worker(job_id):
            try:
                barrier.wait()
                results.append(_claim(job_id, now))
            finally:
                connection.close()
        threads = [threading.Thread(target=worker, args=(job_id,)) for job_id in job_ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return results

    def test_parallel_workers_never_reserve_beyond_the_daily_budget(self):
        results = self.race(self.jobs(6))
        self.assertEqual(sum(isinstance(r, WeightResearchJob) for r in results), 2)
        self.assertEqual(results.count(BUDGET), 4)
        self.assertEqual(WeightResearchSpend.objects.get().reserved_usd, D("1.00"))
        self.assertEqual(WeightResearchJob.objects.filter(status="running").count(), 2)

    def test_parallel_workers_never_run_one_job_twice(self):
        job_id = self.jobs(1)[0]
        results = self.race([job_id] * 4)
        self.assertEqual(sum(isinstance(r, WeightResearchJob) for r in results), 1)
        self.assertEqual(results.count(None), 3)
        self.assertEqual((WeightResearchJob.objects.get().attempts, WeightResearchSpend.objects.get().reserved_usd),
                         (1, D("0.50")))
