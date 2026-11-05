"""Output shapes of G§8.0 Order / OrderItem / Payment (LP-BE-801). Everything is read from prefetched data
(order_queryset), so an order costs a constant number of queries whatever its item count."""

from django.db.models import Prefetch

from accounts.validators import normalize_part_number
from api.i18n import tr
from parts.models import PartImage
from parts.services import image_urls
from pricing.models import SiteSettings

from . import services
from .models import Order, OrderItemEvent
from .texts import render_text


def _money(value):
    return f"{value:.2f}" if value is not None else None


def order_queryset(detail=False):
    related = [Prefetch("items__events", queryset=OrderItemEvent.objects.filter(visible_to_customer=True))]
    if detail:
        related += ["payments", "support_messages"]
    return Order.objects.select_related("pickup_point").prefetch_related("items", "courier_fee_proposals", *related)


def _checkout_link(payment):
    """The provider page, only while paying it is still what the order needs. Hiding it does not close the page: a customer
    who already has it can still pay there, and that result is counted and flagged for review."""
    order = payment.order
    if (payment.status != "pending" or order.status == Order.STATUS_CANCELLED or order.balance_due <= 0
            or payment.amount > order.balance_due or (payment.kind == "initial" and order.amount_paid > 0)):
        return None
    return payment.checkout_url or None


def payment_data(payment):
    return {"id": payment.pk, "kind": payment.kind, "provider": payment.provider, "status": payment.status,
            "amount": _money(payment.amount), "currency": payment.currency,
            "checkout_url": _checkout_link(payment), "paid_at": payment.paid_at,
            "created_at": payment.created_at}


def _text(obj):
    """A system text rendered in the request language; operator/customer text as written."""
    return render_text(obj.text_code, **obj.text_params) if obj.text_code else obj.message


def item_data(request, item, images=None):
    return {
        "id": item.pk, "part_number": item.part_number, "name": item.name, "brand": item.brand,
        "condition": item.condition, "quantity": item.quantity, "vin": item.vin,
        "list_unit_price": _money(item.list_unit_price), "discount_unit_amount": _money(item.discount_unit_amount),
        "unit_price": _money(item.unit_price), "line_total": _money(item.line_total),
        "weight_kg": _money(item.weight_kg), "weight_source": item.weight_source, "item_status": item.item_status,
        "eta_min_days": item.eta_min_days, "eta_max_days": item.eta_max_days,
        "expected_arrival_from": item.expected_arrival_from, "expected_arrival_to": item.expected_arrival_to,
        "action": services.action_data(item),
        "events": [{"id": e.pk, "event_type": e.event_type, "title": render_text(e.text_code, **e.text_params)
                    if e.text_code else "", "message": e.message, "created_at": e.created_at}
                   for e in item.events.all() if e.visible_to_customer],
        "image_urls": image_urls(request, item.part_number, item.brand, images),
    }


def place_names():
    """City/district code -> localized name (one SiteSettings read per response)."""
    site = SiteSettings.get()
    return {row["code"]: tr(row.get("name")) for row in site.delivery_cities + site.delivery_districts}


def _images(orders):
    numbers = {normalize_part_number(i.part_number) for o in orders for i in o.items.all()}
    found = {}
    for image in PartImage.objects.filter(part_number__in=numbers):
        found.setdefault(image.part_number, []).append(image)
    return found


def _timeline(order, items):
    entries = [{"type": "order_created", "title": render_text("order_created"), "at": order.created_at}]
    for p in order.payments.all():
        if p.status != "paid":
            continue
        split = p.kind == "initial" and order.payment_option == "split_70_30" and order.split_upfront_percent
        title = render_text("payment_confirmed_split", percent=f"{order.split_upfront_percent.normalize():f}") if split \
            else render_text("payment_confirmed")
        entries.append({"type": "payment_paid", "title": title, "amount": _money(p.amount), "at": p.paid_at})
    for item in items:
        for e in item.events.all():
            if e.visible_to_customer:
                entries.append({"type": "item_event", "title": _text(e), "item_id": item.pk, "at": e.created_at})
    if order.cancelled_at:
        entries.append({"type": "cancelled", "title": render_text("order_cancelled"), "at": order.cancelled_at})
    return sorted(entries, key=lambda e: e["at"])


