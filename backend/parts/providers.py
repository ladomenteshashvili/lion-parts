from decimal import Decimal
from typing import Any

from django.conf import settings

from .amt_provider import AMTProviderError, get_price_by_oem
from .exchange_rates import ExchangeRateError, get_usd_sell_rate
from .pricing import calculate_final_price_gel


class PartsProviderError(Exception):
    pass


def search_demo_parts(part_number: str, vin: str | None = None) -> dict[str, Any]:
    if part_number.upper().startswith("NF"):
        return {
            "quote_id": "Q-DEMO-NOT-FOUND",
            "part_number": part_number,
            "vin": vin,
            "results": [],
        }

    return {
        "quote_id": "Q-DEMO-0001",
        "part_number": part_number,
        "vin": vin,
        "results": [
            {
                "part_option_id": "P-DEMO-001",
                "name": "Demo OEM Part",
                "condition": "New",
                "brand": "OEM",
                "availability": "Available",
                "eta_days": 14,
                "final_price_gel": 650.00,
                "currency": "GEL",
                "requires_weight_input": False,
                "weight_kg": 2.00,
                "note": "Original new part. Demo offer. Supplier integration will be added later.",
            },
            {
                "part_option_id": "P-DEMO-002",
                "name": "Demo Aftermarket Part",
                "condition": "New",
                "brand": "Aftermarket",
                "availability": "Available",
                "eta_days": 10,
                "final_price_gel": 520.00,
                "currency": "GEL",
                "requires_weight_input": False,
                "weight_kg": 1.80,
                "note": "Lower price option. Compatibility must be confirmed before purchase.",
            },
            {
                "part_option_id": "P-DEMO-003",
                "name": "Demo OEM Express Part",
                "condition": "New",
                "brand": "OEM",
                "availability": "Limited",
                "eta_days": 7,
                "final_price_gel": 790.00,
                "currency": "GEL",
                "requires_weight_input": False,
                "weight_kg": 2.00,
                "note": "Faster ETA option. Final availability will be checked after payment.",
            },
        ],
    }


def _to_decimal(value: Any) -> Decimal | None:
    if value in ("", None):
        return None

    try:
        return Decimal(str(value))
    except Exception:
        return None


def search_amt_parts(part_number: str, vin: str | None = None) -> dict[str, Any]:
    try:
        rows = get_price_by_oem(part_number)
        usd_to_gel_rate = get_usd_sell_rate()
    except (AMTProviderError, ExchangeRateError) as error:
        raise PartsProviderError(str(error)) from error

    results = []

    for index, row in enumerate(rows, start=1):
        api_price_usd = _to_decimal(row.get("list_price"))
        weight_kg = _to_decimal(row.get("weight"))

        requires_weight_input = weight_kg is None or weight_kg <= 0

        final_price_gel = None

        if api_price_usd is not None and not requires_weight_input:
            final_price_gel = calculate_final_price_gel(
                api_price_usd=api_price_usd,
                weight_kg=weight_kg,
                usd_to_gel_rate=usd_to_gel_rate,
            )

        oem = row.get("oem") or part_number
        description = row.get("descr") or f"Part {oem}"
        brand = row.get("brand") or "Unknown"

        note_parts = []

        if requires_weight_input:
            note_parts.append("ფასის დასათვლელად საჭიროა წონის შეყვანა.")

        if row.get("replace"):
            note_parts.append(f"Replace: {row.get('replace')}")

        results.append(
            {
                "part_option_id": f"AMT-{index}-{oem}",
                "name": description,
                "condition": "New",
                "brand": brand,
                "availability": "Price returned",
                "eta_days": settings.DEFAULT_ETA_DAYS,
                "final_price_gel": float(final_price_gel)
                if final_price_gel is not None
                else None,
                "currency": "GEL",
                "requires_weight_input": requires_weight_input,
                "weight_kg": float(weight_kg) if weight_kg is not None else None,
                "note": " ".join(note_parts),
            }
        )

    return {
        "quote_id": f"AMT-{part_number}",
        "part_number": part_number,
        "vin": vin,
        "results": results,
    }


def search_parts_provider(part_number: str, vin: str | None = None) -> dict[str, Any]:
    if settings.PARTS_PROVIDER == "amt":
        return search_amt_parts(part_number, vin)

    return search_demo_parts(part_number, vin)