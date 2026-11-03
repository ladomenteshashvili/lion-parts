"""Scheduled every 5 minutes (G§7.10, G§11): ask the provider about card payments that are still
pending. Final answers go through mark_payment; payments pending for over 30 minutes become `unknown`
(shown in admin). One failing payment never stops the run. Idempotent."""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from payments.models import Payment
from payments.providers import PLATFORM_STATUSES, get_provider
from payments.services import mark_payment

logger = logging.getLogger(__name__)
CHECK_AFTER = timedelta(minutes=2)
UNKNOWN_AFTER = timedelta(minutes=30)


class Command(BaseCommand):
    help = "Reconcile pending card payments with the payment provider."

    def handle(self, *args, **options):
        provider, now = get_provider(), timezone.now()
        counts = {"updated": 0, "unknown": 0, "errors": 0}
        for payment in Payment.objects.filter(provider=provider.name, status=Payment.STATUS_PENDING,
                                              created_at__lt=now - CHECK_AFTER):
            try:
                status = provider.fetch_status(payment)
                if status not in PLATFORM_STATUSES:
                    raise ValueError(f"unexpected status {status!r}")
                if status != Payment.STATUS_PENDING:
                    mark_payment(payment, status, payload={"reconciled": status})
                    counts["updated"] += 1
                elif payment.created_at < now - UNKNOWN_AFTER:
                    mark_payment(payment, Payment.STATUS_UNKNOWN)
                    logger.warning("Payment %s still pending after 30 minutes: marked unknown", payment.pk)
                    counts["unknown"] += 1
            except Exception:
                logger.exception("Reconciling payment %s failed", payment.pk)
                counts["errors"] += 1
        self.stdout.write("updated: {updated}, unknown: {unknown}, errors: {errors}".format(**counts))
