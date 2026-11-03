"""Order rules (G§7.8, G§7.13): money, status, progress, attention. Views and admin call these;
money and status fields are never edited anywhere else."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from pricing.discounts import round2

from .models import CourierFeeProposal, Order, OrderItem, OrderItemEvent, OrderSupportMessage
from .notifications import notify_after_commit
from .texts import render_plural, render_text

PROGRESS = {
    OrderItem.STATUS_CREATED: 0, OrderItem.STATUS_PAYMENT_CONFIRMED: 1, OrderItem.STATUS_CHECKING: 1,
    OrderItem.STATUS_ACTION_REQUIRED: 1, OrderItem.STATUS_PURCHASED: 2, OrderItem.STATUS_RECEIVED_USA: 3,
    OrderItem.STATUS_SHIPPED_TO_GEORGIA: 4, OrderItem.STATUS_RECEIVED_GEORGIA: 5,
    OrderItem.STATUS_READY_FOR_PICKUP: 6, OrderItem.STATUS_COMPLETED: 6,
}
CANCELLABLE_ITEM_STATUSES = {OrderItem.STATUS_CREATED, OrderItem.STATUS_PAYMENT_CONFIRMED, OrderItem.STATUS_CHECKING,
                             OrderItem.STATUS_ACTION_REQUIRED}
ZERO = Decimal("0.00")


class OperatorError(Exception):
    """An operator action that can't be applied (admin shows the message, demo endpoints a field error)."""

    def __init__(self, field, message):
        super().__init__(message)
        self.field, self.message = field, message


def assign_order_number(order):
    """LP-{year}-{pk:05d}, right after the insert, inside the creating transaction (unique by pk)."""
    order.order_number = f"LP-{order.created_at.year}-{order.pk:05d}"
    order.save(update_fields=["order_number"])


def _live_items(order):
    return order.items.exclude(item_status=OrderItem.STATUS_CANCELLED)


def paid_amount(order):
    """Paid payments minus their partial refunds (a fully refunded payment is no longer `paid`)."""
    from payments.models import Payment, Refund

    paid = Payment.objects.filter(order=order, status=Payment.STATUS_PAID)
    collected = paid.aggregate(s=Sum("amount"))["s"] or ZERO
    returned = Refund.objects.filter(payment__in=paid).aggregate(s=Sum("amount"))["s"] or ZERO
    return collected - returned


def recalculate_order(order):
    """G§7.8 money, in the order currency, over non-cancelled items. amount_due_now is set once at checkout."""
    totals = _live_items(order).aggregate(items=Sum(F("list_unit_price") * F("quantity")),
                                          discount=Sum(F("discount_unit_amount") * F("quantity")))
    order.items_total = totals["items"] or ZERO
    order.discount_total = totals["discount"] or ZERO
    order.total = order.items_total - order.discount_total + order.courier_fee
    order.amount_paid = paid_amount(order)
    order.balance_due = order.total - order.amount_paid
    order.save(update_fields=["items_total", "discount_total", "total", "amount_paid", "balance_due", "updated_at"])
    return order


def sync_order_status(order):
    """Status from items and courier fee. Never leaves payment_pending (only mark_payment does) and never
    changes completed/cancelled."""
    if order.status in (Order.STATUS_PAYMENT_PENDING, Order.STATUS_COMPLETED, Order.STATUS_CANCELLED):
        return order
    statuses = list(_live_items(order).values_list("item_status", "action_required"))
    if statuses and all(s == OrderItem.STATUS_COMPLETED for s, _ in statuses):
        new = Order.STATUS_COMPLETED
    elif order.courier_fee_action_required or any(a or s == OrderItem.STATUS_ACTION_REQUIRED for s, a in statuses):
        new = Order.STATUS_ACTION_REQUIRED
    else:
        new = Order.STATUS_PROCESSING
    if new != order.status:
        order.status = new
        order.save(update_fields=["status", "updated_at"])
    return order


