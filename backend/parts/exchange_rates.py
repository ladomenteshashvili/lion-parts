from decimal import Decimal

import requests
from django.conf import settings


class ExchangeRateError(Exception):
    pass


def get_usd_sell_rate() -> Decimal:
    response = requests.get(
        settings.KURSI_PUBLIC_CURRENCIES_URL,
        timeout=10,
    )
    response.raise_for_status()

    currencies = response.json()

    for currency in currencies:
        if (
            currency.get("baseCurrencyCode") == "GEL"
            and currency.get("secondaryCurrencyCode") == "USD"
        ):
            sell_rate = currency.get("sellRate")

            if sell_rate is None:
                raise ExchangeRateError("USD sellRate is missing")

            return Decimal(str(sell_rate))

    raise ExchangeRateError("USD rate was not found")