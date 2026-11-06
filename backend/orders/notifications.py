"""Notifications (G§7.11): templates per language (LP-BE-1305), notify() and delivery (LP-BE-902)."""

import logging
import re

from django.conf import settings
from django.template import Context, Template, TemplateSyntaxError

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


ALLOWED_TAGS = {"if", "elif", "else", "endif"}
TAG = re.compile(r"{%-?\s*(\w+)")


def check_template(source):
    """Only {% if %} logic: no {% include %}, {% debug %}, {% load %}... (staff-edited templates, D24).
    Raises TemplateSyntaxError."""
    used = set(TAG.findall(source or ""))
    if used - ALLOWED_TAGS:
        raise TemplateSyntaxError(f"tags not allowed in notification templates: {', '.join(sorted(used - ALLOWED_TAGS))}")
    return Template(source or "")


def render_text(source, context):
    """Plain text (SMS / text email): no HTML escaping; only the restricted context (D24)."""
    return check_template(source).render(Context(context, autoescape=False)).strip()


def template_context(notification, link, title):
    """Primitive values only (D24): an edited template can't walk to other models or fields."""
    order, quote = notification.order, notification.quote_request
    return {"link": link, "title": title,
            "order": {"order_number": order.order_number} if order else None,
            "notification": {"quote_request": {"part_number": quote.part_number} if quote else None}}


# notify() and delivery (LP-BE-902)

DEDUP_SECONDS = 60
CREATE_ATTEMPTS = 3
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

    from django.db import OperationalError, transaction
    from django.utils import timezone

    from accounts.models import Customer

    from .models import Notification

    if customer is None or customer.anonymized_at:
        return None
    params = params or {}
    for attempt in range(1, CREATE_ATTEMPTS + 1):
        try:
            with transaction.atomic():
                # NO KEY UPDATE: serializes notify() for this customer without blocking (or deadlocking with)
                # other transactions that only insert rows pointing at the customer or the order.
                Customer.objects.select_for_update(no_key=True).filter(pk=customer.pk).first()
                recent = Notification.objects.filter(
                    customer=customer, event_type=event_type, text_params=params,
                    created_at__gte=timezone.now() - timedelta(seconds=DEDUP_SECONDS),
                    **{f"{name}": targets.get(name) for name in TARGETS})
                if recent.exists():
                    return None
                notification = Notification.objects.create(customer=customer, event_type=event_type,
                                                           message=message, text_params=params, **targets)
            break
        except OperationalError:  # deadlock / serialization failure: transient, try again
            if attempt == CREATE_ATTEMPTS:
                logger.error("The %s notification for customer %s was not created after %d attempts",
                             event_type, customer.pk, attempt, exc_info=True)
                return None
            logger.warning("Creating the %s notification: retry %d after a transient database error", event_type,
                           attempt)
        except Exception:  # a notification must never break the action that triggered it
            logger.exception("Creating the %s notification failed", event_type)
            return None
    try:
        send(notification, _channels(event_type))
    except Exception:  # the row exists (in-app); a delivery problem must not break the triggering action
        logger.exception("Delivering the %s notification failed", event_type)
    return notification


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
    from api.mail import transactional_footer

    customer, ticket = notification.customer, notification.support_ticket
    if customer is None:  # guest ticket reply
        if ticket is None or not ticket.contact:
            return None
        with translation.override(ticket.language):
            title = notification_title(notification)
            body = (f"{_('Lion Parts support replied to your request:')}\n\n{notification.message}\n\n"
                    f"{transactional_footer()}")
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
                f"{render_text(template.email_template, context)}\n\n{transactional_footer()}")


def send(notification, channels):
    """Deliver on `channels` (sms/email); each attempt is a NotificationDelivery row. Never raises.
    -> the delivery rows created."""
    if not channels:
        return []
    try:
        content = _content(notification)
    except Exception:
        logger.exception("Rendering notification %s failed", notification.pk)
        content = None
    if content is None:
        return []
    _language, phone, email, sms, subject, body = content
    deliveries = []
    if "sms" in channels and phone and "@" not in phone:
        deliveries.append(deliver_sms(notification, phone, sms))
    if "email" in channels and email and "@" in email:
        deliveries.append(deliver_email(notification, email, subject, body))
    return deliveries


def _delivery(notification, channel, recipient, status, *, message_id="", error=""):
    from .models import NotificationDelivery

    return NotificationDelivery.objects.create(notification=notification, channel=channel, recipient=recipient,
                                               status=status, provider_message_id=str(message_id)[:120],
                                               error=error[:500])


def deliver_sms(notification, phone, text):
    """sent: the provider accepted it (statusId 1); skipped: SMS is switched off (SENDER_GE_ENABLED=False, send_sms
    answers with its console stub, nothing leaves the server); failed: any error or other answer. The text is
    never logged (it holds the link token)."""
    from accounts.sms import send_sms

    from .models import NotificationDelivery

    try:
        result = send_sms(phone, text)
    except Exception as error:
        logger.warning("SMS for notification %s failed: %s", notification.pk, type(error).__name__)
        return _delivery(notification, "sms", phone, NotificationDelivery.STATUS_FAILED, error=str(error))
    result = result if isinstance(result, dict) else {}
    if result.get("provider") == "console":
        return _delivery(notification, "sms", phone, NotificationDelivery.STATUS_SKIPPED,
                         error="SMS provider disabled (SENDER_GE_ENABLED=False)")
    if str(result.get("statusId")) != "1":
        logger.warning("SMS for notification %s: the provider did not confirm it", notification.pk)
        return _delivery(notification, "sms", phone, NotificationDelivery.STATUS_FAILED,
                         error=f"unexpected provider answer (statusId={result.get('statusId')!r})")
    return _delivery(notification, "sms", phone, NotificationDelivery.STATUS_SENT,
                     message_id=result.get("messageId", ""))


NON_DELIVERING_EMAIL_BACKENDS = ("django.core.mail.backends.console.EmailBackend",
                                 "django.core.mail.backends.dummy.EmailBackend")


def deliver_email(notification, address, subject, body):
    """sent: the backend accepted the message; skipped: a backend that delivers nothing (console, dummy; refused in
    production); failed: an error, or no message accepted (send() returned 0)."""
    from django.core.mail import EmailMessage

    from api.mail import reply_to

    from .models import NotificationDelivery

    backend = settings.MAILERS.get("default", {}).get("BACKEND", "")  # Django 6.1 MAILERS (no EMAIL_BACKEND)
    if backend in NON_DELIVERING_EMAIL_BACKENDS:
        return _delivery(notification, "email", address, NotificationDelivery.STATUS_SKIPPED,
                         error=f"email backend delivers nothing ({backend.rsplit('.', 2)[-2]})")
    try:
        accepted = EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [address], reply_to=reply_to()).send()
    except Exception as error:
        logger.warning("Email for notification %s failed: %s", notification.pk, type(error).__name__)
        return _delivery(notification, "email", address, NotificationDelivery.STATUS_FAILED, error=str(error))
    if not accepted:
        logger.warning("Email for notification %s: no message accepted", notification.pk)
        return _delivery(notification, "email", address, NotificationDelivery.STATUS_FAILED,
                         error="the email backend accepted no message")
    return _delivery(notification, "email", address, NotificationDelivery.STATUS_SENT)


def resend(delivery):
    """Admin "resend" (LP-BE-1105): the same notification on the same channel again, as new delivery rows."""
    return send(delivery.notification, {delivery.channel})


# shortcut: synchronous SMS/email delivery; move to a task queue when send latency or retries become a problem.