def create_item_event(item, event_type, *, text_code="", params=None, message="", old=None, new=None,
                      actor=OrderItemEvent.ACTOR_SYSTEM, actor_name="", visible=False):
    return OrderItemEvent.objects.create(item=item, event_type=event_type, text_code=text_code,
                                         text_params=params or {}, message=message, old_value=old, new_value=new,
                                         actor_type=actor, actor_name=actor_name, visible_to_customer=visible)


def payment_status(order):
    if (order.status == Order.STATUS_CANCELLED and order.amount_paid > 0) or order.balance_due < 0:
        return "refund_pending"
    if order.amount_paid <= 0:
        return "unpaid"
    return "paid" if order.balance_due == 0 else "partially_paid"


def _loaded_live_items(order):
    """Non-cancelled items via order.items.all(), so a prefetched order costs no extra query (LP-BE-801)."""
    return [i for i in order.items.all() if i.item_status != OrderItem.STATUS_CANCELLED]


def progress(order):
    steps = [PROGRESS[i.item_status] for i in _loaded_live_items(order)]
    return {"step": min(steps) if steps else 0, "of": 6}


def can_cancel(order):
    if order.status not in Order.OPEN_STATUSES:
        return False
    return all(i.item_status in CANCELLABLE_ITEM_STATUSES for i in _loaded_live_items(order))


def _needs_decision(item):
    return item.action_required or item.item_status == OrderItem.STATUS_ACTION_REQUIRED


def attention(order):
    """The single most important banner (G§7.8), in the request language; None when nothing to do."""
    if order.status == Order.STATUS_CANCELLED:
        return None
    items = _loaded_live_items(order)
    pending = sum(1 for i in items if _needs_decision(i))
    if pending:
        return {"kind": "action_required", "message": render_plural("attention_action_required", pending)}
    if order.courier_fee_action_required:
        return {"kind": "courier_fee", "message": render_text("attention_courier_fee")}
    if order.balance_due > 0:
        return {"kind": "balance_due",
                "message": render_text("attention_balance_due", amount=order.balance_due,
                                       currency=order.currency)}
    if any(i.item_status == OrderItem.STATUS_READY_FOR_PICKUP for i in items):
        return {"kind": "ready_for_pickup", "message": render_text("attention_ready_for_pickup")}
    return None


def _str(value):
    return None if value is None else (f"{value:.2f}" if isinstance(value, Decimal) else value)


def action_change(item):
    """G§7.9 (old, new) for the pending action, as shown to the customer."""
    t = item.action_type
    if t == "price_change":
        return {"unit_price": item.unit_price}, {"unit_price": item.proposed_unit_price}
    if t == "eta_change":
        return ({"eta_min_days": item.eta_min_days, "eta_max_days": item.eta_max_days},
                {"eta_min_days": item.proposed_eta_min_days, "eta_max_days": item.proposed_eta_max_days})
    if t == "alternative_required":
        old, new = ({"part_number": item.part_number, "name": item.name},
                    {"part_number": item.proposed_part_number, "name": item.proposed_name})
        if item.proposed_unit_price is not None:
            old["unit_price"], new["unit_price"] = item.unit_price, item.proposed_unit_price
        return old, new
    if t == "fitment_issue":
        return {"vin": item.vin}, {}
    if t == "weight_change":
        return ({"weight_kg": item.weight_kg, "unit_price": item.unit_price},
                {"weight_kg": item.proposed_weight_kg, "unit_price": item.proposed_unit_price})
    return {}, {}


def decisions(item):
    """acknowledge only for a weight change after purchase (notice only); accept/cancel otherwise."""
    if not item.action_required:
        return []
    if item.action_type == "weight_change" and item.purchased_at is not None:
        return ["acknowledge"]
    return ["accept", "cancel"]


def action_data(item):
    if not item.action_required:
        return None
    old, new = action_change(item)
    return {"type": item.action_type, "message": item.action_message,
            "old": {k: _str(v) for k, v in old.items()}, "new": {k: _str(v) for k, v in new.items()},
            "decisions": decisions(item)}


def pending_proposal(order):
    """The pending courier fee proposal (from prefetched data when available)."""
    return next((p for p in order.courier_fee_proposals.all() if p.status == CourierFeeProposal.STATUS_PENDING),
                None)


