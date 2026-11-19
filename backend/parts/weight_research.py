"""AI part-weight research (LPK-247).

When an AMT part has no usable weight, a research job is queued (once per part, see COOLDOWN). The cron command
`run_weight_research` sends the job to the research service, an external AI that searches the web, then checks every
observation it returns here, in plain code:

- the page must name exactly this part number and manufacturer;
- the unit must be a known mass unit and the measurement net or shipping weight;
- the weight must be plausible (0.01 to 500 kg) and the URL https;
- at least MIN_SOURCES independent sites (different domains) must agree within the tolerance.

The result is a *pending* AI weight with its sources, confidence and explanation. It prices nothing until an operator
approves it, through the existing review and missing-weight queue (parts.weight_tasks), and it never replaces a
stronger approved weight (parts.registry.record_weight).

Only the part number, manufacturer and the supplier's part name leave this server: never a customer, a VIN, a search
log or a price. Logs name the job and an error type only."""

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from statistics import median
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone

from accounts.validators import normalize_part_number

from .models import PartWeight, WeightResearchJob, WeightResearchSource, WeightTask, normalize_brand
from .registry import is_plausible, record_weight

logger = logging.getLogger(__name__)

COOLDOWN = timedelta(days=30)  # one paid search per part per month, whatever its outcome
RETRY_BASE = timedelta(minutes=10)  # 10, 20, 40 ... minutes between attempts
STALE_RUNNING = timedelta(minutes=30)  # a run that died mid-job (crash, kill) gives the job back
MIN_SOURCES = 2  # independent sites that must agree
MIN_CONFIDENCE = Decimal("0.50")
AGREE_RATIO, AGREE_MIN_KG = Decimal("0.10"), Decimal("0.05")  # within 10 % of the median, or 50 g
MAX_OBSERVATIONS = 20
MAX_RESPONSE_BYTES = 512 * 1024
TO_KG = {"kg": Decimal("1"), "kgs": Decimal("1"), "kilogram": Decimal("1"), "kilograms": Decimal("1"),
         "g": Decimal("0.001"), "gram": Decimal("0.001"), "grams": Decimal("0.001"),
         "lb": Decimal("0.45359237"), "lbs": Decimal("0.45359237"), "pound": Decimal("0.45359237"),
         "pounds": Decimal("0.45359237"), "oz": Decimal("0.028349523125"), "ounce": Decimal("0.028349523125"),
         "ounces": Decimal("0.028349523125")}
MEASUREMENTS = ("shipping", "net")  # preferred first: the courier fee is charged on the shipped weight
SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu"}


class ResearchError(Exception):
    """The research service failed or answered something unusable; the job is retried."""


@dataclass
class Observation:
    url: str
    title: str = ""
    part_number: str = ""
    manufacturer: str = ""
    value: str = ""
    unit: str = ""
    measurement: str = ""
    excerpt: str = ""


# --- the research service (the only code that talks to it) ------------------------------------------------------------

def research_query(part):
    """Everything the research service receives about a part. Nothing about any customer, search or price."""
    return {"part_number": part.part_number, "manufacturer": part.brand, "name": part.name,
            "max_cost_usd": str(settings.AI_WEIGHT_RESEARCH_MAX_COST_PER_JOB_USD), "min_sources": MIN_SOURCES}


def call_research_service(query):
    """POST the query as JSON; the service answers {"cost_usd": "0.04", "observations": [{url, title, part_number,
    manufacturer, value, unit, measurement, excerpt}, ...]}. -> (observations, cost). Raises ResearchError."""
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
    observations = []
    for item in data["observations"][:MAX_OBSERVATIONS]:
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            observations.append(Observation(**{key: _text(item.get(key), size) for key, size in (
                ("url", 1000), ("title", 300), ("part_number", 120), ("manufacturer", 120), ("value", 40),
                ("unit", 20), ("measurement", 20), ("excerpt", 1000))}))
    return observations, cost


def _text(value, size):
    return str(value).strip()[:size] if isinstance(value, (str, int, float)) else ""


