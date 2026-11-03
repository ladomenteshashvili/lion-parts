"""Test factories (not used at runtime)."""

from decimal import Decimal

from django.utils import timezone

from .models import Order, OrderItem
from .services import assign_order_number, recalculate_order

D = Decimal


def make_order(customer, *, status=Order.STATUS_PAYMENT_PENDING, currency="GEL", rate=D("2.6000"),
               items=(("PART-1", D("100.00"), 1),), payment_option="full", payment_method="invoice"):
    """An order with items (part_number, unit_price_usd, quantity), converted at `rate`."""
    order = Order.objects.create(
        customer=customer, status=status, currency=currency, exchange_rate=rate if currency == "GEL" else D("1"),
        billing_snapshot={"first_name": customer.first_name, "last_name": customer.last_name},
        delivery_method="pickup", payment_method=payment_method, payment_option=payment_option,
        terms_accepted_at=timezone.now())
    assign_order_number(order)
    factor = order.exchange_rate
    for part_number, usd, quantity in items:
        price = (usd * factor).quantize(D("0.01"))
        OrderItem.objects.create(
            order=order, offer_id=f"O-{part_number}", part_number=part_number, name=part_number, quantity=quantity,
            supplier_price_usd=usd, shipping_usd_per_kg=D("8"), markup_percent=D("20"), vat_percent=D("18"),
            list_price_usd=usd, discount_usd=D("0"), unit_price_usd=usd,
            list_unit_price=price, discount_unit_amount=D("0"), unit_price=price)
    recalculate_order(order)
    order.amount_due_now = order.total
    order.save(update_fields=["amount_due_now"])
    return order
