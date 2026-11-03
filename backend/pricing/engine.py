"""The one place that computes prices (G§7.2). USD is the canonical pricing currency.

    list_price_usd = round2((S + W x R) x (1 + M/100) x (1 + T/100))     M: tariff markup, T: VAT
    unit_price_usd = list_price_usd - discount_usd                       (G§7.3, discounts in USD)

The canonical USD amounts never depend on the exchange rate. A customer-facing GEL price is a pure
conversion of them (convert()):

    unit_price (GEL)           = round2(unit_price_usd x X)              X = NBG GEL per USD
    list_unit_price (GEL)      = round2(list_price_usd x X)
    discount_unit_amount (GEL) = list_unit_price - unit_price            (so every line adds up)

Repricing for a tariff/discount change reuses the stored S, W and R (reprice()); an exchange-rate
change only reconverts (convert()) and never touches the USD amounts."""

import logging
from dataclasses import asdict, dataclass, fields
from decimal import Decimal

from django.utils import timezone

from accounts.models import CustomerTariff
from parts.models import CarrierService

from .discounts import best_discount, round2
from .models import BASE_CURRENCY, SiteSettings

logger = logging.getLogger(__name__)

DECIMAL_FIELDS = ("supplier_price_usd", "weight_kg", "shipping_usd_per_kg", "markup_percent", "vat_percent",
                  "list_price_usd", "discount_usd", "unit_price_usd")


@dataclass(frozen=True)
class PriceInput:
    supplier_price_usd: Decimal
    weight_kg: Decimal
    shipping_usd_per_kg: Decimal


@dataclass(frozen=True)
class PriceSnapshot:
    """Canonical USD price with everything it was computed from (stored on offers, quotes, cart and
    order lines)."""

    supplier_price_usd: Decimal
    weight_kg: Decimal
    shipping_usd_per_kg: Decimal
    markup_percent: Decimal
    vat_percent: Decimal
    list_price_usd: Decimal
    discount_usd: Decimal
    unit_price_usd: Decimal
    discount_id: int | None

    def to_json(self):
        """For JSON storage (PartSearchLog.normalized_response): decimals as strings."""
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}

    @classmethod
    def from_json(cls, data):
        return cls(**{f.name: (Decimal(str(data[f.name])) if f.name in DECIMAL_FIELDS else data[f.name])
                      for f in fields(cls)})


@dataclass(frozen=True)
class Converted:
    """A canonical price shown in a customer currency."""

    currency: str
    exchange_rate: Decimal  # GEL per USD for GEL; 1 for USD
    list_unit_price: Decimal
    discount_unit_amount: Decimal
    unit_price: Decimal


class PricingConfigurationError(Exception):
    """Pricing data is missing (e.g. the default tariff has no markup). Never priced as zero: the
    request fails (500, logged) until an operator fixes the configuration in admin."""


def default_shipping_usd_per_kg():
    carrier = CarrierService.get_default()
    return carrier.usd_per_kg if carrier else SiteSettings.get().default_shipping_usd_per_kg


def markup_percent(customer):
    """The customer's tariff markup, else the default tariff's. Never a silent zero."""
    tariff = customer.get_tariff() if customer else None
    if tariff is None or tariff.markup_percent is None:
        tariff = CustomerTariff.objects.filter(is_default=True).first()
    if tariff is None or tariff.markup_percent is None:
        logger.error("The default tariff has no markup")
        raise PricingConfigurationError("the default tariff has no markup")
    return tariff.markup_percent


def price_offer(inp, *, customer, part_number, brand, now=None):
    """Canonical USD price of one unit for `customer` (None = guest: default tariff). Needs no rate."""
    markup = markup_percent(customer)
    vat = SiteSettings.get().vat_percent
    list_price = round2((inp.supplier_price_usd + inp.weight_kg * inp.shipping_usd_per_kg)
                        * (1 + markup / 100) * (1 + vat / 100))
    rule, discount = best_discount(customer, part_number, brand, list_price, now or timezone.now())
    return PriceSnapshot(
        supplier_price_usd=inp.supplier_price_usd, weight_kg=inp.weight_kg,
        shipping_usd_per_kg=inp.shipping_usd_per_kg, markup_percent=markup, vat_percent=vat,
        list_price_usd=list_price, discount_usd=discount, unit_price_usd=list_price - discount,
        discount_id=rule.id if rule else None,
    )


def reprice(source, *, customer, part_number=None, brand=None):
    """Recompute the canonical price of a stored line (a model with snapshot fields, or a snapshot
    dict) with the current tariff, VAT and discounts; S, W and R come from the source."""
    get = source.get if isinstance(source, dict) else lambda k, default=None: getattr(source, k, default)
    inp = PriceInput(Decimal(str(get("supplier_price_usd"))), Decimal(str(get("weight_kg"))),
                     Decimal(str(get("shipping_usd_per_kg"))))
    return price_offer(inp, customer=customer, part_number=part_number or get("part_number", ""),
                       brand=brand if brand is not None else get("brand", ""))


def convert(source, currency, rate):
    """Show a canonical price (snapshot, model or dict with list_price_usd/unit_price_usd) in
    `currency`. `rate` = GEL per USD (ignored for USD)."""
    get = source.get if isinstance(source, dict) else lambda k: getattr(source, k)
    list_usd, unit_usd = Decimal(str(get("list_price_usd"))), Decimal(str(get("unit_price_usd")))
    if currency == BASE_CURRENCY:
        return Converted(currency, Decimal("1"), list_usd, list_usd - unit_usd, unit_usd)
    list_price, unit_price = round2(list_usd * rate), round2(unit_usd * rate)
    return Converted(currency, rate, list_price, list_price - unit_price, unit_price)
