"""Read-side helpers for the public configuration (G§8.1)."""

from api.i18n import tr

from .models import CurrencySettings, ExchangeRate, PickupPoint, SiteSettings

CURRENCY_SYMBOLS = {"GEL": "₾", "USD": "$"}


def latest_usd_rate():
    return ExchangeRate.objects.filter(currency="USD").order_by("-date").first()


def meta_pricing():
    """Active currencies, the latest stored USD rate, active pickup points and delivery areas,
    names in the request language."""
    site, rate = SiteSettings.get(), latest_usd_rate()
    return {
        "currencies": [{"code": code, "symbol": CURRENCY_SYMBOLS.get(code, code)}
                       for code in CurrencySettings.active_codes()],
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
