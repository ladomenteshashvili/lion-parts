"""Customer-facing price validity in Georgian business days (G§7.5).

Boundary: a price set on day D is valid until the end of the N-th business day after D (Monday to
Friday, excluding SiteSettings.public_holidays), Asia/Tbilisi time. price_valid_until is the first
instant it is no longer valid (00:00 Tbilisi of the following day), so a price is expired when
now >= price_valid_until. D itself never counts, also when D is a holiday or a weekend day."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import SiteSettings

TBILISI = ZoneInfo("Asia/Tbilisi")


def add_business_days(day, count, holidays):
    while count > 0:
        day += timedelta(days=1)
        if day.weekday() < 5 and day not in holidays:
            count -= 1
    return day


def price_valid_until(start=None):
    site = SiteSettings.get()
    holidays = {date.fromisoformat(d) for d in site.public_holidays}
    day = (start or timezone.now()).astimezone(TBILISI).date()
    last_day = add_business_days(day, site.price_valid_business_days, holidays)
    return datetime.combine(last_day + timedelta(days=1), time.min, tzinfo=TBILISI)


def is_expired(valid_until, now=None):
    return valid_until is None or (now or timezone.now()) >= valid_until
