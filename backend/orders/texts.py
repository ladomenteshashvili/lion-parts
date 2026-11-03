"""System texts rendered at read time in the request language (G§5.8): the DB stores codes, never text.
LP-BE-1304 adds the order event, timeline and banner texts."""

from django.utils.translation import gettext_lazy as _

EVENT_TEXTS = {
    "weight_customer_notice": _(
        "The price is calculated with the estimated weight you entered. The final weight is set when the part "
        "arrives at the US warehouse. If it differs, the final amount may change: you may pay extra or get a refund."
    ),
}


def render_text(code, **params):
    return str(EVENT_TEXTS[code]).format(**params)
