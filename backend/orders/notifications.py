"""Notifications (G§7.11): templates per language (LP-BE-1305), notify() and delivery (LP-BE-902)."""

import logging

from django.conf import settings
from django.template import Context, Template

from .models import NotificationTemplate
from .texts import DEFAULT_TITLES

logger = logging.getLogger(__name__)


def notification_title(notification):
    return str(DEFAULT_TITLES[notification.event_type])


def pick_template(event_type, language):
    """The template in `language`, else in the default language, else None (nothing to send)."""
    templates = {t.language: t for t in NotificationTemplate.objects.filter(
        event_type=event_type, language__in=(language, settings.LANGUAGE_CODE))}
    return templates.get(language) or templates.get(settings.LANGUAGE_CODE)


def render_text(source, context):
    """Plain text (SMS / text email): no HTML escaping; only the restricted context (D24)."""
    return Template(source).render(Context(context, autoescape=False)).strip()


def template_context(notification, link, title):
    """Primitive values only (D24): an edited template can't walk to other models or fields."""
    order, quote = notification.order, notification.quote_request
    return {"link": link, "title": title,
            "order": {"order_number": order.order_number} if order else None,
            "notification": {"quote_request": {"part_number": quote.part_number} if quote else None}}
