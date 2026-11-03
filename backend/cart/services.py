"""Cart services (G§7.7): one cart per customer, one per guest session.

Every line stores its canonical USD price (never changed by the exchange rate) and the same price in
the cart currency. sync_prices() keeps the cart-currency amounts on the current NBG rate; it runs on
every cart read and change and after each rate fetch (refresh_gel_prices), and is idempotent."""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from accounts.models import CustomerTariff
from accounts.services import ensure_session
from api.errors import ApiError
from orders.texts import render_text
from parts.models import PartQuoteRequest
from parts.services import image_urls, owned_log, priced_for, request_customer, run_search
from pricing.currency import allowed_currencies, default_currency, display_rate
from pricing.engine import PriceInput, PriceSnapshot, convert, default_shipping_usd_per_kg, price_offer, reprice
from pricing.validity import is_expired

from .models import Cart, CartItem

MAX_QUANTITY = 99
CONVERTED_FIELDS = ("exchange_rate", "list_unit_price", "discount_unit_amount", "unit_price")


def get_cart(request, create=True):
    """The requester's cart: Cart(customer=...) when logged in, else Cart(session_key=...) for this
    browser session. With create=False, None when there is none yet."""
    customer = request_customer(request)
    if customer is not None:
        if create:
            return Cart.objects.get_or_create(customer=customer, defaults={"currency": default_currency()})[0]
        return Cart.objects.filter(customer=customer).first()
    if not create:
        key = request.session.session_key
        return Cart.objects.filter(session_key=key).first() if key else None
    return Cart.objects.get_or_create(session_key=ensure_session(request),
                                      defaults={"currency": default_currency()})[0]


def invalid_quantity():
    return serializers.ValidationError({"quantity": [serializers.ErrorDetail(
        str(_("The quantity must be between 1 and 99.")), code="invalid_quantity")]})


def _sync_line(line, currency, rate):
    """Set the cart-currency amounts from the canonical USD price; True when they changed."""
    converted = convert(line, currency, rate)
    if all(getattr(line, f) == getattr(converted, f) for f in CONVERTED_FIELDS):
        return False
    line.apply_converted(converted)
    return True


def sync_prices(cart):
    """Bring the cart to an allowed currency and the current rate (FX never changes the USD prices).
    Raises RateUnavailable for GEL without a current rate: an outdated GEL amount is never shown.
    -> the rate used (read once, so callers can freeze exactly that rate)."""
    if cart.currency not in allowed_currencies():  # e.g. multi-currency was switched off
        cart.currency = default_currency()
        cart.save(update_fields=["currency", "updated_at"])
    rate = display_rate(cart.currency)
    for line in cart.items.all():
        if _sync_line(line, cart.currency, rate):
            line.save(update_fields=[*CONVERTED_FIELDS, "updated_at"])
    return rate


def refresh_gel_prices(rate):
    """After a new NBG rate: re-convert every GEL cart line still on another rate. Idempotent;
    returns the number of lines changed. Orders are never touched (their amounts are frozen)."""
    changed = []
    for line in CartItem.objects.filter(cart__currency="GEL").exclude(exchange_rate=rate):
        if _sync_line(line, "GEL", rate):
            changed.append(line)
    CartItem.objects.bulk_update(changed, CONVERTED_FIELDS)
    return len(changed)


def _line_from_offer(log, offer):
    return {"source": CartItem.SOURCE_SEARCH, "search_log": log, "offer_id": offer["offer_id"],
            "quote_id": f"LOG-{log.pk}", "part_number": log.part_number, "name": offer["name"],
            "brand": offer["brand"], "condition": offer["condition"], "vin": log.vin,
            "weight_source": offer["weight_source"], "eta_min_days": offer["eta_min_days"],
            "eta_max_days": offer["eta_max_days"], "price_valid_until": log.price_valid_until
            }, PriceSnapshot.from_json(offer["snapshot"])


