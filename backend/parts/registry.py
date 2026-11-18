"""Parts Master Registry (LPK-238, LPK-239): part identity, supplier offers, demand counts and weight knowledge.

A part is a normalized part number from one manufacturer: the same number from two manufacturers is two parts, and a
weight belongs to exactly one part. Nothing here stores a customer or a customer's price: a search keeps its own priced
offers (G§7.6), and a reused weight is priced again for every customer (pricing.engine)."""

import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts.validators import normalize_part_number

from .models import PartMaster, PartSearchLog, PartSupplierOffer, PartWeight, PartWeightEvent, normalize_brand

logger = logging.getLogger(__name__)

# A car part outside this range is a supplier or typing error far more often than real. Such a weight is recorded for
# review but never priced with until an operator approves it.
MIN_PLAUSIBLE_KG, MAX_PLAUSIBLE_KG = Decimal("0.01"), Decimal("500")
# A new weight differing from the current one by more than both limits is flagged as a conflict.
CONFLICT_RATIO, CONFLICT_MIN_KG = Decimal("0.20"), Decimal("0.05")
# What a client sees as weight_source (G§8.0 Offer: api | customer | operator | ""): only the supplier's own is "api".
PUBLIC_SOURCE = {PartWeight.SOURCE_AMT: "api", PartWeight.SOURCE_AI: "operator", PartWeight.SOURCE_OPERATOR: "operator",
                 PartWeight.SOURCE_PHYSICAL: "operator"}
KG = Decimal("0.01")


class InvalidWeight(ValueError):
    pass


def clean_weight(value):
    """-> a positive weight in kg with two decimals, as every weight field stores it. Raises InvalidWeight."""
    try:
        number = Decimal(str(value)).quantize(KG)
    except (InvalidOperation, TypeError, ValueError):
        raise InvalidWeight(value) from None
    if not number.is_finite() or number <= 0 or number >= Decimal("1000000"):
        raise InvalidWeight(value)
    return number


def _kg(weight):
    return Decimal(weight).quantize(KG)


def is_plausible(weight):
    return MIN_PLAUSIBLE_KG <= weight <= MAX_PLAUSIBLE_KG


def identity(part_number, brand):
    return normalize_part_number(part_number)[:120], normalize_brand(brand)[:120]


def get_parts(items):
    """items: (part number, brand, name) triples -> {identity: PartMaster}, creating the missing ones. Concurrent
    searches for a new part read back the row the first one inserted: never two rows for one identity."""
    wanted = {}
    for number, brand, name in items:
        key = identity(number, brand)
        if key[0]:
            wanted.setdefault(key, (str(number)[:120], str(brand or "")[:120], str(name or "")[:255]))
    if not wanted:
        return {}

    def load():
        numbers = {key[0] for key in wanted}
        rows = PartMaster.objects.filter(part_number_key__in=numbers).select_related("current_weight")
        return {(part.part_number_key, part.brand_key): part for part in rows}

    parts = load()
    missing = [PartMaster(part_number=number, brand=brand, name=name, part_number_key=key[0], brand_key=key[1])
               for key, (number, brand, name) in wanted.items() if key not in parts]
    if missing:
        PartMaster.objects.bulk_create(missing, ignore_conflicts=True)
        parts = load()
    return {key: parts[key] for key in wanted}


def weight_for_row(part, supplier_weight, rejected=frozenset()):
    """-> (weight, public weight_source) to price a supplier row with, or (None, "") when no usable weight is known.
    A stronger approved weight (operator, physically measured) beats the supplier's; then the supplier's own plausible
    weight, unless an operator rejected that value for this part (`rejected`); then the part's approved weight on
    record. Pending and rejected weights are never used."""
    current = part.current_weight if part else None
    if current and current.rank > PartWeight.RANK[PartWeight.SOURCE_AMT]:
        return current.weight_kg, PUBLIC_SOURCE[current.source]
    if supplier_weight is not None and is_plausible(supplier_weight) and _kg(supplier_weight) not in rejected:
        return supplier_weight, "api"
    if current:
        return current.weight_kg, PUBLIC_SOURCE[current.source]
    return None, ""


def with_known_weights(rows):
    """Before pricing a search: each row gets its registry part and the weight to price with (LPK-239). A registry
    failure never fails the search: the rows are then priced as before, from the supplier weight alone."""
    rejected = {}
    try:
        parts = get_parts((row.get("oem", ""), row["brand"], row["name"]) for row in rows)
        refused = PartWeight.objects.filter(part__in=parts.values(), source=PartWeight.SOURCE_AMT,
                                            status=PartWeight.STATUS_REJECTED)
        for part_id, weight in refused.values_list("part_id", "weight_kg"):
            rejected.setdefault(part_id, set()).add(weight)
    except Exception:
        logger.exception("Parts registry lookup failed; pricing from supplier weights only")
        parts = {}
    out = []
    for row in rows:
        part = parts.get(identity(row.get("oem", ""), row["brand"]))
        weight, source = weight_for_row(part, row["weight_kg"], rejected.get(part.pk, ()) if part else ())
        out.append({**row, "part": part, "supplier_weight_kg": row["weight_kg"], "weight_kg": weight,
                    "weight_source": source or "api"})
    return out


def _supplier_weight(value):
    """The raw supplier weight as stored on a supplier offer, or None when it does not fit the column."""
    if value is None or not value.is_finite() or value >= Decimal("1000000"):
        return None
    return Decimal(str(value)).quantize(Decimal("0.001"))


