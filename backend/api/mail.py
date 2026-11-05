"""Templated, translated emails (G§5.8, G§7.11). One template per email; texts come from the catalogs."""

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import translation
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

SUBJECTS = {
    "verify_email": _("Confirm your email address"),
    "password_reset": _("Reset your password"),
    "email_change_confirm": _("Confirm your new email address"),
    "email_changed_notice": _("Your email address was changed"),
}


def frontend_link(path, language, **params):
    """Frontend route from G§4.8 with the recipient's language: /verify-email?token=…&lang=ru."""
    return f"{settings.FRONTEND_BASE_URL.rstrip('/')}{path}?{urlencode({**params, 'lang': language})}"


def send_templated_email(to, template, context, language):
    """Render templates/emails/<template>.txt/.html in `language` and send. Never raises: a failed
    delivery must not break the action that triggered it; returns False and logs instead."""
    try:
        with translation.override(language):
            subject = str(SUBJECTS[template])
            text = render_to_string(f"emails/{template}.txt", context)
            html = render_to_string(f"emails/{template}.html", context)
        message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to])
        message.attach_alternative(html, "text/html")
        message.send()
        return True
    except Exception as error:  # the error type only: SMTP errors can carry the recipient, the context holds tokens
        logger.error("Sending the %s email failed: %s", template, type(error).__name__)
        return False