def _quote_snapshot(qr):
    return PriceSnapshot(
        supplier_price_usd=qr.supplier_price_usd, weight_kg=qr.prepared_weight_kg,
        shipping_usd_per_kg=qr.shipping_usd_per_kg, markup_percent=qr.markup_percent, vat_percent=qr.vat_percent,
        list_price_usd=qr.list_price_usd, discount_usd=qr.discount_usd, unit_price_usd=qr.unit_price_usd,
        discount_id=qr.discount_id)


def _line_from_quote(qr):
    """The operator-approved USD price is the base; it expires with the quote (re-approval needed)."""
    return {"source": CartItem.SOURCE_PREPARED_QUOTE, "quote_request": qr, "offer_id": f"QR-{qr.pk}",
            "quote_id": f"QR-{qr.pk}", "part_number": qr.part_number, "name": qr.name or qr.part_number,
            "brand": qr.brand, "condition": qr.condition, "vin": qr.vin, "weight_source": "operator",
            "eta_min_days": qr.eta_min_days, "eta_max_days": qr.eta_max_days,
            "price_valid_until": qr.price_valid_until}, _quote_snapshot(qr)


def add_item(request, data):
    """Add by reference only (G§7.7): the price comes from the owned search log or prepared quote,
    never from the request. Same (source, offer) -> quantity is added (max 99)."""
    customer = request_customer(request)
    if data["source"] == CartItem.SOURCE_SEARCH:
        log = owned_log(request, data["search_id"])
        offer = log.offer(data["offer_id"])
        if offer is None:
            raise ApiError("not_found", status=404)
        if is_expired(log.price_valid_until):
            raise ApiError("offer_expired", status=409)
        if offer.get("status") != "ready" or not offer.get("snapshot"):
            raise ApiError("offer_not_ready", status=409)
        fields, snap = _line_from_offer(log, offer)
        if log.normalized_response.get("priced_for", "unknown") != priced_for(customer):
            # Priced for someone else (e.g. a guest search before login): this customer's tariff and
            # discounts apply (canonical USD; the rate only converts).
            shown, snap = snap, reprice(snap.to_json(), customer=customer, part_number=fields["part_number"],
                                        brand=fields["brand"])
        else:
            shown = snap
    else:
        qr = PartQuoteRequest.objects.filter(pk=data["quote_request_id"], customer=customer).first() \
            if customer is not None else None
        if qr is None:
            raise ApiError("not_found", status=404)
        if not qr.is_price_ready:
            raise ApiError("offer_not_ready", status=409)
        if is_expired(qr.price_valid_until):
            raise ApiError("offer_expired", status=409)  # the operator re-approves an expired quote
        fields, snap = _line_from_quote(qr)
        shown = snap

    with transaction.atomic():
        cart = Cart.objects.select_for_update().get(pk=get_cart(request).pk)
        sync_prices(cart)  # allowed currency + current rate (RateUnavailable before anything changes)
        existing = cart.items.filter(source=fields["source"], offer_id=fields["offer_id"]).first()
        if existing:
            if existing.quantity + data["quantity"] > MAX_QUANTITY:
                raise invalid_quantity()
            existing.quantity += data["quantity"]
            existing.save(update_fields=["quantity", "updated_at"])
            return cart
        rate = display_rate(cart.currency)
        line = CartItem(cart=cart, quantity=data["quantity"], **fields)
        line.apply_snapshot(snap)
        line.apply_converted(convert(snap, cart.currency, rate))
        if shown.unit_price_usd != snap.unit_price_usd:
            line.previous_unit_price = convert(shown, cart.currency, rate).unit_price
            line.availability_status = CartItem.STATUS_PRICE_CHANGED
        line.save()
    return cart


# Read side (G§8.0 Cart)

def _money(value):
    return f"{value:.2f}" if value is not None else None


def line_status(line, now):
    return "expired" if is_expired(line.price_valid_until, now) else line.availability_status


