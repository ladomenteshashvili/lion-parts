"""Cart services (G§7.7): one cart per customer, one per guest session."""

from datetime import timedelta
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
from parts.services import image_urls, owned_log, request_customer, run_search
from pricing.engine import PriceInput, PriceSnapshot, default_shipping_usd_per_kg, price_offer, reprice
from pricing.models import SiteSettings
from pricing.rates import get_usd_rate

from .models import Cart, CartItem


def get_cart(request, create=True):
    """The requester's cart: Cart(customer=...) when logged in, else Cart(session_key=...) for this
    browser session. With create=False, None when there is none yet."""
    customer = request_customer(request)
    if customer is not None:
        if create:
            return Cart.objects.get_or_create(customer=customer)[0]
        return Cart.objects.filter(customer=customer).first()
    if not create:
        key = request.session.session_key
        return Cart.objects.filter(session_key=key).first() if key else None
    return Cart.objects.get_or_create(session_key=ensure_session(request))[0]


MAX_QUANTITY = 99


def invalid_quantity():
    return serializers.ValidationError({"quantity": [serializers.ErrorDetail(
        str(_("The quantity must be between 1 and 99.")), code="invalid_quantity")]})


def _line_from_offer(log, offer):
    snap = PriceSnapshot.from_json(offer["snapshot"])
    return {"source": CartItem.SOURCE_SEARCH, "search_log": log, "offer_id": offer["offer_id"],
            "quote_id": f"LOG-{log.pk}", "part_number": log.part_number, "name": offer["name"],
            "brand": offer["brand"], "condition": offer["condition"], "vin": log.vin,
            "weight_source": offer["weight_source"], "eta_min_days": offer["eta_min_days"],
            "eta_max_days": offer["eta_max_days"], "price_valid_until": log.price_valid_until}, snap


def _line_from_quote(qr):
    snap = PriceSnapshot(
        supplier_price_usd=qr.supplier_price_usd, weight_kg=qr.prepared_weight_kg,
        shipping_usd_per_kg=qr.shipping_usd_per_kg, markup_percent=qr.markup_percent, vat_percent=qr.vat_percent,
        exchange_rate=qr.exchange_rate, list_unit_price=qr.list_unit_price,
        discount_unit_amount=qr.discount_unit_amount, unit_price=qr.unit_price, discount_id=qr.discount_id,
        currency=qr.currency)
    valid_until = timezone.now() + timedelta(minutes=SiteSettings.get().price_valid_minutes)
    return {"source": CartItem.SOURCE_PREPARED_QUOTE, "quote_request": qr, "offer_id": f"QR-{qr.pk}",
            "quote_id": f"QR-{qr.pk}", "part_number": qr.part_number, "name": qr.name or qr.part_number,
            "brand": qr.brand, "condition": qr.condition, "vin": qr.vin, "weight_source": "operator",
            "eta_min_days": qr.eta_min_days, "eta_max_days": qr.eta_max_days, "price_valid_until": valid_until}, snap


def add_item(request, data):
    """Add by reference only (G§7.7): the price comes from the owned search log or prepared quote,
    never from the request. Same (source, offer) -> quantity is added (max 99)."""
    customer = request_customer(request)
    if data["source"] == CartItem.SOURCE_SEARCH:
        log = owned_log(request, data["search_id"])
        offer = log.offer(data["offer_id"])
        if offer is None:
            raise ApiError("not_found", status=404)
        if not log.price_valid_until or log.price_valid_until <= timezone.now():
            raise ApiError("offer_expired", status=409)
        if offer.get("status") != "ready" or not offer.get("snapshot"):
            raise ApiError("offer_not_ready", status=409)
        fields, snap = _line_from_offer(log, offer)
    else:
        qr = PartQuoteRequest.objects.filter(pk=data["quote_request_id"], customer=customer).first() \
            if customer is not None else None
        if qr is None:
            raise ApiError("not_found", status=404)
        if not qr.is_price_ready:
            raise ApiError("offer_not_ready", status=409)
        fields, snap = _line_from_quote(qr)

    with transaction.atomic():
        cart = Cart.objects.select_for_update().get(pk=get_cart(request).pk)
        existing = cart.items.filter(source=fields["source"], offer_id=fields["offer_id"]).first()
        if existing:
            if existing.quantity + data["quantity"] > MAX_QUANTITY:
                raise invalid_quantity()
            existing.quantity += data["quantity"]
            existing.save(update_fields=["quantity", "updated_at"])
            return cart
        if snap.currency != cart.currency:
            snap = reprice(snap.to_json() | {"part_number": fields["part_number"], "brand": fields["brand"]},
                           customer=customer, currency=cart.currency)
        line = CartItem(cart=cart, quantity=data["quantity"], **fields)
        line.apply_snapshot(snap)
        line.save()
    return cart


