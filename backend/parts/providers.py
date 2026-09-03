from decimal import Decimal
from typing import Any

from django.conf import settings

from accounts.models import Customer
from .amt_provider import AMTProviderError, get_price_by_oem
from .exchange_rates import ExchangeRateError, get_usd_sell_rate
from .models import CarrierService, PartSearchLog
from .pricing import calculate_final_price_gel


class PartsProviderError(Exception):
    pass



def _save_part_search_log(
    *,
    provider: str,
    part_number: str,
    vin: str | None,
    session_id: str | None,
    raw_response: object,
    normalized_response: dict[str, Any],
    status: str = PartSearchLog.STATUS_SUCCESS,
    error_message: str = "",
) -> None:
    try:
        PartSearchLog.objects.create(
            provider=provider,
            part_number=part_number,
            vin=vin or "",
            session_id=session_id or "",
            found_count=len(normalized_response.get("results", [])),
            status=status,
            raw_response={"data": raw_response},
            normalized_response=normalized_response,
            error_message=error_message,
        )
    except Exception:
        # Search logging must never break customer search.
        pass


def search_demo_parts(
    part_number: str,
    vin: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    if part_number.upper().startswith("NF"):
        response = {
            "quote_id": "Q-DEMO-NOT-FOUND",
            "part_number": part_number,
            "vin": vin,
            "results": [],
        }

        _save_part_search_log(
            provider=PartSearchLog.PROVIDER_DEMO,
            part_number=part_number,
            vin=vin,
            session_id=session_id,
            raw_response=response,
            normalized_response=response,
        )

        return response

    response = {
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
                "weight_source": "api",
                "customer_notice": "",
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
                "weight_source": "api",
                "customer_notice": "",
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
                "weight_source": "api",
                "customer_notice": "",
                "note": "Faster ETA option. Final availability will be checked after payment.",
            },
        ],
    }

    _save_part_search_log(
        provider=PartSearchLog.PROVIDER_DEMO,
        part_number=part_number,
        vin=vin,
        session_id=session_id,
        raw_response=response,
        normalized_response=response,
    )

    return response


def _to_decimal(value: Any) -> Decimal | None:
    if value in ("", None):
        return None

    try:
        return Decimal(str(value))
    except Exception:
        return None


def _clean_api_text(value: Any) -> str:
    if value in ("", None):
        return ""

    if isinstance(value, dict):
        return ""

    return str(value).strip()


def _is_no_part_found_row(row: dict[str, Any]) -> bool:
    description = _clean_api_text(row.get("descr")).upper()
    price = row.get("list_price")

    return description == "NO_PART_FOUND" or price in ("", None)


def _get_customer_markup_percent(customer: Customer | None) -> Decimal:
    if customer:
        return customer.get_markup_percent()

    return Decimal(str(settings.DEFAULT_CUSTOMER_MARKUP_PERCENT))


def _get_carrier_values() -> tuple[Decimal, int]:
    carrier = CarrierService.get_default()

    if carrier:
        return carrier.usd_per_kg, carrier.max_eta_days

    return (
        Decimal(str(settings.DEFAULT_SHIPPING_USD_PER_KG)),
        settings.DEFAULT_ETA_DAYS,
    )


