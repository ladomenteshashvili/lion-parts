"""Scheduled every 5 minutes (G§7.10, G§11): ask the provider about its card payments that are still pending
or unknown. Final answers go through mark_payment; payments pending for over 30 minutes become `unknown`
(shown in admin), and age alone never closes an unknown payment. One failing payment never stops the run.
Logs carry the payment id and error type only: provider errors can contain URLs or credentials. Idempotent."""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from payments.models import Payment
from payments.providers import get_provider
from payments.services import mark_payment, recheck_payment

logger = logging.getLogger(__name__)
CHECK_AFTER = timedelta(minutes=2)
UNKNOWN_AFTER = timedelta(minutes=30)


class Command(BaseCommand):
    help = "Reconcile pending and unknown card payments with the payment provider."

    def handle(self, *args, **options):
        provider, now = get_provider(), timezone.now()
        counts = {"updated": 0, "unknown": 0, "errors": 0}
        for payment in Payment.objects.filter(provider=provider.name,
                                              status__in=(Payment.STATUS_PENDING, Payment.STATUS_UNKNOWN),
                                              created_at__lt=now - CHECK_AFTER):
            try:
                outcome = recheck_payment(payment, provider)
                if outcome == "updated":
                    counts["updated"] += 1
                elif outcome == "conflict":  # logged by recheck_payment: a person must look
                    counts["errors"] += 1
                elif outcome == "pending" and payment.status == Payment.STATUS_PENDING \
                        and payment.created_at < now - UNKNOWN_AFTER:
                    # refused when a callback closed the payment during the provider call
                    if mark_payment(payment, Payment.STATUS_UNKNOWN).status == Payment.STATUS_UNKNOWN:
                        logger.warning("Payment %s still pending after 30 minutes: marked unknown", payment.pk)
                        counts["unknown"] += 1
            except Exception as error:
                logger.error("Reconciling payment %s failed: %s", payment.pk, type(error).__name__)
                counts["errors"] += 1
        self.stdout.write("updated: {updated}, unknown: {unknown}, errors: {errors}".format(**counts))