def item_data(request, line, now):
    return {
        "id": line.pk, "source": line.source, "part_number": line.part_number, "name": line.name,
        "brand": line.brand, "condition": line.condition, "quantity": line.quantity, "vin": line.vin,
        "list_unit_price": _money(line.list_unit_price), "discount_unit_amount": _money(line.discount_unit_amount),
        "unit_price": _money(line.unit_price), "previous_unit_price": _money(line.previous_unit_price),
        "line_total": _money(line.unit_price * line.quantity), "status": line_status(line, now),
        "eta_min_days": line.eta_min_days, "eta_max_days": line.eta_max_days,
        "weight_kg": _money(line.weight_kg), "weight_source": line.weight_source,
        "customer_notice": render_text("weight_customer_notice") if line.weight_source == "customer" else "",
        "price_valid_until": line.price_valid_until,
        "image_urls": image_urls(request, line.part_number, line.brand),
    }


def cart_data(request, cart):
    """The G§8.0 Cart on the current rate (cart=None: the empty cart, without creating one). A pending
    merge summary is returned once, then cleared."""
    if cart is not None:
        sync_prices(cart)
    now = timezone.now()
    lines = list(cart.items.all()) if cart else []
    items = [item_data(request, line, now) for line in lines]
    customer = request_customer(request)
    tariff = customer.get_tariff() if customer else CustomerTariff.objects.get(is_default=True)
    blocked = [{"item_id": i["id"], "code": i["status"]} for i in items if i["status"] in ("expired", "unavailable")]
    merge = cart.merge_summary if cart else None
    if merge is not None:
        cart.merge_summary = None
        cart.save(update_fields=["merge_summary", "updated_at"])
    return {
        "currency": cart.currency if cart else default_currency(),
        "items": items,
        "summary": {
            "items_count": sum(line.quantity for line in lines),
            "items_total": _money(sum((line.list_unit_price * line.quantity for line in lines), Decimal("0"))),
            "discount_total": _money(sum((line.discount_unit_amount * line.quantity for line in lines), Decimal("0"))),
            "delivery_fee": None,
            "total": _money(sum((line.unit_price * line.quantity for line in lines), Decimal("0"))),
        },
        "payment_terms": tariff.payment_terms,
        "checkout_blocked": bool(blocked),
        "checkout_blocked_reasons": blocked,
        "merge": merge,
    }


def cart_quantities(request):
    """offer_id -> quantity of this requester's `search` lines (for in_cart_quantity), without creating a cart."""
    cart = get_cart(request, create=False)
    if cart is None:
        return {}
    return dict(cart.items.filter(source=CartItem.SOURCE_SEARCH).values_list("offer_id", "quantity"))


def cart_count(request):
    """Sum of line quantities, for /api/auth/me/."""
    cart = get_cart(request, create=False)
    return sum(cart.items.values_list("quantity", flat=True)) if cart else 0


def own_line(request, item_id):
    """A line of the requester's cart, else 404."""
    cart = get_cart(request, create=False)
    line = cart.items.filter(pk=item_id).first() if cart else None
    if line is None:
        raise ApiError("not_found", status=404)
    return line


def update_line(line, data):
    fields = [f for f in ("quantity", "vin") if f in data]
    for field in fields:
        setattr(line, field, data[field])
    if fields:
        line.save(update_fields=[*fields, "updated_at"])


def apply_vin(cart, vin):
    cart.items.update(vin=vin, updated_at=timezone.now())


def switch_currency(request, currency):
    """Show the cart in another allowed currency: every line is converted from its canonical USD
    price with the current rate. USD prices, validity and line status don't change (G§7.7)."""
    rate = display_rate(currency)
    with transaction.atomic():
        cart = Cart.objects.select_for_update().get(pk=get_cart(request).pk)
        for line in cart.items.all():
            if _sync_line(line, currency, rate):
                line.save(update_fields=[*CONVERTED_FIELDS, "updated_at"])
        cart.currency = currency
        cart.save(update_fields=["currency", "updated_at"])
    return cart