def proposed_courier_fee(order):
    """The amount the customer is asked to accept, in the order currency, frozen in the proposal (D21)."""
    proposal = pending_proposal(order)
    return proposal.amount if proposal else None


@transaction.atomic
def propose_courier_fee(order_id, fee_gel, message, actor=None):
    """Admin/demo (G§7.14 step 2): a new proposal version. GEL orders: the GEL fee as is; USD orders: converted
    with the current NBG rate, which is frozen with the proposal (D21). A pending proposal is superseded;
    accepted ones stay as they are. Raises RateUnavailable (nothing changes) / OperatorError."""
    from pricing.rates import get_usd_rate

    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status not in Order.OPEN_STATUSES or order.delivery_method != "delivery":
        raise OperatorError("fee_gel", _("Courier fees can be proposed for open delivery orders only."))
    if fee_gel is None or fee_gel < 0:
        raise OperatorError("fee_gel", _("The courier fee must be zero or more."))
    rate = None if order.currency == "GEL" else get_usd_rate()
    amount = round2(fee_gel) if rate is None else round2(fee_gel / rate)
    order.courier_fee_proposals.filter(status=CourierFeeProposal.STATUS_PENDING).update(
        status=CourierFeeProposal.STATUS_SUPERSEDED, answered_at=timezone.now())
    proposal = CourierFeeProposal.objects.create(order=order, fee_gel=round2(fee_gel), exchange_rate=rate,
                                                 amount=amount, currency=order.currency, message=message,
                                                 proposed_by=actor)
    order.proposed_courier_fee_gel, order.courier_fee_message = proposal.fee_gel, message
    order.courier_fee_action_required = True
    order.save(update_fields=["proposed_courier_fee_gel", "courier_fee_message", "courier_fee_action_required",
                              "updated_at"])
    sync_order_status(order)
    notify_after_commit(order.customer, "courier_fee_proposed", order=order, params={"proposal_id": proposal.pk})
    return proposal


PROPOSED_CLEAR = {"action_required": False, "action_type": "", "action_message": "", "proposed_part_number": "",
                  "proposed_name": "", "proposed_unit_price": None, "proposed_eta_min_days": None,
                  "proposed_eta_max_days": None, "proposed_weight_kg": None}


def _jsonable(values):
    return {k: _str(v) for k, v in values.items()}


def _set_price(item, unit_price):
    item.unit_price = unit_price
    item.list_unit_price = unit_price + item.discount_unit_amount


def _apply_change(item):
    """G§7.9 "on accept/acknowledge" effects (order currency; the checkout USD snapshot stays as it was)."""
    t = item.action_type
    if t == "price_change" or (t == "alternative_required" and item.proposed_unit_price is not None):
        _set_price(item, item.proposed_unit_price)
    if t == "eta_change":
        today = timezone.localdate()
        item.eta_min_days, item.eta_max_days = item.proposed_eta_min_days, item.proposed_eta_max_days
        item.expected_arrival_from = today + timedelta(days=item.eta_min_days)
        item.expected_arrival_to = today + timedelta(days=item.eta_max_days)
    if t == "alternative_required":
        item.part_number, item.name = item.proposed_part_number, item.proposed_name
    if t == "weight_change":
        item.weight_kg, item.weight_source = item.proposed_weight_kg, "operator"
        _set_price(item, item.proposed_unit_price)


def lock_item(customer, item_id):
    """(order, item, open payments) of the customer, row-locked: open payments, then the order, then the item
    (the lock order used everywhere)."""
    from api.errors import ApiError

    if not OrderItem.objects.filter(pk=item_id, order__customer=customer).exists():
        raise ApiError("not_found", status=404)
    return _lock_for_operator(item_id, with_open_payments=True)


