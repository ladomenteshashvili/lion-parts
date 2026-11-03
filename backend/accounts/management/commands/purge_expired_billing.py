"""Monthly (G§11): anonymize the billing data of deleted customers' orders after the 5-year retention.
Idempotent."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from orders.models import Order

RETENTION_DAYS = 5 * 365 + 2  # 5 years including leap days



class Command(BaseCommand):
    help = "Anonymize billing data on orders of deleted customers older than 5 years."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=RETENTION_DAYS)
        count = Order.objects.filter(customer__anonymized_at__isnull=False, created_at__lt=cutoff).update(billing_snapshot={"anonymized": True})
        self.stdout.write(f"{count} order(s) anonymized")
