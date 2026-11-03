"""Parts services: part images and pricing a prepared quote (G§7.6)."""

from django.utils import timezone

from accounts.validators import normalize_part_number
from pricing.engine import PriceInput, default_shipping_usd_per_kg, price_offer

from .models import PartImage, PartQuoteRequest


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