def _money(value):
    """A reported cost, or None when missing or unreadable (the caller then assumes the per-job limit)."""
    try:
        cost = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return cost if cost.is_finite() and cost >= 0 else None


# --- queueing -------------------------------------------------------------------------------------------------------

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


# --- running --------------------------------------------------------------------------------------------------------

def spent_today():
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    # shortcut: a job's whole cost counts on the day of its latest attempt; exact per-attempt accounting is not needed
    # while one job costs cents and retries are rare.
    return WeightResearchJob.objects.filter(started_at__gte=start).aggregate(total=Sum("cost_usd"))["total"] or 0


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
        if spent_today() + settings.AI_WEIGHT_RESEARCH_MAX_COST_PER_JOB_USD > settings.AI_WEIGHT_RESEARCH_DAILY_BUDGET_USD:
            summary["budget_reached"] = True
            break
        status = run_job(job_id, now)
        if status is not None:
            summary["ran"] += 1
            summary["proposed"] += status == WeightResearchJob.STATUS_DONE
    return summary


def _requeue_stale(now):
    stale = WeightResearchJob.objects.filter(status=WeightResearchJob.STATUS_RUNNING,
                                             started_at__lt=now - STALE_RUNNING)
    for job in stale:  # it may have been billed before it died: counted at the per-job limit, like any failure
        WeightResearchJob.objects.filter(pk=job.pk).update(
            cost_usd=F("cost_usd") + settings.AI_WEIGHT_RESEARCH_MAX_COST_PER_JOB_USD)
        _failed_attempt(job, "interrupted", now)


def run_job(job_id, now=None):
    """Claim one queued job and run it. -> its new status, or None when another run claimed it first."""
    now = now or timezone.now()
    claimed = WeightResearchJob.objects.filter(pk=job_id, status=WeightResearchJob.STATUS_QUEUED).update(
        status=WeightResearchJob.STATUS_RUNNING, started_at=now, attempts=F("attempts") + 1)
    if not claimed:
        return None
    job = WeightResearchJob.objects.select_related("part__current_weight").get(pk=job_id)
    if job.part.current_weight is not None:
        return _finish(job, WeightResearchJob.STATUS_SKIPPED, now, explanation="The part already has an approved "
                       "weight; nothing was searched.")
    limit = settings.AI_WEIGHT_RESEARCH_MAX_COST_PER_JOB_USD
    try:
        observations, cost = call_research_service(research_query(job.part))
    except ResearchError as error:
        # An attempt that failed may still have been billed: count it at the per-job limit, so the budget holds.
        WeightResearchJob.objects.filter(pk=job.pk).update(cost_usd=F("cost_usd") + limit)
        logger.warning("AI weight research job %s failed: %s", job.pk, error)
        return _failed_attempt(job, str(error)[:200], now)
    WeightResearchJob.objects.filter(pk=job.pk).update(cost_usd=F("cost_usd") + (limit if cost is None else cost))
    if cost is not None and cost > limit:
        return _finish(job, WeightResearchJob.STATUS_FAILED, now, error="cost limit exceeded",
                       explanation=f"The service reported ${cost}, over the ${limit} limit per job; its answer was "
                                   "not used.")
    return _assess(job, observations, now)


def _failed_attempt(job, error, now):
    if job.attempts >= settings.AI_WEIGHT_RESEARCH_MAX_ATTEMPTS:
        return _finish(job, WeightResearchJob.STATUS_FAILED, now, error=error)
    WeightResearchJob.objects.filter(pk=job.pk).update(
        status=WeightResearchJob.STATUS_QUEUED, last_error=error,
        next_attempt_at=now + RETRY_BASE * 2 ** max(job.attempts - 1, 0))
    return WeightResearchJob.STATUS_QUEUED


def _finish(job, status, now, *, error="", explanation="", **fields):
    WeightResearchJob.objects.filter(pk=job.pk).update(status=status, finished_at=now, last_error=error,
                                                       explanation=explanation, **fields)
    return status


# --- checking the evidence ------------------------------------------------------------------------------------------

