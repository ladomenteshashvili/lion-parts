"""Business error codes (G§9) and ApiError. msgids are English; ka/ru live in locale/*.po."""

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException

MESSAGES = {
    "validation_error": _("Please check the highlighted fields."),
    "invalid_credentials": _("Incorrect email or password."),
    "otp_invalid": _("The code is incorrect."),
    "otp_expired": _("The code has expired. Request a new one."),
    "otp_attempts_exceeded": _("Too many attempts. You can request a new code later."),
    "registration_expired": _("Your registration session has expired. Please fill in the form again."),
    "token_invalid": _("This link is no longer valid."),
    "invalid_callback": _("Invalid callback."),  # provider-facing, never shown to users
    "not_authenticated": _("Please sign in."),
    "csrf_failed": _("Security check failed. Please refresh the page."),
    "verification_required": _("Phone and email must be verified before payment."),
    "phone_verification_required": _("Phone verification is required."),
    "weight_entry_not_allowed": _("Weight entry is not available for your account."),
    "quote_request_not_allowed": _("Price requests are not available for your account."),
    "permission_denied": _("This action is not allowed."),
    "not_found": _("Not found."),
    "email_taken": _("This email is already linked to another account."),
    "already_verified": _("Email is already verified."),
    "otp_resend_too_soon": _("You can't request a new code yet."),
    "offer_expired": _("The price has expired. Refresh to get the current price."),
    "offer_not_ready": _("The price for this part is still being checked."),
    "cart_empty": _("Your cart is empty."),
    "cart_invalid": _("Checkout is blocked: an item in your cart has an expired price or is unavailable."),
    "no_pending_action": _("No action is required anymore."),
    "not_cancellable": _("The order can no longer be cancelled: a part has already been purchased."),
    "nothing_to_pay": _("There is nothing to pay."),
    "payment_in_progress": _("A payment is already in progress."),
    "account_has_open_orders": _("You can delete your account once your active orders are completed."),
    "throttled": _("Too many attempts. Please try again later."),
    "supplier_unavailable": _("The service is temporarily unavailable. Please try again in a few minutes."),
    "rate_unavailable": _("The service is temporarily unavailable. Please try again in a few minutes."),
    "sms_failed": _("The SMS could not be sent. Please try again."),
    "payment_init_failed": _("The payment page could not be opened. You were not charged — please try again."),
    # Not in G§9 yet: JSON body of handler500 (LP-BE-102 requires JSON 500s). Proposed guide addition.
    "server_error": _("Something went wrong. Please try again later."),
}


class ApiError(APIException):
    """raise ApiError("otp_invalid", attempts_left=2)  ->  400 {"code", "detail", "attempts_left": 2}"""

    status_code = 400

    def __init__(self, code, detail=None, status=None, **extra):
        self.status_code = status or self.status_code
        self.code = code
        # Kept apart from `detail`: DRF would coerce nested values to strings ("2" instead of 2).
        self.extra = extra
        super().__init__(detail or MESSAGES[code], code)