@transaction.atomic
def decide(customer, item_id, decision, actor_name=""):
    """POST /api/orders/items/<id>/decision/ (G§7.9). A decision not offered for the pending action (or no
    action at all, e.g. a repeated submit) -> 409 no_pending_action. -> the order."""
    from api.errors import ApiError

    order, item, open_payments = lock_item(customer, item_id)
    if decision not in decisions(item) or order.status == Order.STATUS_CANCELLED:
        raise ApiError("no_pending_action", status=409)
    old, new = action_change(item)
    before = {"action_type": item.action_type, "item_status": item.item_status, **_jsonable(old)}
    if decision == "cancel":
        item.item_status = OrderItem.STATUS_CANCELLED
        code = "item_cancelled"
    else:
        _apply_change(item)
        if decision == "accept":
            item.item_status = OrderItem.STATUS_CHECKING
        code = "action_accepted" if decision == "accept" else "notice_acknowledged"
    for field, value in PROPOSED_CLEAR.items():
        setattr(item, field, value)
    item.save()
    create_item_event(item, f"decision_{decision}", text_code=code, old=before,
                      new={"item_status": item.item_status, **_jsonable(new)}, actor=OrderItemEvent.ACTOR_CUSTOMER,
                      actor_name=actor_name, visible=True)
    recalculate_order(order)
    if decision == "cancel" and not _live_items(order).exists():
        close_cancelled_order(order, open_payments, "", item=item, actor=OrderItemEvent.ACTOR_CUSTOMER,
                              actor_name=actor_name)
        return order
    sync_order_status(order)
    return order


@transaction.atomic
def cancel_order(order_id, reason, *, actor, actor_name="", operator=False):
    """G§7.13. Customers: open order with nothing purchased (else 409 not_cancellable). Operators (owner
    decision D19): any open order, reason required. Open payments expire; money already collected stays
    recorded, so amount_paid > 0 shows as refund_pending (refunded manually, never automatically).
    Locks the open payments, then the order (the order mark_payment uses). -> the order."""
    from api.errors import ApiError
    from payments.models import Payment

    if operator and not reason.strip():
        raise OperatorError("reason", _("A cancellation reason is required."))
    open_payments = list(Payment.objects.select_for_update().filter(order_id=order_id,
                                                                     status__in=Payment.OPEN_STATUSES))
    order = Order.objects.select_for_update().get(pk=order_id)
    allowed = order.status in Order.OPEN_STATUSES if operator else can_cancel(order)
    if not allowed:
        raise ApiError("not_cancellable", status=409)
    item = None
    for item in _live_items(order).select_for_update():
        old = item.item_status
        item.item_status = OrderItem.STATUS_CANCELLED
        for field, value in PROPOSED_CLEAR.items():
            setattr(item, field, value)
        item.save()
        create_item_event(item, "status_changed", text_code="status_cancelled", message=reason if operator else "",
                          old={"item_status": old}, new={"item_status": item.item_status, "reason": reason},
                          actor=actor, actor_name=actor_name, visible=True)
    item = item or order.items.order_by("-id").first()
    close_cancelled_order(order, open_payments, reason, item=item, actor=actor, actor_name=actor_name)
    return order


def close_cancelled_order(order, open_payments, reason, *, item, actor, actor_name=""):
    """The terminal step of every order cancellation (D22): the whole order, or its last active item.
    The caller holds the locks (open payments, then order). Open payments expire, a pending courier
    proposal is cancelled and a confirmed courier fee is released (nothing will be delivered); collected
    money stays recorded, so the order owes back exactly what was collected (refund_pending; refunded
    manually). The before/after state is kept in an internal event on `item`."""
    from payments.models import Payment
    from payments.services import mark_payment

    before = {"order_status": order.status, "courier_fee": _str(order.courier_fee),
              "amount_paid": _str(order.amount_paid), "open_payments": [p.pk for p in open_payments]}
    order.status, order.cancel_reason = Order.STATUS_CANCELLED, reason
    order.courier_fee, order.courier_fee_action_required, order.proposed_courier_fee_gel = ZERO, False, None
    order.save()
    order.courier_fee_proposals.filter(status=CourierFeeProposal.STATUS_PENDING).update(
        status=CourierFeeProposal.STATUS_CANCELLED, answered_at=timezone.now())
    for payment in open_payments:
        mark_payment(payment, Payment.STATUS_EXPIRED, payload={"expired_by": "order_cancelled"})
    recalculate_order(order)
    create_item_event(item, "order_cancelled", text_code="order_cancelled", message=reason, old=before,
                      new={"order_status": order.status, "courier_fee": _str(order.courier_fee),
                           "amount_paid": _str(order.amount_paid), "balance_due": _str(order.balance_due),
                           "reason": reason}, actor=actor, actor_name=actor_name)
    order.cancelled_at = timezone.now()  # after the item events: the timeline ends with "cancelled"
    order.save(update_fields=["cancelled_at", "updated_at"])
    if actor == OrderItemEvent.ACTOR_OPERATOR:  # the customer's own cancellation needs no message
        notify_after_commit(order.customer, "status_update", order=order, params={"order_status": order.status})


