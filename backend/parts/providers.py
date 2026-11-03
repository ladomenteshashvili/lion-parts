"""Supplier rows -> priced offers (G§7.6).

Providers (AMT SOAP, or demo data) only return raw rows; build_offers() prices every row through
pricing.engine, so demo and AMT prices follow the same rules. Stored offers (PartSearchLog
.normalized_response["offers"]) keep internal keys (supplier price, snapshot); public_offer() is the
only way they reach a client."""

from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings

from api.errors import ApiError
from pricing.engine import PriceInput, default_shipping_usd_per_kg, price_offer
from pricing.rates import get_usd_rate

from .amt_provider import AMTProviderError, get_price_by_oem
from .models import CarrierService, PartSearchLog

OFFER_KEYS = ("offer_id", "name", "brand", "condition", "status", "list_unit_price", "discount_unit_amount",
              "unit_price", "currency", "eta_min_days", "eta_max_days", "weight_kg", "weight_source",
              "requires_weight", "in_cart_quantity", "image_urls", "customer_notice", "note")

# Demo supplier rows (USD supplier prices, kg). One row has no weight, so staging can exercise the
# "checking" / weight-calculation / quote-request flows. Part numbers starting with NF find nothing.
DEMO_ROWS = [
    {"name": "Demo OEM Part", "brand": "OEM", "supplier_price_usd": "180.00", "weight_kg": "2.00",
     "note": "Original new part. Demo offer."},
    {"name": "Demo Aftermarket Part", "brand": "Aftermarket", "supplier_price_usd": "140.00", "weight_kg": "1.80",
     "note": "Lower price option. Compatibility must be confirmed before purchase."},
    {"name": "Demo OEM Express Part", "brand": "OEM", "supplier_price_usd": "230.00", "weight_kg": "2.00",
     "note": "Faster option. Final availability is checked after payment."},
    {"name": "Demo Part Without Weight", "brand": "Denso", "supplier_price_usd": "95.00", "weight_kg": None,
     "note": ""},
]


class PartsProviderError(ApiError):
    """The supplier failed: 502 supplier_unavailable."""

    def __init__(self):
        super().__init__("supplier_unavailable", status=502)


def _decimal(value):
    if value in ("", None) or isinstance(value, dict):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number > 0 else None


def _text(value):
    return "" if value in ("", None) or isinstance(value, dict) else str(value).strip()


def _amt_rows(part_number):
    try:
        rows = get_price_by_oem(part_number)
    except (AMTProviderError, requests.RequestException):
        raise PartsProviderError() from None
    out = []
    for index, row in enumerate(rows, start=1):
        price = _decimal(row.get("list_price"))
        if _text(row.get("descr")).upper() == "NO_PART_FOUND" or price is None:
            continue
        oem = _text(row.get("oem")) or part_number
        replacement = _text(row.get("replace"))
        out.append({"offer_id": f"AMT-{index}-{oem}", "name": _text(row.get("descr")) or oem,
                    "brand": _text(row.get("brand")), "supplier_price_usd": price,
                    "weight_kg": _decimal(row.get("weight")), "note": f"Replace: {replacement}" if replacement else ""})
    return rows, out


def _demo_rows(part_number):
    if part_number.upper().startswith("NF"):
        return [], []
    return DEMO_ROWS, [{**row, "offer_id": f"DEMO-{i}-{part_number}", "supplier_price_usd": Decimal(row["supplier_price_usd"]),
                        "weight_kg": _decimal(row["weight_kg"])} for i, row in enumerate(DEMO_ROWS, start=1)]


def fetch_rows(part_number):
    """-> (provider, raw response, normalized rows). Raises PartsProviderError."""
    if settings.PARTS_PROVIDER == "amt":
        return (PartSearchLog.PROVIDER_AMT, *_amt_rows(part_number))
    return (PartSearchLog.PROVIDER_DEMO, *_demo_rows(part_number))


def build_offers(rows, *, customer, currency, part_number, weight_source="api", weight_override=None, usd_rate=None):
    """Price every row for `customer` (None = guest) in `currency`. A row without a weight is
    `checking` with null prices. `weight_override` (customer weight calculation) replaces the
    supplier weight and sets `weight_source`."""
    carrier = CarrierService.get_default()
    shipping = default_shipping_usd_per_kg()
    eta_min, eta_max = (carrier.min_eta_days, carrier.max_eta_days) if carrier else (None, None)
    rate = usd_rate
    offers = []
    for row in rows:
        weight = weight_override if weight_override is not None else row["weight_kg"]
        offer = {"offer_id": row["offer_id"], "name": row["name"], "brand": row["brand"], "condition": "New",
                 "currency": currency, "eta_min_days": eta_min, "eta_max_days": eta_max, "note": row["note"],
                 "supplier_price_usd": str(row["supplier_price_usd"])}
        if weight is None:
            offer.update(status="checking", list_unit_price=None, discount_unit_amount=None, unit_price=None,
                         weight_kg=None, weight_source="", requires_weight=True, snapshot=None)
        else:
            rate = rate if rate is not None else get_usd_rate()
            snap = price_offer(PriceInput(row["supplier_price_usd"], weight, shipping), customer=customer,
                               currency=currency, part_number=part_number, brand=row["brand"], usd_rate=rate)
            offer.update(status="ready", list_unit_price=str(snap.list_unit_price),
                         discount_unit_amount=str(snap.discount_unit_amount), unit_price=str(snap.unit_price),
                         weight_kg=f"{weight:.2f}", weight_source=weight_source if weight_override is not None else "api",
                         requires_weight=False, snapshot=snap.to_json())
        offers.append(offer)
    return offers


def public_offer(offer, *, in_cart_quantity=0, image_urls=(), customer_notice=""):
    """The G§8.0 Offer for clients: never the supplier price or the internal snapshot."""
    return {**{key: offer.get(key) for key in OFFER_KEYS}, "in_cart_quantity": in_cart_quantity,
            "image_urls": list(image_urls), "customer_notice": customer_notice}
