import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class SenderGeError(Exception):
    pass


def send_sms(destination, content):
    """
    Send SMS through Sender.ge.

    destination must be Georgian 9 digit mobile number without +995.
    """
    if not settings.SENDER_GE_ENABLED:
        # Never log the content (it may hold a code); the last digits identify the recipient enough.
        logger.warning("SMS not delivered: SENDER_GE_ENABLED=False (recipient …%s)", str(destination)[-2:])
        return {
            "provider": "console",
            "messageId": "console",
            "statusId": 1,
            "destination": destination,
            "content": content,
        }

    if not settings.SENDER_GE_API_KEY:
        raise SenderGeError("SENDER_GE_API_KEY is not configured")

    payload = {
        "apikey": settings.SENDER_GE_API_KEY,
        "smsno": settings.SENDER_GE_SMSNO,
        "destination": destination,
        "content": content,
        "priority": settings.SENDER_GE_PRIORITY,
    }

    try:
        response = requests.post(
            settings.SENDER_GE_SEND_URL,
            data=payload,
            timeout=15,
        )
    except requests.RequestException as exc:
        raise SenderGeError("Sender.ge request failed") from exc

    if response.status_code != 200:
        raise SenderGeError(f"Sender.ge returned HTTP {response.status_code}")

    try:
        data = response.json()
    except ValueError:
        data = {"raw": response.text}

    status_id = data.get("statusId") if isinstance(data, dict) else None

    if status_id is not None and str(status_id) != "1":
        raise SenderGeError(f"Sender.ge returned statusId {status_id}")

    return data