@transaction.atomic
def respond_courier_fee(order_id, accept):
    """G§7.14 accept/decline of the pending proposal; nothing pending -> 409 no_pending_action.
    Accept: the proposal's frozen amount becomes courier_fee (a separate money line; item prices and the
    order rate are untouched). Decline: pickup at the first active point and no courier fee. The answer is
    kept on the proposal and as a system support message (code + params). -> the order."""
    from api.errors import ApiError
    from pricing.models import PickupPoint

    order = Order.objects.select_for_update().get(pk=order_id)
    proposal = order.courier_fee_proposals.select_for_update().filter(
        status=CourierFeeProposal.STATUS_PENDING).first()
    if proposal is None or not order.courier_fee_action_required or order.status not in Order.OPEN_STATUSES:
        raise ApiError("no_pending_action", status=409)
    now = timezone.now()
    order.courier_fee_action_required = False
    if accept:
        order.courier_fee, order.courier_fee_confirmed_at = proposal.amount, now
        proposal.status, code = CourierFeeProposal.STATUS_ACCEPTED, "courier_fee_accepted"
    else:
        order.courier_fee, order.courier_fee_declined_at = ZERO, now
        order.delivery_method, order.pickup_point = "pickup", PickupPoint.objects.filter(is_active=True).first()
        proposal.status, code = CourierFeeProposal.STATUS_DECLINED, "courier_fee_declined"
    proposal.answered_at = now
    proposal.save(update_fields=["status", "answered_at"])
    order.proposed_courier_fee_gel = None
    order.save()
    OrderSupportMessage.objects.create(
        order=order, sender_type=OrderSupportMessage.SENDER_SYSTEM, text_code=code, is_read_by_customer=True,
        text_params={"amount": f"{proposal.amount:.2f}", "currency": proposal.currency, "proposal_id": proposal.pk})
    recalculate_order(order)
    sync_order_status(order)
    return order


ACTION_TYPES = ("price_change", "eta_change", "alternative_required", "fitment_issue", "weight_change", "other")
OPERATOR_STATUSES = (OrderItem.STATUS_CHECKING, OrderItem.STATUS_PURCHASED, OrderItem.STATUS_RECEIVED_USA,
                     OrderItem.STATUS_SHIPPED_TO_GEORGIA, OrderItem.STATUS_RECEIVED_GEORGIA,
                     OrderItem.STATUS_READY_FOR_PICKUP, OrderItem.STATUS_COMPLETED, OrderItem.STATUS_CANCELLED)
PAID_STATUSES_ONLY = (OrderItem.STATUS_READY_FOR_PICKUP, OrderItem.STATUS_COMPLETED)


def _lock_for_operator(item_id, with_open_payments=False):
    """(order, item[, open payments]) row-locked: open payments, then the order, then the item (the lock
    order mark_payment and cancel_order use)."""
    from payments.models import Payment

    order_id = OrderItem.objects.values_list("order_id", flat=True).get(pk=item_id)
    payments = list(Payment.objects.select_for_update().filter(order_id=order_id, status__in=Payment.OPEN_STATUSES)) \
        if with_open_payments else []
    order = Order.objects.select_for_update().get(pk=order_id)
    item = OrderItem.objects.select_for_update().get(pk=item_id)
    return (order, item, payments) if with_open_payments else (order, item)


