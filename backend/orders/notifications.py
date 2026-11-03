"""Notifications (G§7.11): templates per language (LP-BE-1305), notify() and delivery (LP-BE-902)."""

import logging

from django.conf import settings
from django.template import Context, Template

from .models import NotificationTemplate
from .texts import DEFAULT_TITLES

logger = logging.getLogger(__name__)


PUBLIC_LINK_DAYS = 30  # owner decision D23


def notification_title(notification):
    return str(DEFAULT_TITLES[notification.event_type])


def notification_target(n):
    """G§8.0 Notification.target."""
    if n.event_type == "price_ready" and n.quote_request_id:
        return {"screen": "search_history", "quote_request_id": n.quote_request_id}
    if n.event_type == "action_required" and n.order_id:
        return {"screen": "decision", "order_number": n.order.order_number, "item_id": n.item_id}
    if n.support_ticket_id and not n.order_id:
        return {"screen": "support_ticket", "ticket_id": n.support_ticket_id}
    return {"screen": "order", "order_number": n.order.order_number if n.order_id else None}


def notification_data(n):
    """The customer's own notification (authenticated, owner-scoped): operator text as written."""
    return {"id": n.pk, "event_type": n.event_type, "title": notification_title(n), "message": n.message,
            "target": notification_target(n), "blocking": n.event_type == "support_reply", "read_at": n.read_at,
            "created_at": n.created_at}


def public_notification_data(n):
    """GET /api/public/n/<token>/ (D23): no names, phone, email, VIN, amounts, addresses or operator text."""
    return {"event_type": n.event_type, "title": notification_title(n), "message": "",
            "order_number": n.order.order_number if n.order_id else None,
            "item_part_number": n.item.part_number if n.item_id else None,
            "target": notification_target(n), "requires_login": True}


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


# notify() and delivery (LP-BE-902)

DEDUP_SECONDS = 60
TARGETS = ("order", "item", "quote_request", "support_message", "support_ticket")


def _channels(event_type):
    from .models import NotificationChannelSetting

    setting = NotificationChannelSetting.objects.filter(event_type=event_type).first()
    channel = setting.channel if setting else "none"
    return {"sms", "email"} if channel == "both" else set() if channel == "none" else {channel}


def notify(customer, event_type, *, message="", params=None, **targets):
    """G§7.11 for a customer: the in-app row, then SMS/email per the channel setting in the customer's language.
    The same notification (event, targets, params) within DEDUP_SECONDS is not created or sent twice
    (repeated callbacks, double clicks; decided under a row lock on the customer). Never raises.
    -> the Notification (or None when skipped)."""
    from datetime import timedelta

    from django.db import transaction
    from django.utils import timezone

    from accounts.models import Customer

    from .models import Notification

    try:
        if customer is None or customer.anonymized_at:
            return None
        params = params or {}
        with transaction.atomic():
            Customer.objects.select_for_update().filter(pk=customer.pk).first()
            recent = Notification.objects.filter(
                customer=customer, event_type=event_type, text_params=params,
                created_at__gte=timezone.now() - timedelta(seconds=DEDUP_SECONDS),
                **{f"{name}": targets.get(name) for name in TARGETS})
            if recent.exists():
                return None
            notification = Notification.objects.create(customer=customer, event_type=event_type, message=message,
                                                       text_params=params, **targets)
        send(notification, _channels(event_type))
        return notification
    except Exception:  # a notification must never break the action that triggered it
        logger.exception("Creating the %s notification failed", event_type)
        return None


def notify_after_commit(customer, event_type, **kwargs):
    """notify() once the surrounding transaction commits (a rolled-back action sends nothing)."""
    from django.db import transaction

    transaction.on_commit(lambda: notify(customer, event_type, **kwargs), robust=True)


def notify_contact(ticket, reply):
    """Guest ticket reply (G§7.11): delivered to ticket.contact (email if it has "@", else SMS) in the ticket's
    language, following the support_reply channel setting. The reply text is included: a guest has no account
    to read it in (D24). -> the Notification."""
    from .models import Notification

    notification = Notification.objects.create(event_type="support_reply", support_ticket=ticket, message=reply)
    wanted = "email" if "@" in ticket.contact else "sms"
    send(notification, _channels("support_reply") & {wanted})
    return notification


def _content(notification):
    """(language, sms recipient, email recipient, sms text, email subject, email body); None if nothing to send."""
    from django.utils import translation
    from django.utils.translation import gettext as _

    from accounts.services import customer_language

    customer, ticket = notification.customer, notification.support_ticket
    if customer is None:  # guest ticket reply
        if ticket is None or not ticket.contact:
            return None
        with translation.override(ticket.language):
            title = notification_title(notification)
            body = _("Lion Parts support replied to your request:\n\n%(reply)s") % {"reply": notification.message}
            return (ticket.language, ticket.contact, ticket.contact, f"Lion Parts: {notification.message}",
                    f"Lion Parts: {title}", body)
    language = customer_language(customer)
    template = pick_template(notification.event_type, language)
    if template is None:
        return None
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    quote = notification.quote_request
    path = f"/q/{quote.notification_token}" if quote else f"/n/{notification.token}"
    with translation.override(language):
        context = template_context(notification, f"{base}{path}?lang={language}", notification_title(notification))
        return (language, customer.phone, customer.user.email, render_text(template.sms_template, context),
                " ".join(render_text(template.email_subject, context).split()),
                render_text(template.email_template, context))


def send(notification, channels):
    """Deliver on `channels` (sms/email); each attempt is a NotificationDelivery row. Never raises."""
    if not channels:
        return
    try:
        content = _content(notification)
    except Exception:
        logger.exception("Rendering notification %s failed", notification.pk)
        content = None
    if content is None:
        return
    _language, phone, email, sms, subject, body = content
    if "sms" in channels and phone and "@" not in phone:
        deliver_sms(notification, phone, sms)
    if "email" in channels and email and "@" in email:
        deliver_email(notification, email, subject, body)


def deliver_sms(notification, phone, text):
    from accounts.sms import send_sms

    from .models import NotificationDelivery

    try:
        result = send_sms(phone, text)
        message_id = str(result.get("messageId", "")) if isinstance(result, dict) else ""
        return NotificationDelivery.objects.create(notification=notification, channel="sms", recipient=phone,
                                                   status="sent", provider_message_id=message_id[:120])
    except Exception as error:  # the text is never logged (it holds the link token)
        logger.warning("SMS for notification %s failed: %s", notification.pk, type(error).__name__)
        return NotificationDelivery.objects.create(notification=notification, channel="sms", recipient=phone,
                                                   status="failed", error=str(error)[:500])


def deliver_email(notification, address, subject, body):
    from django.core.mail import send_mail

    from .models import NotificationDelivery

    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [address])
        return NotificationDelivery.objects.create(notification=notification, channel="email", recipient=address,
                                                   status="sent")
    except Exception as error:
        logger.warning("Email for notification %s failed: %s", notification.pk, type(error).__name__)
        return NotificationDelivery.objects.create(notification=notification, channel="email", recipient=address,
                                                   status="failed", error=str(error)[:500])


def resend(delivery):
    """Admin "resend" (LP-BE-1105): the same notification on the same channel again, as a new delivery row."""
    send(delivery.notification, {delivery.channel})
# shortcut: synchronous SMS/email delivery; move to a task queue when send latency or retries become a problem.
