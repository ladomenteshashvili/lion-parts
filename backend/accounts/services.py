"""Account services (G§3.1): views and admin call these, never the other way around."""

from django.conf import settings


def customer_language(customer=None, fallback=None):
    """Language for messages sent outside the customer's request (SMS, email, notifications):
    preferred_language, else `fallback` (e.g. the registration draft), else the default (G§5.8)."""
    codes = {code for code, _name in settings.LANGUAGES}
    for lang in (getattr(customer, "preferred_language", None), fallback):
        if lang in codes:
            return lang
    return settings.LANGUAGE_CODE
