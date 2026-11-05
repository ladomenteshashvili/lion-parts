"""Payment state (G§7.10). mark_payment is the only code that changes Payment.status."""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from orders.models import Order, OrderItem
from orders.services import create_item_event, recalculate_order, sync_order_status

from .models import Payment

logger = logging.getLogger(__name__)
FINAL_STATUSES = {Payment.STATUS_REFUNDED}
PROVIDER_CLOSE_MARGIN = timedelta(minutes=15)


@transaction.atomic
def mark_payment(payment, status, payload=None, confirmed_by=None, provider_conflict=False):
    """Idempotent and lock-protected: the same status twice changes nothing; `paid` is never
    downgraded (only refunded); `refunded` is final and only follows `paid`; `pending` only follows `created`
    and `unknown` only follows `pending`, so a late answer can't reopen a closed payment or hide its result.
    provider_conflict is for recheck_payment alone: failed/expired -> unknown when the provider said paid and could
    not confirm it (owner decision, LPK-180). The first paid initial payment moves the order to processing and its
    items to payment_confirmed."""
    if status not in dict(Payment.STATUSES):
        raise ValueError(f"unknown payment status {status!r}")
    payment = Payment.objects.select_for_update(no_key=True).get(pk=payment.pk)
    unknown_from = (Payment.STATUS_PENDING, Payment.STATUS_FAILED, Payment.STATUS_EXPIRED) if provider_conflict \
        else (Payment.STATUS_PENDING,)
    if (payment.status == status or payment.status in FINAL_STATUSES
            or (payment.status == Payment.STATUS_PAID and status != Payment.STATUS_REFUNDED)
            or (status == Payment.STATUS_REFUNDED and payment.status != Payment.STATUS_PAID)
            or (status == Payment.STATUS_PENDING and payment.status != Payment.STATUS_CREATED)
            or (status == Payment.STATUS_UNKNOWN and payment.status not in unknown_from)):
        return payment
    payment.status = status
    if payload is not None:  # merged: who re-checked or closed it stays on record through later changes
        payment.provider_payload = {**payment.provider_payload, **payload}
    if status == Payment.STATUS_PAID:
        payment.paid_at = timezone.now()
        payment.confirmed_by = confirmed_by
    payment.save()

    order = Order.objects.select_for_update(no_key=True).get(pk=payment.order_id)
    recalculate_order(order)
    if status == Payment.STATUS_PAID and payment.kind == Payment.KIND_INITIAL \
            and order.status == Order.STATUS_PAYMENT_PENDING:
        order.status = Order.STATUS_PROCESSING
        order.save(update_fields=["status", "updated_at"])
        for item in order.items.filter(item_status=OrderItem.STATUS_CREATED):
            item.item_status = OrderItem.STATUS_PAYMENT_CONFIRMED
            item.save(update_fields=["item_status", "updated_at"])
            create_item_event(item, "status_changed", text_code="payment_confirmed", visible=True,
                              params={"amount": f"{payment.amount:.2f}", "currency": payment.currency})
    sync_order_status(order)
    if status in (Payment.STATUS_PAID, Payment.STATUS_FAILED):
        from orders.notifications import notify_after_commit

        notify_after_commit(order.customer, "payment_update", order=order,
                            params={"payment_id": payment.pk, "status": status})
    return payment


def create_payment(order, method, kind=Payment.KIND_INITIAL, amount=None):
    """A payment for exactly the order's frozen amount in the order currency (D16). Card -> the configured
    provider (status created, started after commit); invoice -> pending, paid when an operator confirms."""
    from .providers import get_provider

    amount = amount if amount is not None else (order.amount_due_now if kind == Payment.KIND_INITIAL
                                                else order.balance_due)
    card = method == "card"
    payment = Payment.objects.create(
        order=order, kind=kind, provider=get_provider().name if card else "invoice", amount=amount,
        currency=order.currency, status=Payment.STATUS_CREATED if card else Payment.STATUS_PENDING)
    payment.provider_order_id = f"{order.order_number}-{payment.pk}"
    payment.save(update_fields=["provider_order_id", "updated_at"])
    return payment


