"""Parts services: part images and pricing a prepared quote (G§7.6)."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from accounts.validators import normalize_part_number
from api.errors import ApiError
from pricing.engine import PriceInput, default_shipping_usd_per_kg, price_offer
from pricing.models import SiteSettings
from pricing.rates import get_usd_rate

from .models import PartImage, PartQuoteRequest, PartSearchLog
from .providers import build_offers, fetch_rows, public_offer


def image_urls(request, part_number, brand):
    """Absolute image URLs for a part: brand-specific images first, then generic ones (G§7.6)."""
    images = PartImage.objects.filter(part_number=normalize_part_number(part_number))
    brand = (brand or "").lower()
    ranked = sorted((img for img in images if not img.brand or img.brand.lower() == brand),
                    key=lambda img: (not img.brand, img.sort_order, img.id))
    return [request.build_absolute_uri(img.image.url) for img in ranked]


def mark_quote_price_ready(quote_request):
    """Price a quote request from its supplier price and the operator's weight for its customer and
    currency, store the snapshot and mark it ready. Raises RateUnavailable; callers check
    prepared_weight_kg and supplier_price_usd first."""
    snapshot = price_offer(
        PriceInput(quote_request.supplier_price_usd, quote_request.prepared_weight_kg, default_shipping_usd_per_kg()),
        customer=quote_request.customer, currency=quote_request.currency,
        part_number=quote_request.part_number, brand=quote_request.brand,
    )
    for field in ("shipping_usd_per_kg", "markup_percent", "vat_percent", "exchange_rate", "list_unit_price",
                  "discount_unit_amount", "unit_price"):
        setattr(quote_request, field, getattr(snapshot, field))
    quote_request.discount_id = snapshot.discount_id
    quote_request.status = PartQuoteRequest.STATUS_RESOLVED
    quote_request.price_ready_at = timezone.now()
    quote_request.seen_at = None
    quote_request.save()
    # LP-BE-1104/903: notify(price_ready) once notifications exist.
    return quote_request


# Search (G§7.6, G§8.4)

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
        provider, raw, rows = fetch_rows(part_number)
        rate = get_usd_rate()
        offers = build_offers(rows, customer=customer, currency=currency, part_number=part_number, usd_rate=rate)
    except ApiError as exc:
        log.status, log.error_message = PartSearchLog.STATUS_FAILED, exc.code
        log.save()
        raise
    log.provider, log.raw_response = provider, {"data": raw}
    log.normalized_response, log.found_count = {"offers": offers}, len(offers)
    log.exchange_rate = rate
    log.price_valid_until = timezone.now() + timedelta(minutes=SiteSettings.get().price_valid_minutes)
    log.save()
    return log


def search_response(request, log):
    return {
        "search_id": log.pk, "part_number": log.part_number, "vin": log.vin, "currency": log.currency,
        "exchange_rate": f"{log.exchange_rate:.4f}" if log.exchange_rate is not None else None,
        "price_valid_until": log.price_valid_until, "not_found": not log.offers(),
        "offers": [present_offer(request, offer, log.part_number) for offer in log.offers()],
    }


def present_offer(request, offer, part_number):
    """Public offer with the request-dependent parts: cart quantity (LP-BE-501) and image URLs."""
    return public_offer(offer, in_cart_quantity=0, image_urls=image_urls(request, part_number, offer.get("brand")))
