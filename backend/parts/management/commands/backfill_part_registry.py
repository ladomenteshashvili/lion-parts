"""LPK-238: fill the parts registry from the search history already stored. Safe to run any number of times: a search
already linked is skipped, so counts and weights are never added twice. Without --apply it reports what it would do and
writes nothing. It only adds registry rows and links quote requests to their part: the stored offers and their price
snapshots are never changed. It opens no missing-weight tasks and notifies nobody (those searches are history)."""

from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Min, OuterRef, Subquery

from parts.models import PartMaster, PartQuoteRequest, PartSearchLog, PartSupplierOffer
from parts.registry import get_parts, identity, part_for_offer, record_search


class DryRun(Exception):
    pass


def _rows(log):
    """The stored offers as supplier rows. The supplier's own weight only: a customer or operator weight is not one."""
    rows = []
    for offer in log.offers():
        try:
            price = Decimal(str(offer["supplier_price_usd"]))
        except (KeyError, InvalidOperation, TypeError):
            continue  # an offer stored before supplier prices were kept: nothing reliable to learn
        if not price.is_finite() or price <= 0:
            continue
        prefix, _sep, rest = str(offer.get("offer_id", "")).partition("-")
        oem = rest.partition("-")[2] if prefix in ("AMT", "DEMO") else ""
        weight = None
        if offer.get("weight_source") == "api" and offer.get("weight_kg"):
            try:
                weight = Decimal(str(offer["weight_kg"]))
            except InvalidOperation:
                weight = None
        rows.append({"offer_id": offer["offer_id"], "oem": oem or log.part_number, "brand": offer.get("brand", ""),
                     "name": offer.get("name", ""), "supplier_price_usd": price, "supplier_weight_kg": weight})
    return rows


class Command(BaseCommand):
    help = "Fill the parts registry from stored searches (idempotent). Writes only with --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="write; without it nothing is saved")

    def handle(self, *args, apply=False, **options):
        counts = {"logs linked": 0, "logs already linked": 0, "supplier offers": 0, "quote requests linked": 0}
        parts_before = PartMaster.objects.count()
        try:
            with transaction.atomic():
                self.backfill(counts)
                counts["parts created"] = PartMaster.objects.count() - parts_before
                if not apply:
                    raise DryRun
        except DryRun:
            self.stdout.write("Dry run: nothing was saved. Run again with --apply to write.")
        for name, value in counts.items():
            self.stdout.write(f"{name}: {value}")

    def backfill(self, counts):
        logs = PartSearchLog.objects.filter(status=PartSearchLog.STATUS_SUCCESS).order_by("created_at", "id")
        linked = set(PartSupplierOffer.objects.values_list("search_log_id", flat=True).distinct())
        for log in logs.iterator(chunk_size=500):
            if log.pk in linked:
                counts["logs already linked"] += 1
                continue
            rows = _rows(log)
            if not rows:
                continue
            parts = get_parts((row["oem"], row["brand"], row["name"]) for row in rows)
            for row in rows:
                row["part"] = parts.get(identity(row["oem"], row["brand"]))
            with transaction.atomic():
                record_search(log, rows)
            counts["logs linked"] += 1
            counts["supplier offers"] += sum(1 for row in rows if row["part"])
        first_seen = PartSupplierOffer.objects.filter(part=OuterRef("pk")).values("part").annotate(
            first=Min("observed_at")).values("first")
        for part in PartMaster.objects.annotate(first=Subquery(first_seen)).filter(first__isnull=False):
            if part.first < part.first_seen_at:
                PartMaster.objects.filter(pk=part.pk).update(first_seen_at=part.first)
        for qr in PartQuoteRequest.objects.filter(part__isnull=True, search_log__isnull=False).exclude(offer_id=""):
            part = part_for_offer(qr.search_log, qr.offer_id)
            if part:
                PartQuoteRequest.objects.filter(pk=qr.pk).update(part=part)
                counts["quote requests linked"] += 1
