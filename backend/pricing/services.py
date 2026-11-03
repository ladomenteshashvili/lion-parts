"""Read-side helpers for the public configuration (G§8.1)."""

from api.i18n import tr

from .currency import SYMBOLS, allowed_currencies, default_currency
from .models import BASE_CURRENCY, ExchangeRate, PickupPoint, SiteSettings


def latest_usd_rate():
    return ExchangeRate.objects.filter(currency="USD").order_by("-date").first()


def meta_pricing():
    """Currency mode (whether to show a currency selector), the latest stored USD rate, price validity,
    active pickup points and delivery areas, names in the request language."""
    site, rate = SiteSettings.get(), latest_usd_rate()
    return {
        "currencies": [{"code": code, "symbol": SYMBOLS[code]} for code in allowed_currencies()],
        "multi_currency_enabled": site.multi_currency_enabled,
        "default_currency": default_currency(),
        "base_currency": BASE_CURRENCY,
        "price_valid_business_days": site.price_valid_business_days,
        "usd_rate": {"rate": f"{rate.rate:.4f}", "date": rate.date.isoformat()} if rate else None,
        "pickup_points": [
            {"id": p.pk, "name": tr(p.name_i18n), "address": tr(p.address_i18n), "working_hours": tr(p.working_hours_i18n)}
            for p in PickupPoint.objects.filter(is_active=True)
        ],
        "delivery": {
            "country": "GE",
            "cities": [{"code": c["code"], "name": tr(c.get("name"))} for c in site.delivery_cities],
            "districts": [{"code": d["code"], "city": d["city"], "name": tr(d.get("name"))}
                          for d in site.delivery_districts],
        },
    }