def _set_canonical(line, snap, rate):
    """New canonical price on a line: price_changed (with the old price, converted at today's rate
    so FX alone is never a change) when the USD unit price moved."""
    old_usd, old_shown = line.unit_price_usd, convert(line, line.cart.currency, rate).unit_price
    line.apply_snapshot(snap)
    line.apply_converted(convert(snap, line.cart.currency, rate))
    changed = snap.unit_price_usd != old_usd
    line.previous_unit_price = old_shown if changed else None
    line.availability_status = CartItem.STATUS_PRICE_CHANGED if changed else CartItem.STATUS_OK


def refresh_line(request, line):
    """G§7.5. Search lines: search the supplier again (a new log), find the same offer (else the same
    brand), reprice; gone -> unavailable; new validity. A customer weight is kept. Prepared quotes:
    no supplier call; the line takes the quote's current approval (re-approved -> new price and
    validity), an expired quote stays expired. Failures (supplier, rate) -> 502, line unchanged."""
    cart = line.cart
    rate = display_rate(cart.currency)
    if line.source == CartItem.SOURCE_PREPARED_QUOTE:
        qr = line.quote_request
        if qr is not None and qr.is_price_ready and not is_expired(qr.price_valid_until):
            _set_canonical(line, _quote_snapshot(qr), rate)
            line.price_valid_until = qr.price_valid_until
            line.save()
        return cart
    log = run_search(request, part_number=line.part_number, vin=line.vin, currency=cart.currency)
    offers = log.offers()
    offer = log.offer(line.offer_id) or next((o for o in offers if o["brand"].lower() == line.brand.lower()), None)
    weight = line.weight_kg if line.weight_source == "customer" else (
        Decimal(offer["weight_kg"]) if offer and offer.get("weight_kg") else None)
    line.search_log = log
    line.price_valid_until = log.price_valid_until
    if offer is None or weight is None:
        line.availability_status = CartItem.STATUS_UNAVAILABLE
    else:
        _set_canonical(line, price_offer(
            PriceInput(Decimal(offer["supplier_price_usd"]), weight, default_shipping_usd_per_kg()),
            customer=request_customer(request), part_number=line.part_number, brand=line.brand), rate)
    line.save()
    return cart


def merge_guest_cart(old_session_key, customer):
    """G§7.7 merge, in one transaction with both carts locked: move or sum the guest lines (cap 99),
    reprice the customer's search lines for their tariff (operator-approved quotes keep their USD
    base), delete the guest cart, store merge_summary (returned once). Never breaks a login: without
    a rate the cart-currency amounts aren't refreshed and the lines are listed as problems; the cart
    can't be read until a rate is available again."""
    with transaction.atomic():
        guest = Cart.objects.select_for_update().filter(session_key=old_session_key, customer__isnull=True).first()
        if guest is None:
            return None
        cart, created = Cart.objects.get_or_create(customer=customer, defaults={"currency": guest.currency})
        cart = Cart.objects.select_for_update().get(pk=cart.pk)
        summary = {"merged": True, "added_items": 0, "summed": [], "problems": []}
        for line in guest.items.all():
            existing = cart.items.filter(source=line.source, offer_id=line.offer_id).first()
            if existing:
                total = min(existing.quantity + line.quantity, MAX_QUANTITY)
                summary["summed"].append({"part_number": line.part_number, "from": existing.quantity, "to": total})
                existing.quantity = total
                existing.save(update_fields=["quantity", "updated_at"])
            else:
                line.cart = cart
                line.save(update_fields=["cart", "updated_at"])
                summary["added_items"] += 1
        guest.delete()

        try:
            rate = display_rate(cart.currency)
        except ApiError:
            rate = None
        for line in cart.items.all():
            if rate is None:
                summary["problems"].append({"item_id": line.pk, "code": "rate_unavailable"})
                continue
            if line.source == CartItem.SOURCE_SEARCH:
                old_usd = line.unit_price_usd
                snap = reprice(line, customer=customer)
                if snap.unit_price_usd != old_usd:
                    _set_canonical(line, snap, rate)
                else:
                    _sync_line(line, cart.currency, rate)
            else:
                _sync_line(line, cart.currency, rate)
            line.save()
        cart.merge_summary = summary
        cart.save(update_fields=["merge_summary", "updated_at"])
    return cart
