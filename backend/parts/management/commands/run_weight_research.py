"""Run the due AI weight research jobs (LPK-247). Cron, every 15 minutes; does nothing while the feature is off."""

from django.conf import settings
from django.core.management.base import BaseCommand

from parts.weight_research import run_due_jobs


class Command(BaseCommand):
    help = "Run due AI part-weight research jobs, within the per-run limit and the daily budget."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="at most this many jobs (default: the setting)")

    def handle(self, *args, limit=None, **options):
        if not settings.AI_WEIGHT_RESEARCH_ENABLED:
            self.stdout.write("AI weight research is off (AI_WEIGHT_RESEARCH_ENABLED=False); nothing to do.")
            return
        summary = run_due_jobs(limit=limit)
        self.stdout.write(f"ran {summary['ran']}, weights proposed {summary['proposed']}"
                          + (", daily budget reached" if summary["budget_reached"] else ""))
