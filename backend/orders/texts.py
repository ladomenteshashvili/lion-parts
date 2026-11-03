"""System texts rendered at read time in the request language (G§5.8, LP-BE-1304): the DB stores codes and
params, never rendered text. Operator-written texts (action_message, support replies) stay as typed."""

import logging
from decimal import Decimal

from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

logger = logging.getLogger(__name__)

CURRENCY_SYMBOLS = {"GEL": "₾", "USD": "$"}

EVENT_TEXTS = {
    "weight_customer_notice": _(
        "The price is calculated with the estimated weight you entered. The final weight is set when the part "
        "arrives at the US warehouse. If it differs, the final amount may change: you may pay extra or get a refund."
    ),
    # item events
    "order_item_added": _("The part was added to the order."),
    "payment_confirmed": _("Payment confirmed."),
    "status_created": _("Order placed."),
    "status_payment_confirmed": _("Payment confirmed."),
    "status_checking": _("We are checking the part with the supplier."),
    "status_action_required": _("Your confirmation is needed."),
    "status_purchased": _("The part was purchased."),
    "status_received_usa": _("The part arrived at the US warehouse."),
    "status_shipped_to_georgia": _("The part was shipped to Georgia."),
    "status_received_georgia": _("The part arrived in Georgia."),
    "status_ready_for_pickup": _("The part is ready for pickup."),
    "status_completed": _("The part was handed over."),
    "status_cancelled": _("The part was cancelled."),
    "action_requested_price_change": _("The price changed. New price: {amount}."),
    "action_requested_eta_change": _("The delivery time changed: {eta_min_days}-{eta_max_days} days."),
    "action_requested_alternative_required": _("An alternative part is proposed: {part_number}."),
    "action_requested_fitment_issue": _("The part may not fit your vehicle. Please confirm."),
    "action_requested_weight_change": _("The weight changed to {weight_kg} kg. New price: {amount}."),
    "action_requested_other": _("Please review the message from our team."),
    "action_accepted": _("You accepted the change."),
    "item_cancelled": _("The part was cancelled."),
    "notice_acknowledged": _("You acknowledged the notice."),
    # system support messages
    "courier_fee_accepted": _("Customer accepted the courier fee: {amount}."),
    "courier_fee_declined": _("Customer declined the courier fee."),
    # attention banners (the plural one is in PLURAL_TEXTS)
    "attention_courier_fee": _("Please confirm the delivery fee."),
    "attention_balance_due": _("Remaining amount: {amount}."),
    "attention_ready_for_pickup": _("Your parts are ready for pickup."),
    # timeline titles
    "order_created": _("Order placed."),
    "payment_confirmed_split": _("Payment confirmed ({percent}%)."),
    "order_cancelled": _("Order cancelled."),
}

# Texts with plural forms: (singular, plural) msgids, rendered with ngettext and the count `n`.
PLURAL_TEXTS = {
    "attention_action_required": ("{n} item needs your confirmation.", "{n} items need your confirmation."),
}


def format_money(amount, currency):
    return f"{CURRENCY_SYMBOLS.get(currency, currency + ' ')}{Decimal(amount):.2f}"


def render_text(code, **params):
    """EVENT_TEXTS[code] in the active language. `amount` + `currency` params are shown as money. An unknown
    code or a missing param is logged and the code returned: a stored text never breaks a response."""
    text = EVENT_TEXTS.get(code)
    if text is None:
        logger.warning("Unknown system text code %s", code)
        return code
    if "amount" in params and "currency" in params:
        params = {**params, "amount": format_money(params["amount"], params["currency"])}
    try:
        return str(text).format(**params)
    except (KeyError, IndexError, ValueError):
        logger.warning("System text %s could not be rendered with %s", code, sorted(params), exc_info=True)
        return code


def render_plural(code, n, **params):
    singular, plural = PLURAL_TEXTS[code]
    return ngettext(singular, plural, n).format(n=n, **params)


def _plural_msgids_for_makemessages():  # never called: lets makemessages find the plural msgids
    ngettext("{n} item needs your confirmation.", "{n} items need your confirmation.", 1)
