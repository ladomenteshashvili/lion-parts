"""Discount selection (G§7.3): no stacking, the biggest discount wins, ties -> the lower rule id."""

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q

from accounts.validators import normalize_part_number

from .models import Discount

ZERO = Decimal("0.00")


def round2(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def best_discount(customer, part_number, brand, list_price_usd, now):
    """-> (rule or None, canonical USD discount per unit). Fixed values are USD per unit."""
    # shortcut: in-memory matching; add DB-side filtering if rules exceed a few thousand
    rules = Discount.objects.filter(is_active=True).filter(
        Q(valid_from__isnull=True) | Q(valid_from__lte=now),
        Q(valid_to__isnull=True) | Q(valid_to__gte=now),
    )
    part = normalize_part_number(part_number)
    tariff_id = customer.get_tariff().id if customer else None

    def matches(rule):
        if rule.customer_id and (not customer or rule.customer_id != customer.id):
            return False
        if rule.tariff_id and (not customer or rule.tariff_id != tariff_id):
            return False
        if rule.part_number and rule.part_number != part:
            return False
        if rule.brand and rule.brand.lower() != (brand or "").lower():
            return False
        return True

    def amount(rule):
        raw = list_price_usd * rule.value / 100 if rule.kind == Discount.KIND_PERCENT else rule.value
        return min(round2(raw), list_price_usd)

    best = max((r for r in rules if matches(r)), key=lambda r: (amount(r), -r.id), default=None)
    return best, amount(best) if best else ZERO
