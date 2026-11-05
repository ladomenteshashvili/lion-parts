"""Scheduled every 15 minutes (LPK-219): look for provider evidence on card payments closed here while their provider page
may still take money. A paid answer is counted through the usual path; a closing answer keeps the local status and notifies
nobody. Each payment is claimed before its provider call, no locks are held during the call, and a run does a bounded amount
of work. Checks back off to one a day and continue until there is evidence, so nothing is abandoned. Logs carry payment ids
and error types only: provider errors can contain URLs or credentials. Idempotent."""

import logging
import time

from django.core.management.base import BaseCommand
from django.db.models import F, Q
from django.utils import timezone

from payments.models import Payment
from payments.providers import get_provider
from payments.services import (MANUAL_PROVIDERS, NO_MONEY_PROVIDERS, UNRESOLVED_SESSION, claim_closure_check,
                               recheck_payment)

logger = logging.getLogger(__name__)
BATCH_SIZE = 100
ALERT_AFTER_CHECKS = 8  # about 70 hours
STOP_AFTER_ERRORS_IN_A_ROW = 5  # likely an outage: don't spend the remaining payments' checks on it
RUN_SECONDS = 600  # slow provider answers must not pile runs up, one starting every 15 minutes


class Command(BaseCommand):
    help = "Look for provider evidence on closed card payments whose provider page may still take money."

    def handle(self, *args, **options):
        provider, now = get_provider(), timezone.now()
        counts = {"checked": 0, "closed": 0, "paid": 0, "open": 0, "errors": 0}
        unresolved = Payment.objects.filter(UNRESOLVED_SESSION, provider=provider.name)
        due = unresolved.filter(Q(closure_next_check_at__isnull=True) | Q(closure_next_check_at__lte=now)).order_by(
            F("closure_next_check_at").asc(nulls_first=True), "id")[:BATCH_SIZE]
        started, errors_in_a_row = time.monotonic(), 0
        for payment in due:
            if errors_in_a_row >= STOP_AFTER_ERRORS_IN_A_ROW or time.monotonic() - started > RUN_SECONDS:
                logger.error("Stopped this run of closed payment checks early; the rest stay due")
                break
            if not claim_closure_check(payment, now):
                continue
            counts["checked"] += 1
            try:
                outcome = recheck_payment(payment, provider)
            except Exception as error:
                logger.error("Closed payment %s: provider query failed: %s", payment.pk, type(error).__name__)
                counts["errors"] += 1
                errors_in_a_row += 1
                continue
            errors_in_a_row = 0
            if outcome == "updated":
                if Payment.objects.filter(pk=payment.pk, status=Payment.STATUS_PAID).exists():
                    counts["paid"] += 1
                    logger.error("Closed payment %s: the provider reports it paid", payment.pk)
            elif outcome == "closed":
                counts["closed"] += 1
            elif outcome == "pending":
                counts["open"] += 1
                logger.warning("Closed payment %s: the provider still shows its session open", payment.pk)
            elif outcome == "conflict":  # logged by recheck_payment: a person must look
                counts["errors"] += 1

        overdue = list(unresolved.filter(closure_checks__gte=ALERT_AFTER_CHECKS).values_list("pk", flat=True)[:21])
        if overdue:
            logger.error("Closed payments with no provider evidence after %d checks (first 20 ids): %s", ALERT_AFTER_CHECKS,
                         overdue[:20])
        other = Payment.objects.filter(
            UNRESOLVED_SESSION | Q(status__in=(Payment.STATUS_PENDING, Payment.STATUS_UNKNOWN))
        ).exclude(provider__in=(provider.name, *MANUAL_PROVIDERS, *NO_MONEY_PROVIDERS)).count()
        if other:  # nothing can ask the provider they belong to
            logger.error("%d unresolved card payments belong to another provider", other)
        review = Payment.objects.filter(review_required_at__isnull=False, reviewed_at__isnull=True).count()
        if review:
            logger.error("%d paid results are waiting for review in the payment admin", review)
        self.stdout.write(("checked: {checked}, closed: {closed}, paid: {paid}, open: {open}, errors: {errors}, "
                           "unresolved: {unresolved}, other provider: {other}, needs review: {review}").format(
            **counts, unresolved=unresolved.count(), other=other, review=review))
