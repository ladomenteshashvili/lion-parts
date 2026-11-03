"""Price validity in Georgian business days (G§7.5): Mon-Fri, configured holidays, Asia/Tbilisi."""

from datetime import date, datetime, timedelta, timezone as dt_timezone

from django.test import TestCase

from pricing.models import SiteSettings
from pricing.validity import TBILISI, add_business_days, is_expired, price_valid_until


def tbilisi(*args):
    return datetime(*args, tzinfo=TBILISI)


class BusinessDayTests(TestCase):
    def test_ten_business_days_skip_weekends(self):
        # Wednesday 2026-11-04 -> 10 business days later: Wednesday 2026-11-18
        self.assertEqual(add_business_days(date(2026, 11, 4), 10, set()), date(2026, 11, 18))
        # Friday -> Monday counts as 1
        self.assertEqual(add_business_days(date(2026, 11, 6), 1, set()), date(2026, 11, 9))
        # started on a Saturday: Monday is day 1
        self.assertEqual(add_business_days(date(2026, 11, 7), 1, set()), date(2026, 11, 9))

    def test_holidays_are_skipped(self):
        holidays = {date(2026, 11, 23)}  # St George's Day, a Monday
        self.assertEqual(add_business_days(date(2026, 11, 20), 1, holidays), date(2026, 11, 24))

    def test_valid_until_is_the_start_of_the_day_after_the_last_business_day(self):
        start = tbilisi(2026, 11, 4, 15, 30)
        self.assertEqual(price_valid_until(start), tbilisi(2026, 11, 19, 0, 0))
        site = SiteSettings.get()
        site.public_holidays = ["2026-11-23"]
        site.price_valid_business_days = 3
        site.save()
        # Fri 20 -> Mon 23 is a holiday -> Tue 24, Wed 25, Thu 26 -> valid until Fri 27 00:00
        self.assertEqual(price_valid_until(tbilisi(2026, 11, 20, 9)), tbilisi(2026, 11, 27, 0, 0))

    def test_the_day_is_taken_in_tbilisi(self):
        # 21:30 UTC on Tuesday is already Wednesday 01:30 in Tbilisi (UTC+4)
        utc = datetime(2026, 11, 3, 21, 30, tzinfo=dt_timezone.utc)
        self.assertEqual(price_valid_until(utc), tbilisi(2026, 11, 19, 0, 0))

    def test_expiry_boundary(self):
        until = tbilisi(2026, 11, 19, 0, 0)
        self.assertFalse(is_expired(until, until - timedelta(microseconds=1)))
        self.assertTrue(is_expired(until, until))
        self.assertTrue(is_expired(None))