def start_payment(payment):
    """Open the provider's payment page. Never inside a DB transaction (no row locks held while calling
    out), so the payment may have changed when the provider answers: only a still open payment takes the
    answer. A PaymentInitError marks it failed. -> False when there is no page to open and nothing was paid."""
    from .providers import PaymentInitError, get_provider

    provider = get_provider()
    try:
        url = provider.start(payment)
    except PaymentInitError:
        url = None
    with transaction.atomic():
        current = Payment.objects.select_for_update(no_key=True).get(pk=payment.pk)
        if current.status not in Payment.OPEN_STATUSES:
            return current.status == Payment.STATUS_PAID
        if url is None:
            mark_payment(current, Payment.STATUS_FAILED)
            return False
        now = timezone.now()
        session = {"checkout_url": url, "updated_at": now}
        if provider.checkout_lifetime:
            expires = now + provider.checkout_lifetime
            session["checkout_expires_at"] = max(current.checkout_expires_at or expires, expires)
        Payment.objects.filter(pk=current.pk).update(**session)
        mark_payment(current, Payment.STATUS_PENDING)
    return True


def _close_if_proven(updated, status, provider, queried_at=None):
    """Record provider evidence that a session is closed, in the caller's transaction, after mark_payment and holding
    the payment's row lock. Evidence is a closing result the provider has confirmed final for that status, or a query of
    its current state started after the session's window. A callback can't use the window: its delivery can be retried
    long after the event. No decision reads this evidence yet. -> the payment."""
    closing = status in (Payment.STATUS_FAILED, Payment.STATUS_EXPIRED)
    if closing and updated.status in (Payment.STATUS_PAID, Payment.STATUS_REFUNDED):
        logger.warning("Payment %s: provider sent %s after %s", updated.pk, status, updated.status)
    if status == Payment.STATUS_PAID and updated.provider_closed_at:
        logger.error("Payment %s: paid after its session was recorded as closed", updated.pk)
    if (not closing or updated.status != status or updated.provider_closed_at or not updated.checkout_url
            or updated.provider != provider.name):
        return updated
    final = status in provider.final_statuses
    after_window = (queried_at is not None and updated.checkout_expires_at is not None
                    and queried_at >= updated.checkout_expires_at + PROVIDER_CLOSE_MARGIN)
    if final or after_window:
        now = timezone.now()
        closure = "final_result" if final else "query_after_window"
        Payment.objects.filter(pk=updated.pk).update(
            provider_closed_at=now, updated_at=now, provider_payload={**updated.provider_payload, "closure": closure})
        updated.refresh_from_db()
    return updated


@transaction.atomic
def record_provider_result(payment, status, provider, payload=None):
    """A verified result pushed by the provider: its status and any closure evidence commit together. -> the payment."""
    current = Payment.objects.select_for_update(no_key=True).get(pk=payment.pk)
    return _close_if_proven(mark_payment(current, status, payload=payload), status, provider)


def _apply_if_unchanged(payment_pk, seen, status, audit, provider=None, queried_at=None):
    """mark_payment under the row lock, only if the payment is still `seen`. Closure evidence is considered only when
    provider is given. -> None when applied, else the payment as it is now."""
    with transaction.atomic():
        current = Payment.objects.select_for_update(no_key=True).get(pk=payment_pk)
        if current.status != seen:
            return current
        updated = mark_payment(current, status, payload={"reconciled": status, **audit})
        if provider:
            _close_if_proven(updated, status, provider, queried_at)
    return None


