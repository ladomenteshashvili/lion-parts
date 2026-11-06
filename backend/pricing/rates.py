"""NBG USD rate (G§7.4): stored per day; refreshed when missing or older than rate_max_age_days."""

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.utils import timezone

from api.errors import ApiError

from .models import ExchangeRate, SiteSettings

logger = logging.getLogger(__name__)


class RateUnavailable(ApiError):
    """Renders as 502 rate_unavailable wherever it reaches the API."""

    def __init__(self):
        super().__init__("rate_unavailable", status=502)


def fetch_nbg_usd():
    """Fetch today's NBG USD rate and upsert it -> ExchangeRate. Raises RateUnavailable."""
    try:
        response = requests.get(settings.NBG_RATES_URL, timeout=10)
        response.raise_for_status()
        usd = next(c for c in response.json()[0]["currencies"] if c.get("code") == "USD")
        rate = (Decimal(str(usd["rate"])) / Decimal(str(usd.get("quantity") or 1))).quantize(Decimal("0.0001"))
        day = date.fromisoformat(str(usd["validFromDate"])[:10])
        valid = rate > 0  # comparing NaN raises InvalidOperation
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError, StopIteration,
            AttributeError, InvalidOperation, ZeroDivisionError):
        logger.warning("NBG rate fetch failed", exc_info=True)
        raise RateUnavailable() from None
    if not valid:
        raise RateUnavailable()
    return ExchangeRate.objects.update_or_create(date=day, currency="USD", defaults={"rate": rate})[0]


def get_usd_rate(fetch=True):
    """GEL per USD: the latest stored rate while it's fresh enough, else a new NBG fetch. fetch=False is for callers
    holding row locks (LPK-220): an outdated rate raises RateUnavailable there, with no network call."""
    latest = ExchangeRate.objects.filter(currency="USD").order_by("-date").first()
    max_age = timedelta(days=SiteSettings.get().rate_max_age_days)
    if latest and latest.date >= timezone.localdate() - max_age:
        return latest.rate
    if not fetch:
        raise RateUnavailable()
    return fetch_nbg_usd().rate
