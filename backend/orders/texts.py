"""System texts rendered at read time in the request language (G§5.8): the DB stores codes and params,
never rendered text."""

import logging

from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

logger = logging.getLogger(__name__)

CURRENCY_SYMBOLS = {"GEL": "₾", "USD": "$"}

EVENT_TEXTS = {
    "weight_customer_notice": _(
        "The price is calculated with the estimated weight you entered. The final weight is set when the part "
        "arrives at the US warehouse. If it differs, the final amount may change: you may pay extra or get a refund."
    ),
    "attention_courier_fee": _("Please confirm the delivery fee."),
    "attention_balance_due": _("Remaining amount: {amount}."),
    "attention_ready_for_pickup": _("Your parts are ready for pickup."),
}

# Texts with plural forms: (singular, plural) msgids, rendered with ngettext and the count `n`.
PLURAL_TEXTS = {
    "attention_action_required": ("{n} item needs your confirmation.", "{n} items need your confirmation."),
}


def format_money(amount, currency):
    return f"{CURRENCY_SYMBOLS.get(currency, currency + ' ')}{amount:.2f}"


def render_text(code, **params):
    """EVENT_TEXTS[code] in the active language; an unknown code is logged and returned as is."""
    text = EVENT_TEXTS.get(code)
    if text is None:
        logger.warning("Unknown system text code %s", code)
        return code
    return str(text).format(**params)


def render_plural(code, n, **params):
    singular, plural = PLURAL_TEXTS[code]
    return ngettext(singular, plural, n).format(n=n, **params)


def _plural_msgids_for_makemessages():  # never called: lets makemessages find the plural msgids
    ngettext("{n} item needs your confirmation.", "{n} items need your confirmation.", 1)
