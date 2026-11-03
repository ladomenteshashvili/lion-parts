"""Scheduled (G§11, e.g. daily 00:15 Asia/Tbilisi and a few times during the day): store the NBG USD
rate and, when it changed, re-convert every uncommitted GEL cart price. Idempotent.

Outcomes: "unavailable" (exit code 1, nothing changed), "unchanged" (same rate as the latest stored),
"updated" (new rate; N cart lines re-converted). Canonical USD prices and orders are never changed;
search results, quotes and history convert at read time, so they need no update here."""

from django.core.management.base import BaseCommand, CommandError

from cart.services import refresh_gel_prices
from pricing.rates import RateUnavailable, fetch_nbg_usd
from pricing.services import latest_usd_rate


class Command(BaseCommand):
    help = "Fetch the NBG USD rate and re-convert uncommitted GEL cart prices when it changed."

    def handle(self, *args, **options):
        previous = latest_usd_rate()
        try:
            rate = fetch_nbg_usd()
        except RateUnavailable as exc:
            raise CommandError("unavailable: the NBG rate could not be fetched; nothing was changed") from exc
        lines = refresh_gel_prices(rate.rate)  # also catches up after an earlier failed refresh
        if previous is not None and previous.rate == rate.rate and not lines:
            self.stdout.write(f"unchanged: USD {rate.rate} for {rate.date}")
        else:
            self.stdout.write(f"updated: USD {rate.rate} for {rate.date}; {lines} GEL cart line(s) re-converted")
