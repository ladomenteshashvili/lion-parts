"""AI part-weight research (LPK-247).

When an AMT part has no usable weight, a research job is queued (once per part, see COOLDOWN). The cron command
`run_weight_research` asks a research provider (PROVIDERS: Claude or OpenAI with web search, parts.ai_providers,
LPK-248; or a provider-neutral HTTPS service), then the configured fallbacks if it fails and the budget covers them,
where the part's weight is published. Every call is recorded (WeightResearchCall). Nothing a provider says is trusted:

1. each observation must name exactly this part number and manufacturer, a known unit and a plausible weight;
2. this server then fetches every cited page itself (parts.evidence, SSRF-safe) and checks that the page shows the part
   number, the manufacturer and the weight. Only such *verified* observations count;
3. verified sites are grouped by registrable domain (Public Suffix List) and by the measurement the page states
   (shipping, net or unspecified, never mixed), and scored with Rules, every step written down.

The result is a *pending* AI weight with its sources, evidence score and explanation, or "needs review" with the
reasons. It prices nothing and notifies nobody until an operator approves it, and it never replaces a stronger approved
weight (parts.registry.record_weight).

Only the part number, manufacturer and the supplier's part name leave this server: never a customer, a VIN, a search
log or a price. Logs name the job and an error type only."""

import json
import logging
import time
from dataclasses import dataclass, field, fields
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from statistics import median

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.module_loading import import_string

from accounts.validators import normalize_part_number

from . import evidence
from .evidence import Unverifiable, fetch_page, site, to_kg
from .models import (PartWeight, WeightResearchCall, WeightResearchJob, WeightResearchSource, WeightResearchSpend,
                     WeightTask, WeightTaskRequest, normalize_brand)
from .registry import is_plausible, record_weight

logger = logging.getLogger(__name__)

COOLDOWN = timedelta(days=30)  # one paid search per part per month, whatever its outcome
RETRY_BASE = timedelta(minutes=10)  # 10, 20, 40 ... minutes between attempts
STALE_RUNNING = timedelta(minutes=30)  # a run that died mid-job (crash, kill) gives the job back
MAX_OBSERVATIONS = 20
MAX_RESPONSE_BYTES = 512 * 1024
MEASUREMENTS = ("shipping", "net", "")  # preferred first: the courier fee is charged on the shipped weight
BUDGET = "budget"  # run_job's answer when the daily budget has no room for another attempt


@dataclass(frozen=True)
class Rules:
    """Evidence rules. Override any of them with settings.AI_WEIGHT_RESEARCH_RULES = {"min_sources": 3, ...}.
    The evidence score is a rule-based summary of the evidence, not a calibrated probability."""

    min_sources: int = 2  # verified independent sites that must agree
    min_score: Decimal = Decimal("0.60")  # below: no proposal, needs review
    agree_ratio: Decimal = Decimal("0.10")  # agreeing: within 10 % of the median ...
    agree_min_kg: Decimal = Decimal("0.05")  # ... or within 50 g
    tight_ratio: Decimal = Decimal("0.02")
    base: Decimal = Decimal("0.50")  # min_sources agreeing verified sites
    per_extra_site: Decimal = Decimal("0.10")
    max_extra: Decimal = Decimal("0.20")
    tight_bonus: Decimal = Decimal("0.10")  # agreeing sites within tight_ratio of each other
    trusted_bonus: Decimal = Decimal("0.10")  # the manufacturer's own site or a trusted catalog agrees
    unspecified_penalty: Decimal = Decimal("0.10")  # the pages do not say net or shipping weight
    disagree_penalty: Decimal = Decimal("0.20")  # per verified site showing a different weight
    misquote_penalty: Decimal = Decimal("0.05")  # per page the provider misquoted (it shows a different weight)
    cap: Decimal = Decimal("0.90")
    trusted_sites: tuple = ()  # registrable domains, e.g. ("rockauto.com",); an owner decision
    # Reviewed official manufacturer sites: ((brand key, registrable domain), ...), set in settings as
    # {"Toyota": ["toyota.com"]}. Empty by default: a domain that merely looks like a brand stays a standard source.
    manufacturer_sites: tuple = ()
    marketplaces: tuple = ("ebay", "amazon", "aliexpress", "alibaba", "temu", "wish", "etsy", "allegro", "avito")
    verify_seconds: int = 90  # all page checks of one job together


