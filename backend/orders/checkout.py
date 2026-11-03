"""Checkout (G§7.12, G§8.6)."""

from decimal import Decimal

from cart.services import cart_data, get_cart
from pricing.discounts import round2
from pricing.services import meta_pricing


def payment_options(total, tariff):
    """split_70_30 (default) and full for split tariffs; only full otherwise."""
    full = {"value": "full", "due_now": f"{total:.2f}", "balance": "0.00"}
    if tariff.payment_terms != "split_70_30":
        return [{**full, "default": True}]
    due_now = round2(total * tariff.split_upfront_percent / 100)
    return [{"value": "split_70_30", "due_now": f"{due_now:.2f}", "balance": f"{total - due_now:.2f}", "default": True},
            {**full, "default": False}]


def checkout_options(request, customer):
    cart = cart_data(request, get_cart(request, create=False))  # current rate, current statuses
    tariff = customer.get_tariff()
    company = getattr(customer, "company", None)
    pricing = meta_pricing()
    return {
        "payer": {"name": customer.full_name, "account_type": customer.account_type,
                  "company_name": company.company_official_name if company else None,
                  "phone": customer.phone, "email": customer.user.email},
        "currency": cart["currency"],
        "payment_terms": tariff.payment_terms,
        "payment_options": payment_options(Decimal(cart["summary"]["total"]), tariff),
        "payment_methods": ["card", "invoice"],
        "pickup_points": pricing["pickup_points"],
        "delivery": pricing["delivery"],
        "summary": cart["summary"],
        "checkout_blocked": cart["checkout_blocked"],
        "checkout_blocked_reasons": cart["checkout_blocked_reasons"],
    }
