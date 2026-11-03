"""Payment state (G§7.10). mark_payment is the only code that changes Payment.status."""

from django.db import transaction
from django.utils import timezone

from orders.models import Order, OrderItem
from orders.services import create_item_event, recalculate_order, sync_order_status

from .models import Payment

FINAL_STATUSES = {Payment.STATUS_REFUNDED}


@transaction.atomic
def mark_payment(payment, status, payload=None, confirmed_by=None):
    """Idempotent and lock-protected: the same status twice changes nothing; `paid` is never
    downgraded (only refunded); `refunded` is final and only follows `paid`. The first paid initial
    payment moves the order to processing and its items to payment_confirmed."""
    if status not in dict(Payment.STATUSES):
        raise ValueError(f"unknown payment status {status!r}")
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if (payment.status == status or payment.status in FINAL_STATUSES
            or (payment.status == Payment.STATUS_PAID and status != Payment.STATUS_REFUNDED)
            or (status == Payment.STATUS_REFUNDED and payment.status != Payment.STATUS_PAID)):
        return payment
    payment.status = status
    if payload is not None:
        payment.provider_payload = payload
    if status == Payment.STATUS_PAID:
        payment.paid_at = timezone.now()
        payment.confirmed_by = confirmed_by
    payment.save()

    order = Order.objects.select_for_update().get(pk=payment.order_id)
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
    # LP-BE-902: transaction.on_commit(notify(order.customer, "payment_update", order=order))
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
    out). A PaymentInitError marks the payment failed. -> True when a checkout_url was obtained."""
    from .providers import PaymentInitError, get_provider

    try:
        url = get_provider().start(payment)
    except PaymentInitError:
        mark_payment(payment, Payment.STATUS_FAILED)
        return False
    Payment.objects.filter(pk=payment.pk).update(checkout_url=url, updated_at=timezone.now())
    mark_payment(payment, Payment.STATUS_PENDING)
    return True


PAYMENT_IN_PROGRESS_MINUTES = 30
MANUAL_PROVIDERS = ("invoice", "manual")


def open_payment(order):
    """The payment that still blocks a new one: any pending invoice/manual payment (it never expires by
    itself: the customer may still be paying it), or a card payment pending for < 30 min."""
    from datetime import timedelta

    from django.db.models import Q

    recent = timezone.now() - timedelta(minutes=PAYMENT_IN_PROGRESS_MINUTES)
    return order.payments.filter(Q(provider__in=MANUAL_PROVIDERS) | Q(created_at__gt=recent),
                                 status=Payment.STATUS_PENDING).order_by("-created_at").first()


def request_payment(order_id, method):
    """POST /api/orders/<n>/payments/ under an order row lock (no duplicate payments in parallel).
    While the order still waits for its first payment, the retry is an `initial` payment for
    amount_due_now (so the order can move to processing); afterwards a `balance` payment for balance_due.
    Raises nothing_to_pay / payment_in_progress (+payment)."""
    from api.errors import ApiError
    from orders.serializers import payment_data

    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
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
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        if payment.provider not in MANUAL_PROVIDERS:
            return "not_manual"
        if payment.status != Payment.STATUS_PENDING:
            return "not_pending"
        recalculate_order(order)
        if order.status == Order.STATUS_CANCELLED or payment.amount > order.balance_due:
            return "overpayment"
        mark_payment(payment, Payment.STATUS_PAID, confirmed_by=user)
    return None
