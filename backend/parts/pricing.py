from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings


def decimal_from_setting(value: str) -> Decimal:
    return Decimal(str(value))


def calculate_final_price_gel(
    *,
    api_price_usd: Decimal,
    weight_kg: Decimal,
    usd_to_gel_rate: Decimal,
) -> Decimal:
    shipping_usd_per_kg = decimal_from_setting(settings.DEFAULT_SHIPPING_USD_PER_KG)
    customer_markup_percent = decimal_from_setting(
        settings.DEFAULT_CUSTOMER_MARKUP_PERCENT
    )
    vat_multiplier = decimal_from_setting(settings.VAT_MULTIPLIER)

    shipping_usd = weight_kg * shipping_usd_per_kg
    customer_multiplier = Decimal("1") + (customer_markup_percent / Decimal("100"))

    final_price_gel = (
        (api_price_usd + shipping_usd)
        * customer_multiplier
        * usd_to_gel_rate
        * vat_multiplier
    )

    return final_price_gel.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)