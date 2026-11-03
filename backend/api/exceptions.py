"""One error envelope for every API error (G§4.4, G§5.6): {"code", "detail", ...extra[, "fields"]}."""

import math

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from django.utils.translation import gettext_lazy as _
from rest_framework import exceptions
from rest_framework.views import exception_handler as drf_handler

from .errors import MESSAGES, ApiError

# Messages for DRF's built-in exception codes (English msgids).
DEFAULT_MESSAGES = {
    "not_authenticated": MESSAGES["not_authenticated"],
    "permission_denied": MESSAGES["permission_denied"],
    "not_found": MESSAGES["not_found"],
    "throttled": MESSAGES["throttled"],
    "method_not_allowed": _("Method not allowed."),
    "parse_error": _("Malformed request."),
}


def _flatten(details, prefix=""):
    """{"delivery": {"district": [{message, code}]}} -> {"delivery.district": [{code, message}]}"""
    out = {}
    if isinstance(details, dict):
        for key, val in details.items():
            out.update(_flatten(val, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(details, list):
        if all(isinstance(d, dict) and "code" in d and "message" in d for d in details):
            out[prefix or "non_field_errors"] = [{"code": d["code"], "message": str(d["message"])} for d in details]
        else:  # many=True nested serializers / list fields
            for i, d in enumerate(details):
                out.update(_flatten(d, f"{prefix}.{i}" if prefix else str(i)))
    return out


def exception_handler(exc, context):
    # Same conversions DRF does internally, done here so the codes below see DRF exceptions.
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    response = drf_handler(exc, context)
    if response is None:
        return None  # unhandled -> 500 via handler500

    if isinstance(exc, exceptions.ValidationError):
        response.data = {"code": "validation_error", "detail": str(MESSAGES["validation_error"]),
                         "fields": _flatten(exc.get_full_details())}
        return response

    if isinstance(exc, ApiError):
        response.data = {"code": exc.code, "detail": str(exc.detail), **exc.extra}
        return response

    codes = exc.get_codes()
    code = codes if isinstance(codes, str) else "error"
    response.data = {"code": code, "detail": str(DEFAULT_MESSAGES.get(code, exc.detail))}
    if isinstance(exc, exceptions.Throttled) and exc.wait is not None:
        response.data["retry_after"] = math.ceil(exc.wait)  # never tell clients to retry too early
    return response
