"""Search history (G§7.6): the customer's visible search logs + quote requests, newest first,
one item per (normalized part number, VIN), status resolved in the documented order."""

from decimal import Decimal

from django.utils import timezone

from accounts.validators import normalize_part_number
from pricing.currency import display_rate
from pricing.engine import convert
from pricing.validity import is_expired

from .models import PartQuoteRequest, PartSearchLog

FILTERS = {
    "all": None,
    "ready": {"ready", "price_ready", "price_changed"},
    "checking": {"checking"},
    "other": {"expired", "not_found", "purchased", "cancelled"},
}


def _money(value):
    return f"{Decimal(str(value)):.2f}" if value is not None else None


def _usd(offer):
    snap = offer.get("snapshot") if offer else None
    return Decimal(snap["unit_price_usd"]) if snap else None


def _best_offer(offers):
    """The cheapest ready offer (canonical USD), else the first offer, else None."""
    ready = [o for o in offers if o.get("status") == "ready" and o.get("snapshot")]
    if ready:
        return min(ready, key=_usd)
    return offers[0] if offers else None


class _Rates(dict):
    """Current display rate per currency, looked up once (RateUnavailable for GEL without a rate)."""

    def __missing__(self, currency):
        self[currency] = display_rate(currency)
        return self[currency]


def _shown(usd_amount, currency, rates):
    if usd_amount is None:
        return None
    return _money(convert({"list_price_usd": usd_amount, "unit_price_usd": usd_amount}, currency,
                          rates[currency]).unit_price)


def _purchased_orders(customer):
    """normalized part number -> latest order number, over the customer's non-cancelled items."""
    from orders.models import OrderItem  # shortcut: legacy OrderItem until LP-BE-601's schema

    rows = (OrderItem.objects.filter(order__customer=customer).exclude(item_status="cancelled")
            .order_by("order__created_at").values_list("part_number", "order__order_number"))
    return {normalize_part_number(part): number for part, number in rows}


def _log_item(log, previous_usd, rates):
    best = _best_offer(log.offers())
    unit_usd = _usd(best)
    item = {
        "id": f"log-{log.pk}", "kind": "search", "search_id": log.pk, "quote_request_id": None,
        "part_number": log.part_number, "vin": log.vin, "name": best["name"] if best else "",
        "brand": best["brand"] if best else "", "status": None,
        "unit_price": _shown(unit_usd, log.currency, rates),
        "previous_unit_price": None, "currency": log.currency, "offers_count": len(log.offers()),
        "eta_min_days": best.get("eta_min_days") if best else None,
        "eta_max_days": best.get("eta_max_days") if best else None,
        "weight_kg": best.get("weight_kg") if best else None, "weight_source": best.get("weight_source", "") if best else "",
        "price_valid_until": log.price_valid_until, "is_new": False, "purchased_order_number": None,
        "operator_message": "", "created_at": log.created_at,
    }
    if not best:
        item["status"] = "not_found"
    elif is_expired(log.price_valid_until):
        item["status"] = "expired"
    elif unit_usd is None:
        item["status"] = "checking"  # offers found, none priced yet (no weight)
    elif previous_usd is not None and previous_usd != unit_usd:  # canonical USD: FX alone is no change
        item.update(status="price_changed", previous_unit_price=_shown(previous_usd, log.currency, rates))
    else:
        item["status"] = "ready"
    return item


def _quote_item(qr, rates):
    ready = qr.is_price_ready
    if ready:
        status = "expired" if is_expired(qr.price_valid_until) else "price_ready"
    else:
        status = "cancelled" if qr.status == PartQuoteRequest.STATUS_CANCELLED else "checking"
    return {
        "id": f"qr-{qr.pk}", "kind": "quote_request", "search_id": qr.search_log_id, "quote_request_id": qr.pk,
        "part_number": qr.part_number, "vin": qr.vin, "name": qr.name, "brand": qr.brand, "status": status,
        "unit_price": _shown(qr.unit_price_usd, qr.currency, rates) if ready else None, "previous_unit_price": None,
        "currency": qr.currency,
        "offers_count": 1, "eta_min_days": qr.eta_min_days, "eta_max_days": qr.eta_max_days,
        "weight_kg": _money(qr.prepared_weight_kg),
        "weight_source": "operator" if qr.prepared_weight_kg is not None else "",
        "price_valid_until": qr.price_valid_until, "is_new": status == "price_ready" and qr.seen_at is None,
        "purchased_order_number": None,
        "operator_message": qr.operator_message, "created_at": qr.created_at,
    }


def build_history(customer, filter_name="all"):
    # shortcut: in-memory history merge; move to SQL if a customer exceeds a few thousand searches
    logs = list(PartSearchLog.objects.filter(customer=customer, hidden_at__isnull=True,
                                             status=PartSearchLog.STATUS_SUCCESS).order_by("created_at", "id"))
    key = lambda part, vin: (normalize_part_number(part), (vin or "").upper())

    # Oldest first, so each log can be compared with the previous search of the same part.
    rates, previous, log_items = _Rates(), {}, []
    for log in logs:
        k = key(log.part_number, log.vin)
        log_items.append(_log_item(log, previous.get(k), rates))
        best_usd = _usd(_best_offer(log.offers()))
        if best_usd is not None:
            previous[k] = best_usd

    items = log_items + [_quote_item(qr, rates) for qr in PartQuoteRequest.objects.filter(customer=customer)]
    items.sort(key=lambda i: i["created_at"], reverse=True)

    purchased = _purchased_orders(customer)
    seen, result = set(), []
    for item in items:
        k = key(item["part_number"], item["vin"])
        if k in seen:
            continue
        seen.add(k)
        order_number = purchased.get(k[0])
        if order_number:
            item.update(status="purchased", purchased_order_number=order_number)
        result.append(item)

    wanted = FILTERS[filter_name]
    return [i for i in result if wanted is None or i["status"] in wanted]


def unseen_ready_quotes(customer):
    """Approved, still valid and not seen yet (new_count, G§7.6)."""
    return PartQuoteRequest.objects.filter(customer=customer, price_ready_at__isnull=False, seen_at__isnull=True,
                                           price_valid_until__gt=timezone.now())
