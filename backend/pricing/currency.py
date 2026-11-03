"""Customer currencies (G§4.6). USD is always the canonical pricing currency.

multi_currency_enabled off: customers see and pay GEL only.  On: USD (default) or GEL."""

from decimal import Decimal

from .models import BASE_CURRENCY, SiteSettings
from .rates import get_usd_rate

SYMBOLS = {"GEL": "₾", "USD": "$"}


def allowed_currencies():
    return ["USD", "GEL"] if SiteSettings.get().multi_currency_enabled else ["GEL"]


def default_currency():
    return BASE_CURRENCY if SiteSettings.get().multi_currency_enabled else "GEL"


def effective_currency(currency):
    """The currency to show a stored price in now: its own while allowed, else the default
    (e.g. a USD quote after multi-currency was switched off is shown in GEL)."""
    return currency if currency in allowed_currencies() else default_currency()


def display_rate(currency):
    """The factor from USD to `currency`: 1 for USD, the current NBG rate for GEL.
    Raises RateUnavailable rather than converting with an outdated rate."""
    return Decimal("1") if currency == BASE_CURRENCY else get_usd_rate()
