"""Benchmark the research providers on a fixed list of US-market parts (LPK-248).

Without --run it is a dry run: it prints what would be sent and the worst-case spend, and calls nothing. With --run and
--confirm-spend (at least the worst case) it asks each provider about each part, checks every answer exactly as a
research job does (pages fetched and verified by this server) and writes one CSV row per part and provider. It saves
nothing to the database and does not count against the daily research budget: --confirm-spend is its only limit."""

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string

from accounts.validators import normalize_part_number
from parts.ai_providers import prompt
from parts.models import PartMaster, normalize_brand
from parts.weight_research import PROVIDERS, ResearchError, chain, evaluate, research_query

DEFAULT_FILE = Path(__file__).resolve().parents[2] / "data" / "weight_benchmark_us.csv"
COLUMNS = ("id", "manufacturer", "part_number", "provider", "model", "error", "searches", "cost_usd", "observations",
           "unsourced", "verified", "measurement", "proposal_kg", "score", "reference_kg", "difference_kg")


def parts(path, limit):
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))[:limit]
    return [(row, PartMaster(part_number=row["part_number"], brand=row["manufacturer"], name=row["name"],
                             part_number_key=normalize_part_number(row["part_number"]),
                             brand_key=normalize_brand(row["manufacturer"]))) for row in rows]  # never saved


class Command(BaseCommand):
    help = "Benchmark AI weight research providers on a fixed part list. Dry run unless --run --confirm-spend."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(DEFAULT_FILE))
        parser.add_argument("--providers", default="", help="comma-separated (default: the configured chain)")
        parser.add_argument("--limit", type=int, default=None, help="only the first N parts")
        parser.add_argument("--run", action="store_true", help="make the paid calls")
        parser.add_argument("--confirm-spend", default="", help="USD you accept to spend; at least the worst case")

    def handle(self, *args, file, providers, limit, run, confirm_spend, **options):
        names = [n.strip() for n in providers.split(",") if n.strip()] or chain()
        unknown = set(names) - set(PROVIDERS)
        if unknown:
            raise CommandError(f"unknown provider(s): {', '.join(sorted(unknown))}")
        rows = parts(file, limit)
        per_call = settings.AI_WEIGHT_RESEARCH_MAX_COST_PER_ATTEMPT_USD
        worst = per_call * len(rows) * len(names)
        self.stderr.write(f"{len(rows)} parts x {len(names)} provider(s) ({', '.join(names)}): {len(rows) * len(names)}"
                          f" calls, worst case ${worst} at ${per_call} per call (estimates may exceed it; unknown "
                          "costs count as the per-call limit).")
        if not run:
            if rows:
                self.stderr.write("Dry run, nothing sent. First prompt:\n" + prompt(research_query(rows[0][1])))
            return
        try:
            accepted = Decimal(confirm_spend)
        except InvalidOperation:
            accepted = Decimal("-1")
        if accepted < worst:
            raise CommandError(f"--run needs --confirm-spend of at least {worst} (USD).")
        writer = csv.DictWriter(self.stdout, fieldnames=COLUMNS)
        writer.writeheader()
        spent = Decimal("0")
        for row, part in rows:
            for name in names:
                if spent + per_call > accepted:
                    self.stderr.write(f"Stopped: ${spent} spent, the next call could exceed --confirm-spend.")
                    return
                result, cost = self.one(row, part, name, per_call)
                writer.writerow(result)
                spent += cost
        self.stderr.write(f"Done: about ${spent} (estimates and per-call limits for unknown costs).")

    def one(self, row, part, name, per_call):
        """-> (CSV row, cost counted: the estimate, or the per-call limit when unknown)"""
        out = {"id": row["id"], "manufacturer": row["manufacturer"], "part_number": row["part_number"],
               "provider": name, "reference_kg": row.get("reference_kg", "")}
        try:
            answer = import_string(PROVIDERS[name])(research_query(part))
        except ResearchError as error:
            return {**out, "error": str(error)}, per_call if error.sent else Decimal("0")
        cost = per_call if answer.cost is None else answer.cost
        out.update(model=answer.model, error=answer.error, searches=answer.searches, cost_usd=answer.cost,
                   observations=len(answer.observations), unsourced=answer.unsourced)
        if answer.error:
            return out, cost
        rows, best, proposal, value, *_ = evaluate(part, answer.observations, answer.unsourced)
        out.update(verified=sum(r["outcome"] == "verified" for r in rows), measurement=best or "",
                   proposal_kg=proposal, score=value)
        try:
            out["difference_kg"] = abs(proposal - Decimal(row["reference_kg"])) if proposal is not None else ""
        except (InvalidOperation, TypeError):
            pass  # no reference weight measured yet
        return out, cost