def order_data(request, order, *, detail=False, names=None, images=None):
    """G§8.0 Order; `detail` adds the detail-only fields. List views pass `names`/`images` loaded once."""
    items = list(order.items.all())
    if names is None and order.delivery_method == "delivery":
        names = place_names()
    if images is None:
        images = _images([order])
    names = names or {}
    point = order.pickup_point
    arrivals_from = [i.expected_arrival_from for i in items if i.expected_arrival_from]
    arrivals_to = [i.expected_arrival_to for i in items if i.expected_arrival_to]
    data = {
        "order_number": order.order_number, "status": order.status, "payment_status": services.payment_status(order),
        "currency": order.currency, "created_at": order.created_at, "items_count": sum(i.quantity for i in items),
        "delivery_method": order.delivery_method,
        "pickup_point": {"id": point.pk, "name": tr(point.name_i18n), "address": tr(point.address_i18n),
                         "working_hours": tr(point.working_hours_i18n)} if point else None,
        "delivery": {"city": order.delivery_city, "city_name": names.get(order.delivery_city, ""),
                     "district": order.delivery_district, "district_name": names.get(order.delivery_district, ""),
                     "address": order.delivery_address, "comment": order.delivery_comment}
        if order.delivery_method == "delivery" else None,
        "courier": {"fee": _money(order.courier_fee), "proposed_fee": _money(services.proposed_courier_fee(order)),
                    "action_required": order.courier_fee_action_required, "message": order.courier_fee_message,
                    "confirmed_at": order.courier_fee_confirmed_at, "declined_at": order.courier_fee_declined_at},
        "payment_method": order.payment_method, "payment_option": order.payment_option,
        "items_total": _money(order.items_total), "discount_total": _money(order.discount_total),
        "courier_fee": _money(order.courier_fee), "total": _money(order.total),
        "amount_due_now": _money(order.amount_due_now), "amount_paid": _money(order.amount_paid),
        "balance_due": _money(order.balance_due),
        "expected_arrival_from": min(arrivals_from) if arrivals_from else None,
        "expected_arrival_to": max(arrivals_to) if arrivals_to else None,
        "progress": services.progress(order), "attention": services.attention(order),
        "can_cancel": services.can_cancel(order),
        "items": [item_data(request, i, images.get(normalize_part_number(i.part_number), [])) for i in items],
    }
    if detail:
        by_id = {i.pk: i for i in items}
        messages = [m for m in order.support_messages.all() if m.visible_to_customer]
        data.update({
            "timeline": _timeline(order, items),
            "payments": [payment_data(p) for p in order.payments.all()],
            "support_messages": [{"id": m.pk, "item_id": m.item_id,
                                  "item_part_number": by_id[m.item_id].part_number if m.item_id in by_id else None,
                                  "sender_type": m.sender_type, "message": _text(m),
                                  "is_read_by_customer": m.is_read_by_customer, "created_at": m.created_at}
                                 for m in messages],
            "support_unread_count": sum(1 for m in messages
                                        if m.sender_type != m.SENDER_CUSTOMER and not m.is_read_by_customer),
            "invoice_url": request.build_absolute_uri(f"/api/orders/{order.order_number}/invoice/"),
        })
    return data


def orders_data(request, orders):
    """A page of list orders: place names and part images loaded once for the whole page."""
    orders = list(orders)
    names = place_names() if any(o.delivery_method == "delivery" for o in orders) else {}
    images = _images(orders)
    return [order_data(request, o, names=names, images=images) for o in orders]
