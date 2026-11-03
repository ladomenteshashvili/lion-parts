"""Customer permissions (G§5.4). Staff users have no `customer`, so they never pass these."""

from rest_framework.permissions import BasePermission

from .errors import MESSAGES


class IsCustomer(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and hasattr(request.user, "customer"))


class IsPhoneVerified(IsCustomer):
    message = MESSAGES["phone_verification_required"]
    code = "phone_verification_required"

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.customer.is_phone_verified


class IsFullyVerified(IsCustomer):
    message = MESSAGES["verification_required"]
    code = "verification_required"

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        customer = request.user.customer
        return bool(customer.is_phone_verified and customer.is_email_verified)
