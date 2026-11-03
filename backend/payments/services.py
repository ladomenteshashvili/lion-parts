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
