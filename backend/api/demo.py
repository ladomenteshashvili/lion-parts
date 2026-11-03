"""Demo/staging-only endpoints (G§4.10, G§8.10). Mounted only when ENABLE_DEMO_ENDPOINTS=True,
so in production these routes don't exist at all. Never enable demo settings in production."""

import re
from datetime import timezone
from email.utils import parsedate_to_datetime

from django.conf import settings
from django.core import mail
from django.core.mail.backends.locmem import EmailBackend as LocmemBackend
from django.utils.module_loading import import_string
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

LINK = re.compile(r"https?://\S+")
OUTBOX_LIMIT = 50


def _uses_locmem():
    backend = settings.MAILERS.get("default", {}).get("BACKEND", "")
    return issubclass(import_string(backend), LocmemBackend) if backend else False


def _sent_at(message):
    date = message.extra_headers.get("Date")
    return parsedate_to_datetime(date).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if date else None


@api_view(["GET"])
@permission_classes([AllowAny])
def outbox(request):
    """Emails captured by the in-memory mailer (tests, single-process runserver); [] otherwise."""
    if not _uses_locmem():
        return Response([])
    return Response([
        {"to": list(m.to), "subject": m.subject, "body": m.body,
         "links": LINK.findall(m.body), "sent_at": _sent_at(m)}
        for m in getattr(mail, "outbox", [])[-OUTBOX_LIMIT:]
    ])