# Read side (G§8.0 Cart)

def _money(value):
    return f"{value:.2f}" if value is not None else None


def line_status(line, now):
    return "expired" if now > line.price_valid_until else line.availability_status


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
    """The G§8.0 Cart (cart=None: the empty cart, without creating one). A pending merge summary is
    returned once, then cleared."""
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
        "currency": cart.currency if cart else "GEL",
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
    """Reprice every line into `currency` with the current rate, markup, VAT and discounts.
    price_valid_until and the line status don't change (G§7.7)."""
    customer = request_customer(request)
    rate = get_usd_rate()
    with transaction.atomic():
        cart = Cart.objects.select_for_update().get(pk=get_cart(request).pk)
        for line in cart.items.all():
            line.apply_snapshot(reprice(line, customer=customer, currency=currency, usd_rate=rate))
            line.save()
        cart.currency = currency
        cart.save(update_fields=["currency", "updated_at"])
    return cart


def refresh_line(request, line):
    """G§7.5: search the supplier again (saved as a new log), find the same offer (else the same
    brand), reprice. Gone -> unavailable; a new unit price -> price_changed with the old one kept.
    A customer/operator weight is kept; only an API weight follows the supplier. Supplier or rate
    failure -> 502 with the line unchanged."""
    cart = line.cart
    log = run_search(request, part_number=line.part_number, vin=line.vin, currency=cart.currency)
    offers = log.offers()
    offer = log.offer(line.offer_id) or next((o for o in offers if o["brand"].lower() == line.brand.lower()), None)
    weight = line.weight_kg if line.weight_source in ("customer", "operator") else (
        Decimal(offer["weight_kg"]) if offer and offer.get("weight_kg") else None)
    line.search_log = log if line.source == CartItem.SOURCE_SEARCH else line.search_log
    line.price_valid_until = log.price_valid_until
    if offer is None or weight is None:
        line.availability_status = CartItem.STATUS_UNAVAILABLE
    else:
        old_price = line.unit_price
        line.apply_snapshot(price_offer(
            PriceInput(Decimal(offer["supplier_price_usd"]), weight, default_shipping_usd_per_kg()),
            customer=request_customer(request), currency=cart.currency, part_number=line.part_number,
            brand=line.brand, usd_rate=log.exchange_rate))
        changed = line.unit_price != old_price
        line.previous_unit_price = old_price if changed else None
        line.availability_status = CartItem.STATUS_PRICE_CHANGED if changed else CartItem.STATUS_OK
    line.save()
    return cart


def merge_guest_cart(old_session_key, customer):
    """G§7.7 merge, in one transaction with both carts locked: move or sum the guest lines (cap 99),
    reprice every customer line, delete the guest cart, store merge_summary (returned once).
    Never breaks a login: without a rate the lines keep their prices and are listed as problems."""
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
            rate = get_usd_rate()
        except ApiError:
            rate = None
        for line in cart.items.all():
            if rate is None:
                summary["problems"].append({"item_id": line.pk, "code": "rate_unavailable"})
                continue
            old_price = line.unit_price
            line.apply_snapshot(reprice(line, customer=customer, currency=cart.currency, usd_rate=rate))
            if line.unit_price != old_price:
                line.previous_unit_price = old_price
                line.availability_status = CartItem.STATUS_PRICE_CHANGED
            line.save()
        cart.merge_summary = summary
        cart.save(update_fields=["merge_summary", "updated_at"])
    return cart