def rules():
    """The Rules in force: defaults plus settings.AI_WEIGHT_RESEARCH_RULES. Raises ValueError for an unknown key."""
    overrides = getattr(settings, "AI_WEIGHT_RESEARCH_RULES", None) or {}
    kinds = {f.name: type(f.default) for f in fields(Rules)}
    unknown = set(overrides) - set(kinds)
    if unknown:
        raise ValueError(f"Unknown AI_WEIGHT_RESEARCH_RULES: {', '.join(sorted(unknown))}")
    values = {key: Decimal(str(value)) if kinds[key] is Decimal else kinds[key](value)
              for key, value in overrides.items() if key not in ("trusted_sites", "manufacturer_sites")}
    if "trusted_sites" in overrides:
        values["trusted_sites"] = tuple(_domain(d) for d in overrides["trusted_sites"])
    if "manufacturer_sites" in overrides:
        values["manufacturer_sites"] = tuple(sorted(
            (normalize_brand(brand), _domain(d)) for brand, domains in dict(overrides["manufacturer_sites"]).items()
            for d in domains))
    return Rules(**values)


def _domain(value):
    """An allowlist entry must be exactly a registrable domain ("toyota.com", not "www.toyota.com" or "co.uk")."""
    domain = str(value).strip().lower()
    if not domain or evidence.registrable_domain(domain) != domain:
        raise ValueError(f"AI_WEIGHT_RESEARCH_RULES: {value!r} is not a registrable domain")
    return domain


class ResearchError(Exception):
    """The provider gave no answer (timeout, rate limit, HTTP error, missing key). `sent` False: no request left this
    server, so nothing can have been billed and the call's reservation is released."""

    def __init__(self, message, sent=True):
        super().__init__(message)
        self.sent = sent


@dataclass
class Observation:
    """What the provider says one page shows. Claims only: parts.evidence checks the page itself."""

    url: str
    title: str = ""
    part_number: str = ""
    manufacturer: str = ""
    value: str = ""
    unit: str = ""
    measurement: str = ""
    excerpt: str = ""


@dataclass
class Answer:
    """One provider call: its observations and what the audit records. `error` set: the call was answered (and may be
    billed) but is unusable (refused, cut off, malformed); the next provider in the chain is tried."""

    observations: list
    cost: Decimal | None = None  # USD, reported or estimated; None = unknown, the reservation stands
    model: str = ""
    request_id: str = ""
    usage: dict = field(default_factory=dict)  # as the provider reported it, for reconciliation
    searches: int | None = None
    citations: list = field(default_factory=list)
    unsourced: int = 0  # observations dropped because the search never returned their URL
    cost_note: str = ""
    error: str = ""


# --- research providers (the only code that talks to them) ----------------------------------------------------------

def research_query(part):
    """Everything a provider receives about a part. Nothing about any customer, search or price."""
    return {"part_number": part.part_number, "manufacturer": part.brand, "name": part.name,
            "max_cost_usd": str(settings.AI_WEIGHT_RESEARCH_MAX_COST_PER_ATTEMPT_USD),
            "min_sources": rules().min_sources}


def call_research_service(query):
    """The "http" provider: POST the query as JSON; the service answers {"cost_usd": "0.04", "observations": [{url,
    title, part_number, manufacturer, value, unit, measurement, excerpt}, ...]}. -> Answer. Raises ResearchError. Any
    other key (for example "verified": true) is ignored."""
    try:
        with requests.post(settings.AI_WEIGHT_RESEARCH_URL, json=query, stream=True, allow_redirects=False,
                           timeout=(10, settings.AI_WEIGHT_RESEARCH_TIMEOUT_SECONDS),
                           headers={"Authorization": f"Bearer {settings.AI_WEIGHT_RESEARCH_API_KEY}"}) as response:
            if response.status_code != 200:
                raise ResearchError(f"HTTP {response.status_code}")
            body = response.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ResearchError("response too large")
        data = json.loads(body)
    except ResearchError:
        raise
    except (requests.RequestException, ValueError) as error:
        raise ResearchError(type(error).__name__) from None
    if not isinstance(data, dict) or not isinstance(data.get("observations"), list):
        raise ResearchError("unexpected response shape")
    cost = _money(data.get("cost_usd"))
    return Answer(parse_observations(data["observations"]), cost,
                  cost_note="reported by the research service" if cost is not None else "cost not reported")