def site(url):
    """-> the registrable domain of an https URL ('shop.example.co.uk' -> 'example.co.uk'), or '' if not https."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or "." not in host:
        return ""
    labels = host.split(".")
    # shortcut: a small list of second-level labels instead of the public suffix list; good enough to tell shops apart.
    keep = 3 if len(labels) >= 3 and labels[-2] in SECOND_LEVEL and len(labels[-1]) == 2 else 2
    return ".".join(labels[-keep:])


def to_kg(value, unit):
    """-> kg or None. Accepts '1.2', '1,2' and '1 200' style numbers; units from TO_KG only."""
    factor = TO_KG.get(unit.strip().lower().rstrip("."))
    text = value.strip().replace(" ", "").replace(" ", "")
    if factor is None or not text:
        return None
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite() or number <= 0:
        return None
    return (number * factor).quantize(Decimal("0.001"))


def check(part, observation):
    """-> (weight in kg or None, measurement, domain, reason). None means the observation does not count."""
    domain = site(observation.url)
    measurement = observation.measurement.strip().lower()
    if not domain:
        return None, measurement, "", "not an https URL"
    if normalize_part_number(observation.part_number) != part.part_number_key:
        return None, measurement, domain, "names a different part number"
    if normalize_brand(observation.manufacturer) != part.brand_key:
        return None, measurement, domain, "names a different or no manufacturer"
    if measurement not in MEASUREMENTS:
        return None, measurement, domain, "measurement is not net or shipping weight"
    weight = to_kg(observation.value, observation.unit)
    if weight is None:
        return None, measurement, domain, "weight or unit not understood"
    if not is_plausible(weight):
        return None, measurement, domain, "outside 0.01 to 500 kg"
    return weight, measurement, domain, "valid"


def _agrees(value, centre):
    difference = abs(value - centre)
    return difference <= AGREE_MIN_KG or difference <= centre * AGREE_RATIO


def score(per_site):
    """per_site: {domain: kg} for one measurement type -> (proposal kg or None, confidence, agreeing domains,
    explanation lines). Explainable on purpose: every point added or taken off is written down."""
    if not per_site:
        return None, Decimal("0"), set(), ["No valid observation."]
    centre = Decimal(median(per_site.values()))
    agreeing = {domain for domain, kg in per_site.items() if _agrees(kg, centre)}
    disagreeing = set(per_site) - agreeing
    lines = [f"{len(per_site)} independent site(s) with a valid weight; {len(agreeing)} agree within 10 % or 50 g "
             f"of the median {centre.quantize(Decimal('0.001'))} kg."]
    if len(agreeing) < MIN_SOURCES:
        lines.append(f"At least {MIN_SOURCES} independent sites must agree: no weight proposed.")
        return None, Decimal("0"), agreeing, lines
    confidence = Decimal("0.55") + Decimal("0.10") * min(len(agreeing) - MIN_SOURCES, 2)
    lines.append(f"Base {Decimal('0.55')} for {MIN_SOURCES} agreeing sites, +0.10 for each further one (at most "
                 f"+0.20): {confidence}.")
    values = [per_site[d] for d in agreeing]
    if max(values) - min(values) <= max(values) * Decimal("0.02"):
        confidence += Decimal("0.10")
        lines.append("+0.10: the agreeing sites are within 2 % of each other.")
    if disagreeing:
        confidence -= Decimal("0.15") * len(disagreeing)
        lines.append(f"-0.15 for each site that disagrees ({', '.join(sorted(disagreeing))}).")
    confidence = max(Decimal("0"), min(confidence, Decimal("0.95"))).quantize(Decimal("0.01"))
    proposal = Decimal(median(values)).quantize(Decimal("0.01"))
    if confidence < MIN_CONFIDENCE:
        lines.append(f"Confidence {confidence} is below {MIN_CONFIDENCE}: no weight proposed.")
        return None, confidence, agreeing, lines
    lines.append(f"Proposed {proposal} kg (median of the agreeing sites), confidence {confidence}.")
    return proposal, confidence, agreeing, lines


def _assess(job, observations, now):
    part = job.part
    checked = [(obs, *check(part, obs)) for obs in observations]
    groups = {}
    for obs, weight, measurement, domain, reason in checked:
        if weight is not None:
            groups.setdefault(measurement, {}).setdefault(domain, []).append(weight)
    results = {m: score({d: Decimal(median(v)) for d, v in sites.items()}) for m, sites in groups.items()}
    # the measurement with the most agreeing sites; shipping weight wins a tie
    best = max(MEASUREMENTS, key=lambda m: (results[m][0] is not None, len(results[m][2]), m == "shipping")
               if m in results else (False, -1, False))
    proposal, confidence, agreeing, lines = results.get(best, (None, Decimal("0"), set(), ["No valid observation."]))
    rejected = sum(1 for row in checked if row[1] is None)
    if rejected:
        lines.append(f"{rejected} observation(s) did not count; see the sources for why.")
    with transaction.atomic():
        WeightResearchSource.objects.bulk_create([WeightResearchSource(
            job=job, url=obs.url, domain=domain, title=obs.title, stated_part_number=obs.part_number,
            stated_manufacturer=obs.manufacturer, stated_value=obs.value, stated_unit=obs.unit,
            measurement=measurement, weight_kg=weight, excerpt=obs.excerpt, accepted=weight is not None,
            agrees=weight is not None and measurement == best and domain in agreeing, reason=reason)
            for obs, weight, measurement, domain, reason in checked])
        explanation = "\n".join(lines)
        if proposal is None:
            return _finish(job, WeightResearchJob.STATUS_NO_RESULT, now, explanation=explanation,
                           confidence=confidence or None, measurement=best if best in results else "")
        part.refresh_from_db(fields=["current_weight"])
        if part.current_weight_id is not None:  # approved meanwhile (operator, AMT): keep that, propose nothing
            return _finish(job, WeightResearchJob.STATUS_SKIPPED, now, confidence=confidence, measurement=best,
                           explanation=explanation + "\nAn approved weight was recorded meanwhile; nothing proposed.")
        urls = "\n".join(f"- {obs.url} ({obs.value} {obs.unit}, {measurement})"
                         for obs, weight, measurement, domain, reason in checked
                         if weight is not None and measurement == best and domain in agreeing)
        weight = record_weight(part, proposal, PartWeight.SOURCE_AI, status=PartWeight.STATUS_PENDING,
                               evidence=f"AI research #{job.pk}, {best} weight, confidence {confidence}.\n"
                                        f"{explanation}\nSources:\n{urls}",
                               note=f"AI weight research #{job.pk}")
        return _finish(job, WeightResearchJob.STATUS_DONE, now, explanation=explanation, confidence=confidence,
                       measurement=best, proposed_weight=weight)


def approve_proposal(job_id, actor):
    """The operator approves the job's AI weight; if the part has an open missing-weight task, its waiting customers
    are priced with the part's approved weight (parts.weight_tasks.approve_task). The strongest approved weight still
    wins: approving an AI weight never displaces an AMT, operator or measured one. -> the task priced, or None.
    Raises OperatorError."""
    from django.utils.translation import gettext as _

    from orders.services import OperatorError

    from .registry import review_weight
    from .weight_tasks import approve_task

    job = WeightResearchJob.objects.select_related("proposed_weight", "part").get(pk=job_id)
    if job.proposed_weight is None:
        raise OperatorError("proposed_weight", _("This research proposed no weight."))
    if job.proposed_weight.status == PartWeight.STATUS_REJECTED:
        raise OperatorError("proposed_weight", _("This weight was rejected."))
    approved_now = review_weight(job.proposed_weight.pk, PartWeight.STATUS_APPROVED, actor,
                                 note=f"AI weight research #{job.pk}")
    task = job.part.weight_tasks.filter(status__in=WeightTask.OPEN_STATUSES).first()
    if task is None and not approved_now:
        raise OperatorError("proposed_weight", _("This AI weight is already approved; nothing changed."))
    return approve_task(task.pk, actor) if task else None
