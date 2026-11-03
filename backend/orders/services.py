"""Order rules (G§7.8, G§7.13): money, status, progress, attention. Views and admin call these;
money and status fields are never edited anywhere else."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from pricing.discounts import round2

from .models import Order, OrderItem, OrderItemEvent
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


def assign_order_number(order):
    """LP-{year}-{pk:05d}, right after the insert, inside the creating transaction (unique by pk)."""
    order.order_number = f"LP-{order.created_at.year}-{order.pk:05d}"
    order.save(update_fields=["order_number"])


def _live_items(order):
    return order.items.exclude(item_status=OrderItem.STATUS_CANCELLED)


def paid_amount(order):
    """Sum of paid payments (a refunded payment is no longer `paid`)."""
    payments = getattr(order, "payments", None)
    if payments is None:  # the payments app arrives with LP-BE-701
        return ZERO
    return payments.filter(status="paid").aggregate(s=Sum("amount"))["s"] or ZERO


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


def proposed_courier_fee(order):
    """The proposed GEL courier fee in the order currency at the order's frozen rate (G§7.14)."""
    gel = order.proposed_courier_fee_gel
    if gel is None or order.currency == "GEL":
        return gel
    return round2(gel / order.exchange_rate)


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
    """(order, item) of the customer, both row-locked, order first (the lock order used everywhere)."""
    from api.errors import ApiError

    order_id = OrderItem.objects.filter(pk=item_id, order__customer=customer).values_list("order_id", flat=True).first()
    if order_id is None:
        raise ApiError("not_found", status=404)
    order = Order.objects.select_for_update().get(pk=order_id)
    return order, OrderItem.objects.select_for_update().get(pk=item_id)


@transaction.atomic
def decide(customer, item_id, decision, actor_name=""):
    """POST /api/orders/items/<id>/decision/ (G§7.9). A decision not offered for the pending action (or no
    action at all, e.g. a repeated submit) -> 409 no_pending_action. -> the order."""
    from api.errors import ApiError

    order, item = lock_item(customer, item_id)
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
    sync_order_status(order)
    # LP-BE-902: notify operators? (not in G§7.11; the admin list shows the decision)
    return order
