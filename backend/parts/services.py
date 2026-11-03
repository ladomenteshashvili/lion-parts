"""Parts services: part images and pricing a prepared quote (G§7.6)."""

from django.conf import settings
from django.utils import timezone

from accounts.validators import normalize_part_number
from api.errors import ApiError
from orders.texts import render_text
from pricing.currency import default_currency, display_rate, effective_currency
from pricing.engine import PriceInput, convert, default_shipping_usd_per_kg, price_offer
from pricing.validity import price_valid_until

from .models import PartImage, PartQuoteRequest, PartSearchLog
from .providers import build_offers, fetch_rows, public_offer


def image_urls(request, part_number, brand, images=None):
    """Absolute image URLs for a part: brand-specific images first, then generic ones (G§7.6).
    `images`: this part's PartImage rows when the caller loaded them already (no query)."""
    if images is None:
        images = PartImage.objects.filter(part_number=normalize_part_number(part_number))
    brand = (brand or "").lower()
    ranked = sorted((img for img in images if not img.brand or img.brand.lower() == brand),
                    key=lambda img: (not img.brand, img.sort_order, img.id))
    return [request.build_absolute_uri(img.image.url) for img in ranked]


def mark_quote_price_ready(quote_request):
    """The operator approves a price: canonical USD from the supplier price and the operator's weight
    for the request's customer, valid for the configured business days. Running it again re-approves
    an expired quote. Callers check prepared_weight_kg and supplier_price_usd first."""
    snapshot = price_offer(
        PriceInput(quote_request.supplier_price_usd, quote_request.prepared_weight_kg, default_shipping_usd_per_kg()),
        customer=quote_request.customer, part_number=quote_request.part_number, brand=quote_request.brand,
    )
    for field in ("shipping_usd_per_kg", "markup_percent", "vat_percent", "list_price_usd", "discount_usd",
                  "unit_price_usd"):
        setattr(quote_request, field, getattr(snapshot, field))
    quote_request.discount_id = snapshot.discount_id
    quote_request.status = PartQuoteRequest.STATUS_RESOLVED
    quote_request.price_ready_at = timezone.now()
    quote_request.price_valid_until = price_valid_until(quote_request.price_ready_at)
    quote_request.seen_at = None
    quote_request.save()
    # LP-BE-1104/903: notify(price_ready) once notifications exist.
    return quote_request


def mark_quote_ready(quote_request_id, weight_kg, message="", *, supplier_price_usd=None):
    """Operator "price ready" (G§7.6, LP-BE-1104), used by the admin and the demo endpoint. The supplier price
    S: `supplier_price_usd` when the operator enters it; for a request made from a search offer, the
    supplier is asked again (that offer must still exist); otherwise the price stored on the request.
    Raises OperatorError (nothing changes) or RateUnavailable. -> the quote request."""
    from django.db import transaction
    from django.utils.translation import gettext as _

    from orders.services import OperatorError

    from .providers import PartsProviderError, fetch_rows

    if weight_kg is None or weight_kg <= 0:
        raise OperatorError("weight_kg", _("Enter a weight greater than zero."))
    with transaction.atomic():
        qr = PartQuoteRequest.objects.select_for_update().select_related("customer").get(pk=quote_request_id)
        if qr.status == PartQuoteRequest.STATUS_CANCELLED:
            raise OperatorError("weight_kg", _("The request is cancelled."))
        if supplier_price_usd is None and qr.offer_id:
            try:
                rows = fetch_rows(qr.part_number)[2]
            except PartsProviderError:
                raise OperatorError("supplier_price_usd", _("The supplier is unavailable; enter the supplier price."))
            row = next((r for r in rows if r["offer_id"] == qr.offer_id), None)
            if row is None:
                raise OperatorError("supplier_price_usd", _("The supplier no longer offers this part; enter the "
                                                            "supplier price."))
            supplier_price_usd = row["supplier_price_usd"]
        supplier_price_usd = supplier_price_usd if supplier_price_usd is not None else qr.supplier_price_usd
        if supplier_price_usd is None or supplier_price_usd <= 0:
            raise OperatorError("supplier_price_usd", _("Enter the supplier price."))
        qr.prepared_weight_kg, qr.supplier_price_usd, qr.operator_message = weight_kg, supplier_price_usd, message
        return mark_quote_price_ready(qr)