def _positive(field, value):
    if value is None or value <= 0:
        raise OperatorError(field, _("Enter a value greater than zero."))


@transaction.atomic
def request_item_action(item_id, action_type, message="", *, actor_name="", proposed_unit_price=None,
                        proposed_eta_min_days=None, proposed_eta_max_days=None, proposed_part_number="",
                        proposed_name="", proposed_weight_kg=None):
    """Operator asks the customer to decide (G§7.9). Proposed prices are in the order currency. A weight change
    after purchase is a notice (acknowledge only; the item keeps its status). -> the item."""
    order, item = _lock_for_operator(item_id)
    if action_type not in ACTION_TYPES:
        raise OperatorError("action_type", _("Unknown action type."))
    if order.status not in (Order.STATUS_PROCESSING, Order.STATUS_ACTION_REQUIRED):
        raise OperatorError("action_type", _("Customer decisions can be requested on paid, open orders only."))
    if item.item_status in (OrderItem.STATUS_CANCELLED, OrderItem.STATUS_COMPLETED):
        raise OperatorError("action_type", _("This item is already cancelled or completed."))
    if action_type in ("price_change", "weight_change") or (action_type == "alternative_required"
                                                            and proposed_unit_price is not None):
        _positive("proposed_unit_price", proposed_unit_price)
    if action_type == "price_change" and proposed_unit_price == item.unit_price:
        raise OperatorError("proposed_unit_price", _("The proposed price equals the current price."))
    if action_type == "eta_change":
        _positive("proposed_eta_min_days", proposed_eta_min_days)
        _positive("proposed_eta_max_days", proposed_eta_max_days)
        if proposed_eta_min_days > proposed_eta_max_days:
            raise OperatorError("proposed_eta_max_days", _("The maximum can't be less than the minimum."))
        if (proposed_eta_min_days, proposed_eta_max_days) == (item.eta_min_days, item.eta_max_days):
            raise OperatorError("proposed_eta_min_days", _("The proposed delivery time equals the current one."))
    if action_type == "alternative_required":
        if not proposed_part_number.strip() or not proposed_name.strip():
            raise OperatorError("proposed_part_number", _("Enter the alternative part number and name."))
        if proposed_part_number.strip().upper() == item.part_number.upper():
            raise OperatorError("proposed_part_number", _("The alternative is the same part number."))
    if action_type == "weight_change":
        _positive("proposed_weight_kg", proposed_weight_kg)
    if action_type in ("fitment_issue", "other") and not message.strip():
        raise OperatorError("message", _("Write a message for the customer."))

    before = {"item_status": item.item_status, "action_type": item.action_type}
    for field, value in PROPOSED_CLEAR.items():
        setattr(item, field, value)
    item.action_required, item.action_type, item.action_message = True, action_type, message.strip()
    item.proposed_unit_price, item.proposed_part_number = proposed_unit_price, proposed_part_number.strip()
    item.proposed_name, item.proposed_weight_kg = proposed_name.strip(), proposed_weight_kg
    item.proposed_eta_min_days, item.proposed_eta_max_days = proposed_eta_min_days, proposed_eta_max_days
    if not (action_type == "weight_change" and item.purchased_at):
        item.item_status = OrderItem.STATUS_ACTION_REQUIRED
    item.save()
    old, new = action_change(item)
    params = {"amount": _str(proposed_unit_price) or "0", "currency": order.currency,
              "part_number": item.proposed_part_number, "weight_kg": _str(proposed_weight_kg),
              "eta_min_days": proposed_eta_min_days, "eta_max_days": proposed_eta_max_days}
    event = create_item_event(
        item, "action_requested", text_code=f"action_requested_{action_type}", params=params,
        message=item.action_message, old={**before, **_jsonable(old)},
        new={"item_status": item.item_status, "action_type": action_type, **_jsonable(new)},
        actor=OrderItemEvent.ACTOR_OPERATOR, actor_name=actor_name, visible=True)
    sync_order_status(order)
    notify_after_commit(order.customer, "action_required", order=order, item=item,
                        params={"event_id": event.pk})
    return item