def record_search(log, rows):
    """After a successful search is saved: link its supplier rows to their parts, count the demand and keep the AMT
    weights (LPK-238). Counts only, never who searched. Idempotent per log (one row per log and offer)."""
    rows = [row for row in rows if row.get("part")]
    if not rows:
        return
    PartSupplierOffer.objects.bulk_create(
        [PartSupplierOffer(part=row["part"], search_log=log, offer_id=row["offer_id"], provider=log.provider,
                           supplier_price_usd=row["supplier_price_usd"],
                           supplier_weight_kg=_supplier_weight(row["supplier_weight_kg"]), observed_at=log.created_at)
         for row in rows], ignore_conflicts=True)
    parts = {row["part"].pk: row for row in rows}  # the first row per part
    if log.kind == PartSearchLog.KIND_SEARCH:
        PartMaster.objects.filter(pk__in=parts).update(search_count=F("search_count") + 1,
                                                       vin_search_count=F("vin_search_count") + (1 if log.vin else 0),
                                                       last_searched_at=log.created_at)
    for pk, row in parts.items():
        PartMaster.objects.filter(pk=pk).update(last_supplier_price_usd=row["supplier_price_usd"])
    if log.provider == PartSearchLog.PROVIDER_AMT:  # demo rows are invented: never knowledge
        for row in rows:
            if row["supplier_weight_kg"] is not None:
                record_supplier_weight(row["part"], row["supplier_weight_kg"], log)


def record_supplier_weight(part, value, log):
    """An AMT weight: approved by the system when plausible, else left for review. The same weight as the latest AMT
    observation only counts as seen again, so an operator's rejection of it stays in force."""
    try:
        weight = clean_weight(value)
    except InvalidWeight:
        return None
    latest = part.weights.filter(source=PartWeight.SOURCE_AMT).order_by("-observed_at", "-id").first()
    if latest and latest.weight_kg == weight:
        PartWeight.objects.filter(pk=latest.pk).update(last_seen_at=log.created_at, seen_count=F("seen_count") + 1)
        return latest
    # shortcut: two simultaneous searches may both record a new AMT weight; the duplicate is harmless (same rank and
    # value), so no lock is taken on the search path.
    status = PartWeight.STATUS_APPROVED if is_plausible(weight) else PartWeight.STATUS_PENDING
    return record_weight(part, weight, PartWeight.SOURCE_AMT, status=status, search_log=log, observed_at=log.created_at,
                         note="AMT search")


def _conflicts(current, weight):
    if current is None or current.weight_kg == weight:
        return False
    difference = abs(current.weight_kg - weight)
    return difference > CONFLICT_MIN_KG and difference > current.weight_kg * CONFLICT_RATIO


def record_weight(part, weight, source, *, status, actor=None, evidence="", search_log=None, observed_at=None,
                  note=""):
    """Store an observation with its audit entry and refresh the part's current weight. Never replaces a stronger
    weight: the current weight is always the strongest approved one, so a weaker newcomer is only flagged."""
    weight = clean_weight(weight)
    now = timezone.now()
    with transaction.atomic():
        locked = PartMaster.objects.select_for_update(of=("self",)).select_related("current_weight").get(pk=part.pk)
        observation = PartWeight.objects.create(
            part=locked, weight_kg=weight, source=source, status=status, observed_at=observed_at or now,
            last_seen_at=observed_at or now, search_log=search_log, evidence=evidence,
            conflict=_conflicts(locked.current_weight, weight), implausible=not is_plausible(weight),
            reviewed_by=actor if status != PartWeight.STATUS_PENDING else None,
            reviewed_at=now if status != PartWeight.STATUS_PENDING else None)
        PartWeightEvent.objects.create(weight=observation, action=PartWeightEvent.ACTION_RECORDED, actor=actor,
                                       note=note)
        if status == PartWeight.STATUS_APPROVED:
            PartWeightEvent.objects.create(weight=observation, action=PartWeightEvent.ACTION_APPROVED, actor=actor)
        refresh_current_weight(locked)
    part.current_weight = locked.current_weight
    return observation


def review_weight(observation_id, status, actor, note=""):
    """Approve or reject an observation (admin). -> False when it already has that status (a repeated click)."""
    action = {PartWeight.STATUS_APPROVED: PartWeightEvent.ACTION_APPROVED,
              PartWeight.STATUS_REJECTED: PartWeightEvent.ACTION_REJECTED}[status]
    with transaction.atomic():
        part_id = PartWeight.objects.values_list("part_id", flat=True).get(pk=observation_id)
        part = PartMaster.objects.select_for_update(of=("self",)).select_related("current_weight").get(pk=part_id)
        observation = PartWeight.objects.get(pk=observation_id)
        if observation.status == status:
            return False
        observation.status, observation.reviewed_by, observation.reviewed_at = status, actor, timezone.now()
        if status == PartWeight.STATUS_APPROVED:
            current = part.current_weight if part.current_weight_id != observation.pk else None
            observation.conflict = _conflicts(current, observation.weight_kg)
        observation.save()
        PartWeightEvent.objects.create(weight=observation, action=action, actor=actor, note=note)
        refresh_current_weight(part)
    return True


def refresh_current_weight(part):
    """The strongest approved observation, the newest within a source (call with the part locked)."""
    approved = part.weights.filter(status=PartWeight.STATUS_APPROVED)
    best = max(approved, key=lambda w: (w.rank, w.observed_at, w.pk), default=None)
    if part.current_weight_id != (best.pk if best else None):
        PartMaster.objects.filter(pk=part.pk).update(current_weight=best)
    part.current_weight = best
    return best


def part_for_offer(log, offer_id):
    """The registry part a stored search offer was linked to, or None (older logs before the backfill)."""
    link = PartSupplierOffer.objects.filter(search_log=log, offer_id=offer_id).select_related("part").first()
    return link.part if link else None
