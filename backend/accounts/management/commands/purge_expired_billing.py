"""Monthly (G§11): anonymize the billing data of deleted customers' orders after the 5-year retention.
Idempotent."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from orders.models import Order

RETENTION_DAYS = 5 * 365 + 2  # 5 years including leap days

# shortcut: until LP-BE-601 adds Order.billing_snapshot, the legacy snapshot fields are blanked;
# LP-BE-601 switches this to billing_snapshot={"anonymized": true}.
BILLING_FIELDS = {
    "customer_name": "", "customer_phone": "",
    "legal_entity_company_identification_code": "", "legal_entity_company_official_name": "",
    "legal_entity_legal_address": "", "legal_entity_contact_first_name": "", "legal_entity_contact_last_name": "",
    "legal_entity_email": "", "legal_entity_mobile_phone": "",
}


class Command(BaseCommand):
    help = "Anonymize billing data on orders of deleted customers older than 5 years."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=RETENTION_DAYS)
        count = Order.objects.filter(customer__anonymized_at__isnull=False, created_at__lt=cutoff).update(**BILLING_FIELDS)
        self.stdout.write(f"{count} order(s) anonymized")
