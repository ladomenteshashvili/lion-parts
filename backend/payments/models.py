"""Payments (G§6.6). Amount and currency are copied from the order when the payment is created and never
change afterwards (D16); only payments.services.mark_payment changes `status`."""

from django.conf import settings
from django.db import models


class Payment(models.Model):
    KIND_INITIAL = "initial"
    KIND_BALANCE = "balance"
    STATUS_CREATED = "created"
    STATUS_PENDING = "pending"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"
    STATUS_EXPIRED = "expired"
    STATUS_REFUNDED = "refunded"
    STATUS_UNKNOWN = "unknown"
    STATUSES = [(s, s) for s in (STATUS_CREATED, STATUS_PENDING, STATUS_PAID, STATUS_FAILED, STATUS_EXPIRED,
                                 STATUS_REFUNDED, STATUS_UNKNOWN)]
    OPEN_STATUSES = (STATUS_CREATED, STATUS_PENDING)

    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="payments")
    kind = models.CharField(max_length=10, choices=[(KIND_INITIAL, "Initial"), (KIND_BALANCE, "Balance")])
    provider = models.CharField(max_length=20)  # flitt / invoice / manual / fake
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=10, choices=STATUSES, default=STATUS_CREATED, db_index=True)
    provider_order_id = models.CharField(max_length=60, unique=True, null=True, blank=True)  # {order_number}-{pk}
    provider_payment_id = models.CharField(max_length=120, blank=True)
    checkout_url = models.URLField(max_length=1000, blank=True)
    checkout_expires_at = models.DateTimeField(null=True, blank=True)  # estimate; empty means not known, not "never"
    provider_closed_at = models.DateTimeField(null=True, blank=True)  # when provider evidence was recorded; empty: none
    closure_checks = models.PositiveIntegerField(default=0)  # provider queries spent looking for that evidence
    closure_next_check_at = models.DateTimeField(null=True, blank=True)  # empty: due now
    review_reasons = models.JSONField(default=list, blank=True)  # why a person should look at this paid result
    review_required_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+")
    provider_payload = models.JSONField(default=dict, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.provider_order_id or self.pk} · {self.amount} {self.currency} · {self.status}"


class Refund(models.Model):
    """A manual (partial) refund of a paid payment (owner decision D20): what an operator paid back outside
    the platform. amount_paid = paid payments minus their refunds. Never edited or deleted."""

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(max_digits=12, decimal_places=2)  # payment currency
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.payment} · -{self.amount}"
