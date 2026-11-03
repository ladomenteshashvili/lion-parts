"""The one place that computes prices (G§7.2).

    cost_c          = (S + W x R) x X_c          X = NBG GEL per USD for GEL, 1 for USD
    list_unit_price = round2(cost_c x (1 + M_c/100) x (1 + T_c/100))
    unit_price      = list_unit_price - discount_unit_amount   (G§7.3)

Every priced line stores the full PriceSnapshot; repricing reuses its S, W and R with the current
rate, markup, VAT and discounts."""

from dataclasses import asdict, dataclass, fields
from decimal import Decimal

from django.utils import timezone

from accounts.models import CustomerTariff, TariffMarkup
from parts.models import CarrierService

from .discounts import best_discount, round2
from .models import CurrencySettings, SiteSettings
from .rates import get_usd_rate

DECIMAL_FIELDS = ("supplier_price_usd", "weight_kg", "shipping_usd_per_kg", "markup_percent", "vat_percent",
                  "exchange_rate", "list_unit_price", "discount_unit_amount", "unit_price")


@dataclass(frozen=True)
class PriceInput:
    supplier_price_usd: Decimal
    weight_kg: Decimal
    shipping_usd_per_kg: Decimal


@dataclass(frozen=True)
class PriceSnapshot:
    supplier_price_usd: Decimal
    weight_kg: Decimal
    shipping_usd_per_kg: Decimal
    markup_percent: Decimal
    vat_percent: Decimal
    exchange_rate: Decimal          # NBG GEL per USD at pricing time (also for USD lines: fixed discounts)
    list_unit_price: Decimal
    discount_unit_amount: Decimal
    unit_price: Decimal
    discount_id: int | None
    currency: str

    def to_json(self):
        """For JSON storage (PartSearchLog.normalized_response): decimals as strings."""
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}

    @classmethod
    def from_json(cls, data):
        return cls(**{f.name: (Decimal(str(data[f.name])) if f.name in DECIMAL_FIELDS else data[f.name])
                      for f in fields(cls)})


def default_shipping_usd_per_kg():
    carrier = CarrierService.get_default()
    return carrier.usd_per_kg if carrier else SiteSettings.get().default_shipping_usd_per_kg


def markup_percent(customer, currency):
    """The customer's tariff markup for `currency`; the default tariff's when the tariff has none.
    A default tariff without a markup row for an active currency is a configuration error (500)."""
    default = CustomerTariff.objects.get(is_default=True)
    tariff = customer.get_tariff() if customer else default
    markup = TariffMarkup.objects.filter(tariff=tariff, currency=currency).first()
    return (markup or TariffMarkup.objects.get(tariff=default, currency=currency)).markup_percent


def price_offer(inp, *, customer, currency, part_number, brand, now=None, usd_rate=None):
    """Price one unit for `customer` (None = guest, default tariff) in `currency`.
    Pass `usd_rate` to price many rows with one rate lookup. Raises RateUnavailable."""
    now = now or timezone.now()
    rate = usd_rate if usd_rate is not None else get_usd_rate()
    x = rate if currency == "GEL" else Decimal("1")
    markup = markup_percent(customer, currency)
    vat = CurrencySettings.objects.get(currency=currency).vat_percent
    cost = (inp.supplier_price_usd + inp.weight_kg * inp.shipping_usd_per_kg) * x
    list_unit_price = round2(cost * (1 + markup / 100) * (1 + vat / 100))
    rule, discount = best_discount(customer, part_number, brand, list_unit_price, currency, rate, now)
    return PriceSnapshot(
        supplier_price_usd=inp.supplier_price_usd, weight_kg=inp.weight_kg,
        shipping_usd_per_kg=inp.shipping_usd_per_kg, markup_percent=markup, vat_percent=vat, exchange_rate=rate,
        list_unit_price=list_unit_price, discount_unit_amount=discount, unit_price=list_unit_price - discount,
        discount_id=rule.id if rule else None, currency=currency,
    )


def reprice(source, *, customer, currency, part_number=None, brand=None, usd_rate=None):
    """Recompute a stored line (a model with snapshot fields, or a snapshot dict) with the current
    rate, markup, VAT and discounts. S, W and R come from the source; part number and brand from
    the source unless given."""
    get = source.get if isinstance(source, dict) else lambda k, default=None: getattr(source, k, default)
    inp = PriceInput(Decimal(str(get("supplier_price_usd"))), Decimal(str(get("weight_kg"))),
                     Decimal(str(get("shipping_usd_per_kg"))))
    return price_offer(inp, customer=customer, currency=currency, part_number=part_number or get("part_number", ""),
                       brand=brand if brand is not None else get("brand", ""), usd_rate=usd_rate)