def parse_observations(items):
    """A provider's list of dicts -> at most MAX_OBSERVATIONS Observations, every field a bounded string."""
    return [Observation(**{key: _text(item.get(key), size) for key, size in (
        ("url", 1000), ("title", 300), ("part_number", 120), ("manufacturer", 120), ("value", 40), ("unit", 20),
        ("measurement", 20), ("excerpt", 1000))})
        for item in items[:MAX_OBSERVATIONS] if isinstance(item, dict) and isinstance(item.get("url"), str)]


# Each a function query -> Answer, raising ResearchError when nothing was answered. Dotted paths, imported at call time:
# a test's mock is the one called. config.settings.AI_RESEARCH_PROVIDERS lists the same names.
PROVIDERS = {"http": "parts.weight_research.call_research_service",
             "anthropic": "parts.ai_providers.anthropic_research",
             "openai": "parts.ai_providers.openai_research"}


def _text(value, size):
    return str(value).strip()[:size] if isinstance(value, (str, int, float)) else ""


def _money(value):
    """A reported cost, or None when missing or unreadable (the reservation then stands)."""
    try:
        cost = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return cost.quantize(Decimal("0.0001")) if cost.is_finite() and 0 <= cost < 10000 else None


# --- queueing ------------------------------------------------------------------------------------------------------

def enqueue_research(part):
    """Queue research for a part with no usable weight. -> the new job, or None (flag off, no manufacturer to check
    against, or already researched within COOLDOWN). Safe to call from concurrent searches."""
    if not settings.AI_WEIGHT_RESEARCH_ENABLED or not part.brand_key:
        return None
    if part.research_jobs.filter(created_at__gte=timezone.now() - COOLDOWN).exists():
        return None
    try:
        with transaction.atomic():
            return WeightResearchJob.objects.create(part=part)
    except IntegrityError:  # a concurrent search queued it first (one active job per part)
        return None


# --- running and spending --------------------------------------------------------------------------------------------

def run_due_jobs(limit=None, now=None):
    """Run up to `limit` due jobs, stopping at the daily budget. -> {"ran", "proposed", "budget_reached"}."""
    summary = {"ran": 0, "proposed": 0, "budget_reached": False}
    if not settings.AI_WEIGHT_RESEARCH_ENABLED:
        return summary
    now = now or timezone.now()
    _requeue_stale(now)
    limit = limit or settings.AI_WEIGHT_RESEARCH_JOBS_PER_RUN
    due = WeightResearchJob.objects.filter(status=WeightResearchJob.STATUS_QUEUED, next_attempt_at__lte=now)
    for job_id in list(due.order_by("next_attempt_at", "id").values_list("pk", flat=True)[:limit]):
        status = run_job(job_id, now)
        if status == BUDGET:
            summary["budget_reached"] = True
            break
        if status is not None:
            summary["ran"] += 1
            summary["proposed"] += status == WeightResearchJob.STATUS_DONE
    return summary


def _requeue_stale(now):
    """A job left running by a run that died: its attempt's reservation stands (it may have been billed); the attempt
    counts as failed and the job is queued again, or fails at the attempt limit."""
    stale = WeightResearchJob.objects.filter(status=WeightResearchJob.STATUS_RUNNING,
                                             started_at__lt=now - STALE_RUNNING)
    for job in stale:
        _failed_attempt(job, "interrupted", now)


def chain():
    """The providers one attempt may call, in order: the primary, then the fallbacks."""
    return [settings.AI_WEIGHT_RESEARCH_PROVIDER, *settings.AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS]