# Search (G§7.6, G§8.4)

def priced_for(customer):
    """Who a log's offers were priced for: the customer id, or None for the default (guest) tariff."""
    return customer.pk if customer else None


def request_customer(request):
    """The logged-in customer, or None for guests (and staff sessions)."""
    from accounts.services import can_log_in
    return request.user.customer if can_log_in(request.user) else None


def run_search(request, *, part_number, vin, currency):
    """Query the supplier, price the offers for this customer and save the log (the price source for
    the cart). A supplier failure is logged and re-raised (502)."""
    from accounts.services import ensure_session

    customer, session_key = request_customer(request), ensure_session(request)
    log = PartSearchLog(kind=PartSearchLog.KIND_SEARCH, customer=customer, session_key=session_key,
                        part_number=part_number, vin=vin, currency=currency, provider=settings.PARTS_PROVIDER)
    try:
        rate = display_rate(currency)  # no outdated GEL: an unavailable rate fails before the supplier call
        provider, raw, rows = fetch_rows(part_number)
        offers = build_offers(rows, customer=customer, part_number=part_number)
    except ApiError as exc:
        log.status, log.error_message = PartSearchLog.STATUS_FAILED, exc.code
        log.save()
        raise
    log.provider, log.raw_response = provider, {"data": raw}
    log.normalized_response, log.found_count = {"offers": offers, "priced_for": priced_for(customer)}, len(offers)
    log.exchange_rate = rate
    log.price_valid_until = price_valid_until()
    log.save()
    return log


def search_response(request, log):
    from cart.services import cart_quantities  # cart builds on parts

    in_cart, rate = cart_quantities(request), display_rate(log.currency)
    return {
        "search_id": log.pk, "part_number": log.part_number, "vin": log.vin, "currency": log.currency,
        "exchange_rate": f"{rate:.4f}", "price_valid_until": log.price_valid_until, "not_found": not log.offers(),
        "offers": [present_offer(request, offer, log.part_number, log.currency, rate, in_cart)
                   for offer in log.offers()],
    }


def present_offer(request, offer, part_number, currency, rate, in_cart=None):
    """Public offer with the request-dependent parts: cart quantity (LP-BE-501), image URLs and the
    weight disclaimer in the request language (stored as weight_source only, G§7.6)."""
    notice = render_text("weight_customer_notice") if offer.get("weight_source") == "customer" else ""
    return public_offer(offer, currency=currency, rate=rate, in_cart_quantity=(in_cart or {}).get(offer["offer_id"], 0),
                        image_urls=image_urls(request, part_number, offer.get("brand")),
                        customer_notice=notice)


def calculate_weight_price(request, log, offer_id, weight_kg):
    """Re-query the supplier for a fresh price, price the offer with the customer's weight and save it
    as a new weight_calc log (its search_id + offer_id can be added to the cart)."""
    customer = request_customer(request)
    if log.offer(offer_id) is None:
        raise ApiError("not_found", status=404)
    currency = effective_currency(log.currency)
    rate = display_rate(currency)
    provider, raw, rows = fetch_rows(log.part_number)
    row = next((r for r in rows if r["offer_id"] == offer_id), None)
    if row is None:
        raise ApiError("not_found", status=404)  # the supplier no longer returns this offer
    offers = build_offers([row], customer=customer, part_number=log.part_number,
                          weight_source="customer", weight_override=weight_kg)
    return PartSearchLog.objects.create(
        kind=PartSearchLog.KIND_WEIGHT_CALC, customer=customer, session_key=request.session.session_key or "",
        provider=provider, part_number=log.part_number, vin=log.vin, found_count=1, raw_response={"data": raw},
        normalized_response={"offers": offers, "priced_for": priced_for(customer)}, currency=currency,
        exchange_rate=rate, price_valid_until=price_valid_until(),
    )


