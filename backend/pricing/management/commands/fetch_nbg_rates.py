"""Daily 00:15 Asia/Tbilisi (G§11): store today's NBG USD rate. Idempotent (upsert per date)."""

from django.core.management.base import BaseCommand, CommandError

from pricing.rates import RateUnavailable, fetch_nbg_usd


class Command(BaseCommand):
    help = "Fetch the NBG USD rate and store it."

    def handle(self, *args, **options):
        try:
            rate = fetch_nbg_usd()
        except RateUnavailable as exc:
            raise CommandError("NBG rate unavailable") from exc
        self.stdout.write(f"USD {rate.rate} for {rate.date}")