def _build_amt_part_option(
    *,
    row: dict[str, Any],
    index: int,
    part_number: str,
    usd_to_gel_rate: Decimal,
    customer: Customer | None,
    manual_weight_kg: Decimal | None = None,
) -> dict[str, Any] | None:
    if _is_no_part_found_row(row):
        return None

    api_price_usd = _to_decimal(row.get("list_price"))

    if api_price_usd is None:
        return None

    api_weight_kg = _to_decimal(row.get("weight"))
    effective_weight_kg = manual_weight_kg if manual_weight_kg is not None else api_weight_kg

    requires_weight_input = (
        effective_weight_kg is None or effective_weight_kg <= 0
    )

    shipping_usd_per_kg, eta_days = _get_carrier_values()
    customer_markup_percent = _get_customer_markup_percent(customer)

    final_price_gel = None

    if not requires_weight_input:
        final_price_gel = calculate_final_price_gel(
            api_price_usd=api_price_usd,
            weight_kg=effective_weight_kg,
            usd_to_gel_rate=usd_to_gel_rate,
            shipping_usd_per_kg=shipping_usd_per_kg,
            customer_markup_percent=customer_markup_percent,
        )

    oem = _clean_api_text(row.get("oem")) or part_number
    description = _clean_api_text(row.get("descr")) or f"Part {oem}"
    brand = _clean_api_text(row.get("brand")) or "Unknown"

    note_parts = []

    if requires_weight_input:
        note_parts.append("ფასის დასათვლელად საჭიროა წონის შეყვანა.")
    elif manual_weight_kg is not None:
        note_parts.append("ფასი დათვლილია მომხმარებლის მიერ შეყვანილი წონით.")

    replacement = _clean_api_text(row.get("replace"))

    if replacement:
        note_parts.append(f"Replace: {replacement}")

    return {
        "part_option_id": f"AMT-{index}-{oem}",
        "name": description,
        "condition": "New",
        "brand": brand,
        "availability": "Price returned",
        "eta_days": eta_days,
        "final_price_gel": float(final_price_gel)
        if final_price_gel is not None
        else None,
        "currency": "GEL",
        "requires_weight_input": requires_weight_input,
        "weight_kg": float(effective_weight_kg)
        if effective_weight_kg is not None
        else None,
        "weight_source": "customer" if manual_weight_kg is not None else "api",
        "customer_notice": (
            "ფასი დათვლილია თქვენს მიერ შეყვანილი წონით. საბოლოო შემოწმება მოხდება ოპერატორის მიერ."
            if manual_weight_kg is not None
            else ""
        ),        
        "note": " ".join(note_parts),
    }


def search_amt_parts(
    part_number: str,
    vin: str | None = None,
    customer: Customer | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    try:
        rows = get_price_by_oem(part_number)
        usd_to_gel_rate = get_usd_sell_rate()
    except (AMTProviderError, ExchangeRateError) as error:
        raise PartsProviderError(str(error)) from error

    results = []

    for index, row in enumerate(rows, start=1):
        option = _build_amt_part_option(
            row=row,
            index=index,
            part_number=part_number,
            usd_to_gel_rate=usd_to_gel_rate,
            customer=customer,
        )

        if option:
            results.append(option)

    response = {
        "quote_id": f"AMT-{part_number}",
        "part_number": part_number,
        "vin": vin,
        "results": results,
    }

    _save_part_search_log(
        provider=PartSearchLog.PROVIDER_AMT,
        part_number=part_number,
        vin=vin,
        session_id=session_id or (customer.session_id if customer else ""),
        raw_response=rows,
        normalized_response=response,
    )

    return response


def calculate_amt_part_price(
    *,
    part_number: str,
    part_option_id: str,
    weight_kg: Decimal,
    customer: Customer,
) -> dict[str, Any]:
    try:
        rows = get_price_by_oem(part_number)
        usd_to_gel_rate = get_usd_sell_rate()
    except (AMTProviderError, ExchangeRateError) as error:
        raise PartsProviderError(str(error)) from error

    for index, row in enumerate(rows, start=1):
        option = _build_amt_part_option(
            row=row,
            index=index,
            part_number=part_number,
            usd_to_gel_rate=usd_to_gel_rate,
            customer=customer,
            manual_weight_kg=weight_kg,
        )

        if option and option["part_option_id"] == part_option_id:
            return option

    raise PartsProviderError("part option was not found")


def search_parts_provider(
    part_number: str,
    vin: str | None = None,
    customer: Customer | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    if settings.PARTS_PROVIDER == "amt":
        return search_amt_parts(part_number, vin, customer, session_id)

    return search_demo_parts(part_number, vin, session_id)


def calculate_part_price_provider(
    *,
    part_number: str,
    part_option_id: str,
    weight_kg: Decimal,
    customer: Customer,
) -> dict[str, Any]:
    if settings.PARTS_PROVIDER != "amt":
        raise PartsProviderError("manual weight calculation is available only for AMT")

    return calculate_amt_part_price(
        part_number=part_number,
        part_option_id=part_option_id,
        weight_kg=weight_kg,
        customer=customer,
    )