def owned_log(request, search_id):
    """A search log the requester owns (G§7.6): theirs, or a guest log of this browser session.
    Anything else is 404 (never reveal that it exists)."""
    customer = request_customer(request)
    log = PartSearchLog.objects.filter(pk=search_id).first() if str(search_id).isdigit() else None
    if log is None or log.status != PartSearchLog.STATUS_SUCCESS:
        raise ApiError("not_found", status=404)
    if customer is not None and log.customer_id == customer.pk:
        return log
    session_key = request.session.session_key
    if log.customer_id is None and session_key and log.session_key == session_key:
        return log
    raise ApiError("not_found", status=404)


# Quote requests (G§7.6, G§8.4)

def create_quote_request(customer, data, log=None):
    """-> (quote_request, created). An open request for the same customer, part, offer and type is
    returned instead of a duplicate."""
    if data["request_type"] == PartQuoteRequest.REQUEST_TYPE_WEIGHT_PRICE:
        offer = log.offer(data["offer_id"])
        if offer is None:
            raise ApiError("not_found", status=404)
        fields = {"search_log": log, "part_number": log.part_number, "vin": log.vin, "offer_id": offer["offer_id"],
                  "quote_id": f"LOG-{log.pk}", "name": offer["name"], "brand": offer["brand"],
                  "condition": offer["condition"], "eta_min_days": offer["eta_min_days"],
                  "eta_max_days": offer["eta_max_days"], "currency": log.currency,
                  "supplier_price_usd": offer.get("supplier_price_usd")}
    else:
        fields = {"part_number": data["part_number"], "vin": data.get("vin", ""), "offer_id": "",
                  "currency": default_currency()}
    existing = next((qr for qr in PartQuoteRequest.objects.filter(
        customer=customer, request_type=data["request_type"], offer_id=fields["offer_id"],
        status__in=PartQuoteRequest.OPEN_STATUSES,
    ) if normalize_part_number(qr.part_number) == normalize_part_number(fields["part_number"])), None)
    if existing:
        return existing, False
    return PartQuoteRequest.objects.create(customer=customer, request_type=data["request_type"],
                                           comment=data.get("comment", ""), **fields), True


def quote_unit_price(qr):
    """The approved unit price in the quote's currency at the current rate (GEL follows the rate,
    the USD base doesn't change). Raises RateUnavailable for GEL without a current rate."""
    currency = effective_currency(qr.currency)
    return f"{convert(qr, currency, display_rate(currency)).unit_price:.2f}"


def quote_request_data(qr):
    """The G§8.0 QuoteRequest."""
    return {"id": qr.pk, "request_type": qr.request_type, "part_number": qr.part_number, "vin": qr.vin,
            "name": qr.name, "brand": qr.brand, "status": qr.status, "is_price_ready": qr.is_price_ready,
            "unit_price": quote_unit_price(qr) if qr.is_price_ready else None,
            "currency": effective_currency(qr.currency),
            "price_valid_until": qr.price_valid_until,
            "weight_kg": f"{qr.prepared_weight_kg:.2f}" if qr.prepared_weight_kg is not None else None,
            "operator_message": qr.operator_message, "created_at": qr.created_at}


def public_quote_data(qr):
    """GET /api/public/q/<token>/: the prepared price only, never customer data."""
    return {"quote_request_id": qr.pk, "part_number": qr.part_number, "name": qr.name, "brand": qr.brand,
            "unit_price": quote_unit_price(qr), "currency": effective_currency(qr.currency),
            "price_valid_until": qr.price_valid_until,
            "eta_min_days": qr.eta_min_days,
            "eta_max_days": qr.eta_max_days,
            "weight_kg": f"{qr.prepared_weight_kg:.2f}" if qr.prepared_weight_kg is not None else None,
            "operator_message": qr.operator_message, "requires_login": True}