def recheck_payment(payment, provider, checked_by=None):
    """Ask the provider about a pending/unknown card payment and apply its final answer. The provider is called
    outside any transaction (no row locks held). The answer counts only if the payment has not changed since it
    was read, so stale provider data never overwrites a newer result. Exception: "paid" against a payment closed
    meanwhile is never just dropped, because a closed payment lets the customer pay again. The provider is asked
    once more, after the closing result was committed: a final answer is applied, anything else moves the payment
    to `unknown` so that it blocks new payments until the provider confirms (owner decision, LPK-180). A result
    arriving during that repeat query is kept if it is paid or refunded, or if it closes the payment and the provider
    also answered failed/expired; otherwise the payment is held as `unknown` too. Raises what the first call raises,
    or ValueError on an invalid first answer; the payment then stays as it is.
    -> updated / pending / superseded / conflict (now unknown)."""
    seen = payment.status
    queried_at = timezone.now()  # before the call: the answer can't describe anything later than this
    status = provider.fetch_status(payment)
    if status == Payment.STATUS_PENDING:
        return "pending"
    if status not in (Payment.STATUS_PAID, Payment.STATUS_FAILED, Payment.STATUS_EXPIRED):
        raise ValueError("invalid provider status")  # the value itself is provider data: it is not logged
    audit = {"rechecked_by": checked_by.pk} if checked_by else {}
    current = _apply_if_unchanged(payment.pk, seen, status, audit, provider, queried_at)
    if current is None:
        return "updated"
    closed = (Payment.STATUS_FAILED, Payment.STATUS_EXPIRED)
    if status != Payment.STATUS_PAID or current.status not in closed:
        return "superseded"

    try:
        confirmed = provider.fetch_status(current)
    except Exception as error:
        logger.warning("Payment %s: repeat provider query failed: %s", current.pk, type(error).__name__)
        confirmed, second_answer = None, "error"
    else:  # the raw value is provider data: only its kind goes on record
        second_answer = confirmed if confirmed in (Payment.STATUS_PAID, Payment.STATUS_PENDING, *closed) else "invalid"

    audit = {**audit, "conflict_second_answer": second_answer}
    if confirmed in (Payment.STATUS_PAID, *closed):
        now = _apply_if_unchanged(current.pk, current.status, confirmed, {**audit, "conflict_with": current.status})
        if now is None:
            logger.warning("Payment %s: provider said paid, then %s, over %s", current.pk, confirmed, current.status)
            return "updated" if confirmed != current.status else "superseded"
        if confirmed in closed or now.status not in closed:
            return "superseded"  # a newer closing result the provider agrees with, or a settled one: it stands
    # unresolved evidence that money moved: hold it until the provider answers (owner decisions, LPK-180)
    with transaction.atomic():
        now = Payment.objects.select_for_update(no_key=True).get(pk=current.pk)
        if now.status not in closed:
            return "superseded"
        revoked = {"closure_revoked": True} if now.provider_closed_at else {}  # "paid" disproves that evidence
        mark_payment(now, Payment.STATUS_UNKNOWN, provider_conflict=True,
                     payload={"reconciled": Payment.STATUS_UNKNOWN, **audit, "conflict_with": now.status, **revoked})
        if revoked:
            Payment.objects.filter(pk=now.pk).update(provider_closed_at=None)
    logger.error("Payment %s: provider says paid but it was %s and %s on repeat; marked unknown", now.pk, now.status,
                 second_answer)
    return "conflict"


PAYMENT_IN_PROGRESS_MINUTES = 30
MANUAL_PROVIDERS = ("invoice", "manual")


def open_payment(order):
    """The payment that still blocks a new one: any pending or unknown payment whatever its age or provider
    (the customer may still be paying it, or the money may already have moved: a second payment could charge
    twice), a created invoice/manual payment, or a card payment created < 30 min ago (it is being started with
    the provider right now)."""
    from django.db.models import Q

    recent = timezone.now() - timedelta(minutes=PAYMENT_IN_PROGRESS_MINUTES)
    return order.payments.filter(
        Q(status__in=(Payment.STATUS_PENDING, Payment.STATUS_UNKNOWN))
        | Q(status=Payment.STATUS_CREATED) & (Q(provider__in=MANUAL_PROVIDERS) | Q(created_at__gt=recent))
    ).order_by("-created_at").first()


