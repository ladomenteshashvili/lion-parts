"""Output shapes of G§8.0 Order / OrderItem / Payment (the detail-only fields follow in LP-BE-801)."""

from api.i18n import tr
from parts.services import image_urls
from pricing.models import SiteSettings

from . import services
from .texts import render_text


def _money(value):
    return f"{value:.2f}" if value is not None else None


def payment_data(payment):
    return {"id": payment.pk, "kind": payment.kind, "provider": payment.provider, "status": payment.status,
            "amount": _money(payment.amount), "currency": payment.currency,
            "checkout_url": payment.checkout_url or None, "paid_at": payment.paid_at,
            "created_at": payment.created_at}


def item_data(request, item):
    return {
        "id": item.pk, "part_number": item.part_number, "name": item.name, "brand": item.brand,
        "condition": item.condition, "quantity": item.quantity, "vin": item.vin,
        "list_unit_price": _money(item.list_unit_price), "discount_unit_amount": _money(item.discount_unit_amount),
        "unit_price": _money(item.unit_price), "line_total": _money(item.line_total),
        "weight_kg": _money(item.weight_kg), "weight_source": item.weight_source, "item_status": item.item_status,
        "eta_min_days": item.eta_min_days, "eta_max_days": item.eta_max_days,
        "expected_arrival_from": item.expected_arrival_from, "expected_arrival_to": item.expected_arrival_to,
        "action": None if not item.action_required else {  # decisions matrix: LP-BE-803
            "type": item.action_type, "message": item.action_message, "old": {}, "new": {}, "decisions": []},
        "events": [{"id": e.pk, "event_type": e.event_type, "title": render_text(e.text_code, **e.text_params)
                    if e.text_code else "", "message": e.message, "created_at": e.created_at}
                   for e in item.events.filter(visible_to_customer=True)],
        "image_urls": image_urls(request, item.part_number, item.brand),
    }


def order_data(request, order):
    items = list(order.items.all())
    names = {}
    if order.delivery_method == "delivery":
        site = SiteSettings.get()
        names = {row["code"]: tr(row.get("name")) for row in site.delivery_cities + site.delivery_districts}
    point = order.pickup_point
    arrivals_from = [i.expected_arrival_from for i in items if i.expected_arrival_from]
    arrivals_to = [i.expected_arrival_to for i in items if i.expected_arrival_to]
    return {
        "order_number": order.order_number, "status": order.status, "payment_status": services.payment_status(order),
        "currency": order.currency, "created_at": order.created_at, "items_count": sum(i.quantity for i in items),
        "delivery_method": order.delivery_method,
        "pickup_point": {"id": point.pk, "name": tr(point.name_i18n), "address": tr(point.address_i18n),
                         "working_hours": tr(point.working_hours_i18n)} if point else None,
        "delivery": {"city": order.delivery_city, "city_name": names.get(order.delivery_city, ""),
                     "district": order.delivery_district, "district_name": names.get(order.delivery_district, ""),
                     "address": order.delivery_address, "comment": order.delivery_comment}
        if order.delivery_method == "delivery" else None,
        "courier": {"fee": _money(order.courier_fee), "proposed_fee": _money(order.proposed_courier_fee_gel),
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
        "items": [item_data(request, item) for item in items],
    }