def _claim(job_id, now):
    """Reserve the attempt's calls and claim the job in one transaction, serialized on the day's budget row: overlapping
    runs never reserve beyond the daily budget nor run one job twice. Each call reserves the per-call limit; fallbacks
    are reserved (and so may run) only as far as the job's and the day's budgets still cover them. -> the running job
    (job.chain: the providers reserved), a final status (finished without a paid call), BUDGET, or None (claimed
    elsewhere)."""
    per_attempt = settings.AI_WEIGHT_RESEARCH_MAX_COST_PER_ATTEMPT_USD
    with transaction.atomic():
        spend = WeightResearchSpend.objects.get_or_create(day=timezone.localdate(now))[0]
        spend = WeightResearchSpend.objects.select_for_update().get(pk=spend.pk)
        job = (WeightResearchJob.objects.select_for_update(of=("self",)).select_related("part__current_weight")
               .filter(pk=job_id, status=WeightResearchJob.STATUS_QUEUED).first())
        if job is None:
            return None
        if job.part.current_weight is not None:
            return _finish(job, WeightResearchJob.STATUS_SKIPPED, now, explanation="The part already has an "
                           "approved weight; nothing was searched.")
        job_limit = settings.AI_WEIGHT_RESEARCH_MAX_COST_PER_JOB_USD
        if job.cost_usd + per_attempt > job_limit:
            return _finish(job, WeightResearchJob.STATUS_FAILED, now, error="job cost limit reached",
                           explanation=f"Another attempt could cost up to ${per_attempt}; this job has used "
                                       f"${job.cost_usd} of its ${job_limit}.")
        if spend.reserved_usd + per_attempt > settings.AI_WEIGHT_RESEARCH_DAILY_BUDGET_USD:
            return BUDGET
        calls = min(len(chain()), int((job_limit - job.cost_usd) // per_attempt),
                    int((settings.AI_WEIGHT_RESEARCH_DAILY_BUDGET_USD - spend.reserved_usd) // per_attempt))
        spend.reserved_usd += per_attempt * calls
        spend.save(update_fields=["reserved_usd", "updated_at"])
        job.status, job.started_at, job.attempts = WeightResearchJob.STATUS_RUNNING, now, job.attempts + 1
        job.cost_usd += per_attempt * calls
        job.chain = chain()[:calls]
        job.provider = job.chain[0]
        job.save(update_fields=["status", "started_at", "attempts", "cost_usd", "provider"])
    return job


def _settle(job, day, delta):
    """Correct the job's and the day's spending by `delta`: actual costs minus the reservations."""
    if delta:
        WeightResearchJob.objects.filter(pk=job.pk).update(cost_usd=F("cost_usd") + delta)
        WeightResearchSpend.objects.filter(day=day).update(reserved_usd=F("reserved_usd") + delta)


def run_job(job_id, now=None):
    """Claim one queued job and run it. -> its new status, BUDGET, or None when another run claimed it first."""
    now = now or timezone.now()
    job = _claim(job_id, now)
    if not isinstance(job, WeightResearchJob):
        return job
    limit = settings.AI_WEIGHT_RESEARCH_MAX_COST_PER_ATTEMPT_USD
    query, spent, used, errors = research_query(job.part), Decimal("0"), None, []
    for name in job.chain:
        try:
            answer = import_string(PROVIDERS[name])(query)
            outcome = "unusable" if answer.error else "answered"
        except ResearchError as error:
            outcome = "failed" if error.sent else "not_sent"
            # sent: it may still have been billed, so the reservation (the per-call limit) stands
            answer = Answer([], None if error.sent else Decimal("0"), error=str(error)[:200],
                            cost_note="" if error.sent else "not sent, not billed")
        except Exception as error:  # an answer no parser foresaw: one failed call, not a crashed cron run
            outcome, answer = "failed", Answer([], error=f"{name}: unexpected {type(error).__name__}")
        spent += limit if answer.cost is None else answer.cost
        _record_call(job, name, answer, outcome, limit, now)
        if not answer.error:
            used = (name, answer)
            break
        logger.warning("AI weight research job %s: provider %s failed: %s", job.pk, name, answer.error)
        errors.append(answer.error)
    _settle(job, timezone.localdate(now), spent - limit * len(job.chain))  # calls not needed give their share back
    if used is None:
        return _failed_attempt(job, "; ".join(errors)[:200], now)
    name, answer = used
    WeightResearchJob.objects.filter(pk=job.pk).update(provider=name)
    if answer.cost is not None and answer.cost > limit:
        return _finish(job, WeightResearchJob.STATUS_FAILED, now, error="cost limit exceeded",
                       explanation=f"{name} cost ${answer.cost}, over the ${limit} limit per call; its answer was not "
                                   "used.")
    return _assess(job, answer.observations, now, answer.unsourced)


def _record_call(job, name, answer, outcome, reserved, now):
    WeightResearchCall.objects.create(
        job=job, attempt=job.attempts, provider=name, model=answer.model[:100], request_id=answer.request_id[:200],
        outcome=outcome, error=answer.error[:200], searches=answer.searches, usage=answer.usage,
        citations=answer.citations, unsourced=answer.unsourced, reserved_usd=reserved, cost_usd=answer.cost, cost_note=answer.cost_note[:300],
        created_at=now)


def _failed_attempt(job, error, now):
    if job.attempts >= settings.AI_WEIGHT_RESEARCH_MAX_ATTEMPTS:
        return _finish(job, WeightResearchJob.STATUS_FAILED, now, error=error)
    WeightResearchJob.objects.filter(pk=job.pk).update(
        status=WeightResearchJob.STATUS_QUEUED, last_error=error,
        next_attempt_at=now + RETRY_BASE * 2 ** max(job.attempts - 1, 0))
    return WeightResearchJob.STATUS_QUEUED


def _finish(job, status, now, *, error="", explanation="", **values):
    WeightResearchJob.objects.filter(pk=job.pk).update(status=status, finished_at=now, last_error=error,
                                                       explanation=explanation, **values)
    return status


# --- checking the evidence -----------------------------------------------------------------------------------------

def check(part, observation):
    """What the provider claims, before any page is fetched. -> (weight in kg or None, claimed measurement, domain,
    reason). None means the observation does not count and its page is not fetched."""
    domain = site(observation.url)
    measurement = observation.measurement.strip().lower()
    measurement = "" if measurement in ("unspecified", "unknown") else measurement
    if not domain:
        return None, measurement, "", "not an https URL on a public site"
    if normalize_part_number(observation.part_number) != part.part_number_key:
        return None, measurement, domain, "names a different part number"
    if normalize_brand(observation.manufacturer) != part.brand_key:
        return None, measurement, domain, "names a different or no manufacturer"
    if measurement not in MEASUREMENTS:
        return None, measurement, domain, "measurement is not net, shipping or unspecified weight"
    weight = to_kg(observation.value, observation.unit)
    if weight is None:
        return None, measurement, domain, "weight or unit not understood"
    if not is_plausible(weight):
        return None, measurement, domain, "outside 0.01 to 500 kg"
    return weight, measurement, domain, "claim valid"


def credibility(domain, part, r):
    """Only reviewed allowlists raise a source's credibility; an unknown domain is a standard source."""
    if (part.brand_key, domain) in r.manufacturer_sites:
        return WeightResearchSource.CREDIBILITY_MANUFACTURER
    if domain in r.trusted_sites:
        return WeightResearchSource.CREDIBILITY_TRUSTED
    if domain.split(".")[0] in r.marketplaces:
        return WeightResearchSource.CREDIBILITY_MARKETPLACE
    return WeightResearchSource.CREDIBILITY_STANDARD


def _agrees(value, centre, r):
    difference = abs(value - centre)
    return difference <= r.agree_min_kg or difference <= centre * r.agree_ratio


def _n(number):
    return format(number.normalize(), "f")


def band(value):
    return "strong" if value >= Decimal("0.80") else "moderate" if value >= Decimal("0.60") else "weak"


def score(sites, measurement="shipping", r=None, misquoted=0):
    """sites: {domain: (kg, credibility)} verified on their pages, one measurement -> (proposal kg or None, evidence
    score, agreeing domains, disagreeing domains, explanation lines). Every point added or taken off is written down."""
    r = r or rules()
    counted = {d: kg for d, (kg, cred) in sites.items() if cred != WeightResearchSource.CREDIBILITY_MARKETPLACE}
    lines = [f"{measurement.capitalize()} weight:" if measurement else "Weight, net or shipping not stated:"]
    shown = sorted(set(sites) - set(counted))
    if shown:
        lines.append(f"Marketplace listings shown but not counted: {', '.join(shown)}.")
    if not counted:
        return None, Decimal("0"), set(), set(), lines + ["No verified independent site: needs review."]
    centre = Decimal(median(counted.values()))
    agreeing = {d for d, kg in counted.items() if _agrees(kg, centre, r)}
    disagreeing = set(counted) - agreeing
    lines.append(f"{len(counted)} independent site(s) verified; {len(agreeing)} agree within "
                 f"{_n(r.agree_ratio * 100)} % or {_n(r.agree_min_kg * 1000)} g of the median "
                 f"{centre.quantize(Decimal('0.001'))} kg.")
    if disagreeing:
        lines.append("Conflicting evidence: " + ", ".join(f"{d} shows {counted[d]} kg" for d in sorted(disagreeing))
                     + ".")
    if len(agreeing) < r.min_sources:
        lines.append(f"At least {r.min_sources} verified independent sites must agree: no weight proposed; needs "
                     "review.")
        return None, Decimal("0"), agreeing, disagreeing, lines
    value = r.base + min(r.per_extra_site * (len(agreeing) - r.min_sources), r.max_extra)
    lines.append(f"{r.base} for {r.min_sources} agreeing verified sites, +{r.per_extra_site} for each further one "
                 f"(at most +{r.max_extra}): {value}.")
    values = [counted[d] for d in agreeing]
    if max(values) - min(values) <= max(values) * r.tight_ratio:
        value += r.tight_bonus
        lines.append(f"+{r.tight_bonus}: the agreeing sites are within {_n(r.tight_ratio * 100)} % of each other.")
    strong = sorted(d for d in agreeing if sites[d][1] in (WeightResearchSource.CREDIBILITY_MANUFACTURER,
                                                           WeightResearchSource.CREDIBILITY_TRUSTED))
    if strong:
        value += r.trusted_bonus
        lines.append(f"+{r.trusted_bonus}: an allowlisted manufacturer site or trusted catalog agrees "
                     f"({', '.join(strong)}).")
    if not measurement:
        value -= r.unspecified_penalty
        lines.append(f"-{r.unspecified_penalty}: the pages do not say whether this is net or shipping weight.")
    if disagreeing:
        value -= r.disagree_penalty * len(disagreeing)
        lines.append(f"-{r.disagree_penalty} for each verified site that disagrees.")
    if misquoted:
        value -= r.misquote_penalty * misquoted
        lines.append(f"-{r.misquote_penalty} for each page the provider misquoted ({misquoted}).")
    value = max(Decimal("0"), min(value, r.cap)).quantize(Decimal("0.01"))
    if value < r.min_score:
        lines.append(f"Evidence score {value} is below {r.min_score}: no weight proposed; needs review.")
        return None, value, agreeing, disagreeing, lines
    proposal = Decimal(median(values)).quantize(Decimal("0.01"))
    lines.append(f"Proposed {proposal} kg (median of the agreeing sites), evidence score {value} ({band(value)}).")
    return proposal, value, agreeing, disagreeing, lines


def verify(part, claimed_kg, claimed_measurement, url, deadline, robots):
    """Fetch the cited page (robots.txt permitting) and check it. -> (outcome, page kg or None, page measurement, final
    domain, note, snippet). The one place a future structured-catalog source (LPK-248) would plug its own verified
    lookup in."""
    try:
        final_url, text = fetch_page(url, deadline, robots)
    except Unverifiable as error:
        return error.status, None, "", site(url), error.note, ""
    outcome, kg, measurement, note, snippet = evidence.check_page(text, part.part_number_key, part.brand_key,
                                                                  claimed_kg, claimed_measurement)
    domain = site(final_url)
    if final_url != url:
        note += f" (redirected to {domain})"
    return outcome, kg, measurement, domain, note, snippet


def evaluate(part, observations, unsourced=0):
    """Check every observation on its page and score the verified sites; saves nothing (the benchmark uses it too).
    -> (rows, best measurement, proposal, score, agreeing domains, disagreeing domains, explanation lines,
    misquoted count). `unsourced`: observations a provider cited without its search returning the URL."""
    r = rules()
    deadline = time.monotonic() + r.verify_seconds
    rows, fetched, robots = [], {}, {}  # robots: each host's robots.txt, read once per job
    for obs in observations:
        claimed, stated_measurement, domain, reason = check(part, obs)
        row = {"obs": obs, "stated_measurement": stated_measurement, "domain": domain, "outcome": evidence.NOT_CHECKED,
               "kg": None, "measurement": "", "snippet": "", "reason": reason}
        if claimed is not None:
            key = (obs.url, claimed, stated_measurement)
            if key not in fetched:  # the same page cited twice is fetched (and counted) once
                fetched[key] = verify(part, claimed, stated_measurement, obs.url, deadline, robots)
            outcome, kg, measurement, final_domain, note, snippet = fetched[key]
            row.update(outcome=outcome, kg=kg, measurement=measurement, snippet=snippet, reason=note,
                       domain=final_domain or domain)
        row["credibility"] = credibility(row["domain"], part, r) if row["domain"] else ""
        rows.append(row)
    verified = [row for row in rows if row["outcome"] == evidence.VERIFIED]
    # a page that shows another weight and a URL the search never returned both say the provider is unreliable here
    misquoted = sum(row["outcome"] == evidence.CONTRADICTED for row in rows) + unsourced
    groups = {}
    for row in verified:
        kgs = groups.setdefault(row["measurement"], {}).setdefault(row["domain"], ([], row["credibility"]))[0]
        kgs.append(row["kg"])
    results = {m: score({d: (Decimal(median(kgs)), cred) for d, (kgs, cred) in sites.items()}, m, r, misquoted)
               for m, sites in groups.items()}
    # a proposal first, then the higher score; shipping before net before unspecified on a tie
    best = max(results, key=lambda m: (results[m][0] is not None, results[m][1], -MEASUREMENTS.index(m)),
               default=None)
    proposal, value, agreeing, disagreeing, lines = results.get(
        best, (None, Decimal("0"), set(), set(), ["No observation was verified on its page: needs review."]))
    counts = {}
    for row in rows:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
    summary = (f"{len(rows)} observation(s) from the provider, checked on their pages: "  # stored: English always
               + ", ".join(f"{n} {o.replace('_', ' ')}" for o, n in sorted(counts.items())) + "."
               if rows else "The provider found no observation.")
    dropped = ([f"{unsourced} more observation(s) dropped: their URL was never returned by the provider's search."]
               if unsourced else [])
    other = [f"Also verified, not proposed: {m or 'unspecified'} weight." for m in results if m != best]
    lines = [summary, *dropped, *lines, *other,
             "The evidence score is a rule-based summary of the evidence above, not a probability."]
    return rows, best, proposal, value, agreeing, disagreeing, lines, misquoted


def _assess(job, observations, now, unsourced=0):
    part = job.part
    rows, best, proposal, value, agreeing, disagreeing, lines, misquoted = evaluate(part, observations, unsourced)
    verified = [row for row in rows if row["outcome"] == evidence.VERIFIED]
    with transaction.atomic():
        WeightResearchSource.objects.bulk_create([WeightResearchSource(
            job=job, url=row["obs"].url, domain=row["domain"], title=row["obs"].title,
            stated_part_number=row["obs"].part_number, stated_manufacturer=row["obs"].manufacturer,
            stated_value=row["obs"].value, stated_unit=row["obs"].unit, stated_measurement=row["stated_measurement"],
            measurement=row["measurement"], weight_kg=row["kg"], excerpt=row["obs"].excerpt,
            page_excerpt=row["snippet"][:500], verification=row["outcome"], credibility=row["credibility"],
            accepted=row["outcome"] == evidence.VERIFIED,
            agrees=row["outcome"] == evidence.VERIFIED and row["measurement"] == best and row["domain"] in agreeing,
            reason=row["reason"][:200]) for row in rows])
        common = {"explanation": "\n".join(lines), "confidence": value or None, "measurement": best or "",
                  "conflicting": bool(disagreeing) or misquoted > 0}
        if proposal is None:
            return _finish(job, WeightResearchJob.STATUS_NO_RESULT, now, **common)
        part.refresh_from_db(fields=["current_weight"])
        if part.current_weight_id is not None:  # approved meanwhile (operator, AMT): keep that, propose nothing
            common["explanation"] += "\nAn approved weight was recorded meanwhile; nothing proposed."
            return _finish(job, WeightResearchJob.STATUS_SKIPPED, now, **common)
        urls = "\n".join(f"- {row['obs'].url} ({row['kg']} kg on the page)" for row in verified
                         if row["measurement"] == best and row["domain"] in agreeing)
        weight = record_weight(part, proposal, PartWeight.SOURCE_AI, status=PartWeight.STATUS_PENDING,
                               evidence=f"AI research #{job.pk}, {best or 'unspecified'} weight, evidence score "
                                        f"{value} ({band(value)}).\n{common['explanation']}\nVerified sources:\n{urls}",
                               note=f"AI weight research #{job.pk}")
        return _finish(job, WeightResearchJob.STATUS_DONE, now, proposed_weight=weight, **common)


# --- operator decisions -----------------------------------------------------------------------------------------------

def waiting_customers(part):
    """Customers waiting on the part's open missing-weight task: who "approve and price" would price and notify."""
    return WeightTaskRequest.objects.filter(task__part=part, task__status__in=WeightTask.OPEN_STATUSES,
                                            outcome=WeightTaskRequest.OUTCOME_WAITING).count()


def _approve(job, actor):
    """-> True when approved now, False when it already was. Raises OperatorError."""
    from django.utils.translation import gettext as _

    from orders.services import OperatorError

    from .registry import review_weight

    if job.proposed_weight is None:
        raise OperatorError("proposed_weight", _("This research proposed no weight."))
    if job.proposed_weight.status == PartWeight.STATUS_REJECTED:
        raise OperatorError("proposed_weight", _("This weight was rejected."))
    return review_weight(job.proposed_weight.pk, PartWeight.STATUS_APPROVED, actor,
                         note=f"AI weight research #{job.pk}")


def _already_approved():
    from django.utils.translation import gettext as _

    from orders.services import OperatorError

    return OperatorError("proposed_weight", _("This AI weight is already approved; nothing changed."))


def approve_proposal(job_id, actor):
    """Approve the job's AI weight only: nobody is priced or notified. Later searches of the part are priced with it
    unless a stronger approved weight exists; waiting customers stay on the missing-weight task until an operator
    prices them. Raises OperatorError (nothing proposed, rejected, or already approved)."""
    job = WeightResearchJob.objects.select_related("proposed_weight", "part").get(pk=job_id)
    if not _approve(job, actor):
        raise _already_approved()


def approve_and_price(job_id, actor):
    """Approve the job's AI weight and, if the part has an open missing-weight task, price and notify its waiting
    customers once (parts.weight_tasks.approve_task, with the part's strongest approved weight: an AI weight never
    displaces an AMT, operator or measured one). -> the task priced, or None. Raises OperatorError."""
    from .weight_tasks import approve_task

    job = WeightResearchJob.objects.select_related("proposed_weight", "part").get(pk=job_id)
    approved_now = _approve(job, actor)
    task = job.part.weight_tasks.filter(status__in=WeightTask.OPEN_STATUSES).first()
    if task is None and not approved_now:
        raise _already_approved()
    return approve_task(task.pk, actor) if task else None