@transaction.atomic
def set_item_status(item_id, status, *, actor_name="", reason="", bypass_balance_guard=False):
    """Operator status change (G§7.8). Balance guard: no ready_for_pickup/completed while balance_due > 0
    (only demo endpoints bypass it). `purchased` stamps purchased_at. Cancelling needs a reason and may
    happen at any stage (D19); the money is recalculated (overpaid -> refund_pending). A pending customer
    action is closed. On an order still waiting for its first payment, a cancelled item also re-sets
    amount_due_now for the new total and expires the open payments (their amounts are out of date; the
    customer starts a new payment). -> True when changed, False when the item already had that status."""
    order, item, open_payments = _lock_for_operator(item_id, with_open_payments=True)
    reason = reason.strip()
    if status not in OPERATOR_STATUSES:
        raise OperatorError("item_status", _("This status can't be set here."))
    if order.status in (Order.STATUS_CANCELLED, Order.STATUS_COMPLETED) or \
            item.item_status == OrderItem.STATUS_CANCELLED:
        raise OperatorError("item_status", _("The order or item is already closed."))
    if order.status == Order.STATUS_PAYMENT_PENDING and status != OrderItem.STATUS_CANCELLED:
        raise OperatorError("item_status", _("The order is not paid yet."))
    if item.item_status == status:
        return False
    if status in PAID_STATUSES_ONLY and order.balance_due > 0 and not bypass_balance_guard:
        raise OperatorError("item_status", _("The order still has a balance due."))
    if status == OrderItem.STATUS_CANCELLED and not reason:
        raise OperatorError("reason", _("A cancellation reason is required."))
    before = {"item_status": item.item_status, "action_type": item.action_type}
    if status == OrderItem.STATUS_PURCHASED and item.purchased_at is None:
        item.purchased_at = timezone.now()
    for field, value in PROPOSED_CLEAR.items():
        setattr(item, field, value)
    item.item_status = status
    item.save()
    create_item_event(item, "status_changed", text_code=f"status_{status}", message=reason, old=before,
                      new={"item_status": status, **({"reason": reason} if reason else {})},
                      actor=OrderItemEvent.ACTOR_OPERATOR, actor_name=actor_name, visible=True)
    recalculate_order(order)
    if status == OrderItem.STATUS_CANCELLED and not _live_items(order).exists():
        close_cancelled_order(order, open_payments, reason, item=item, actor=OrderItemEvent.ACTOR_OPERATOR,
                              actor_name=actor_name)
        return True
    if status == OrderItem.STATUS_CANCELLED and order.status == Order.STATUS_PAYMENT_PENDING:
        _reset_amount_due_now(order, open_payments)
    sync_order_status(order)
    notify_after_commit(order.customer, "status_update", order=order, item=item, params={"item_status": status})
    return True


def _reset_amount_due_now(order, open_payments):
    """Nothing is paid yet: what is due now follows the new total with the terms frozen at checkout."""
    from payments.services import mark_payment

    order.amount_due_now = round2(order.total * order.split_upfront_percent / 100) \
        if order.payment_option == "split_70_30" and order.split_upfront_percent else order.total
    order.save(update_fields=["amount_due_now", "updated_at"])
    for payment in open_payments:
        mark_payment(payment, "expired", payload={"expired_by": "order_total_changed"})


@transaction.atomic
def reply_to_order(order, message, sender_name):
    """Operator message on an order (G§7.16): visible to the customer, customer messages marked read by the
    operator, then notify(support_reply). -> the message."""
    reply = OrderSupportMessage.objects.create(order=order, sender_type=OrderSupportMessage.SENDER_OPERATOR,
                                               sender_name=sender_name, message=message, is_read_by_operator=True)
    order.support_messages.filter(sender_type=OrderSupportMessage.SENDER_CUSTOMER).update(is_read_by_operator=True)
    notify_after_commit(order.customer, "support_reply", order=order, support_message=reply,
                        message=message)
    return reply