def request_payment(order_id, method):
    """POST /api/orders/<n>/payments/ under an order row lock (no duplicate payments in parallel).
    While the order still waits for its first payment, the retry is an `initial` payment for
    amount_due_now (so the order can move to processing); afterwards a `balance` payment for balance_due.
    Raises nothing_to_pay / payment_in_progress (+payment)."""
    from api.errors import ApiError
    from orders.serializers import payment_data

    with transaction.atomic():
        order = Order.objects.select_for_update(no_key=True).get(pk=order_id)
        if order.status == Order.STATUS_CANCELLED or order.balance_due <= 0:
            raise ApiError("nothing_to_pay", status=409)
        if blocking := open_payment(order):
            raise ApiError("payment_in_progress", status=409, payment=payment_data(blocking))
        initial = order.status == Order.STATUS_PAYMENT_PENDING
        return create_payment(order, method, Payment.KIND_INITIAL if initial else Payment.KIND_BALANCE,
                              amount=order.amount_due_now if initial else order.balance_due)


def confirm_manual_payment(payment, user):
    """Admin "payment received" for one pending invoice/manual payment (G§7.10). Refused when it would
    collect more than the order still owes (e.g. the order was meanwhile paid by card). Locks in the
    same order as mark_payment (payment, then order). -> None when confirmed, else a reason code:
    not_manual / not_pending / overpayment."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update(no_key=True).get(pk=payment.pk)
        order = Order.objects.select_for_update(no_key=True).get(pk=payment.order_id)
        if payment.provider not in MANUAL_PROVIDERS:
            return "not_manual"
        if payment.status != Payment.STATUS_PENDING:
            return "not_pending"
        recalculate_order(order)
        if order.status == Order.STATUS_CANCELLED or payment.amount > order.balance_due:
            return "overpayment"
        mark_payment(payment, Payment.STATUS_PAID, confirmed_by=user)
    return None


def refundable(payment):
    from django.db.models import Sum

    return payment.amount - (payment.refunds.aggregate(s=Sum("amount"))["s"] or 0)


def _lock_paid(order_id):
    """Paid payments then the order, row-locked (mark_payment's lock order); money recalculated."""
    payments = list(Payment.objects.select_for_update(no_key=True).filter(order_id=order_id, status=Payment.STATUS_PAID)
                    .order_by("-paid_at", "-id"))
    order = Order.objects.select_for_update(no_key=True).get(pk=order_id)
    recalculate_order(order)
    return payments, order


def record_refund(order_id, user, reason=""):
    """Admin "refund the amount owed" (D20): records refunds of exactly what the order owes back
    (-balance_due, e.g. after cancelling an item or the order), taken from the latest paid payments and
    never more than any payment still holds. Idempotent: nothing owed -> nothing recorded. -> amount."""
    from .models import Refund

    with transaction.atomic():
        payments, order = _lock_paid(order_id)
        owed, total = -order.balance_due, 0
        for payment in payments:
            take = min(refundable(payment), owed - total)
            if take > 0:
                Refund.objects.create(payment=payment, amount=take, reason=reason, created_by=user)
                total += take
        recalculate_order(order)
    return total


def refund_whole_payment(payment, user):
    """Admin "mark refunded": only when the whole remaining amount of this payment is owed back (D20).
    -> None when refunded, else a reason code: not_paid / exceeds_owed."""
    with transaction.atomic():
        payments, order = _lock_paid(payment.order_id)
        payment = next((p for p in payments if p.pk == payment.pk), None)
        if payment is None:
            return "not_paid"
        if refundable(payment) > -order.balance_due:
            return "exceeds_owed"
        mark_payment(payment, Payment.STATUS_REFUNDED, payload={**payment.provider_payload, "refunded_by": user.pk})
    return None
