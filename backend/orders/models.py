"""Orders, support tickets and notifications (G§6.5). Money is frozen at checkout (G§7.17 / D16): the order keeps its currency and the
exchange rate used; every item keeps its canonical USD snapshot and its amounts in the order
currency. Exchange-rate changes never touch an order."""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

MONEY = {"max_digits": 12, "decimal_places": 2}


class Order(models.Model):
    STATUS_PAYMENT_PENDING = "payment_pending"
    STATUS_PROCESSING = "processing"
    STATUS_ACTION_REQUIRED = "action_required"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUSES = [(s, s) for s in (STATUS_PAYMENT_PENDING, STATUS_PROCESSING, STATUS_ACTION_REQUIRED,
                                 STATUS_COMPLETED, STATUS_CANCELLED)]
    OPEN_STATUSES = (STATUS_PAYMENT_PENDING, STATUS_PROCESSING, STATUS_ACTION_REQUIRED)
    FROZEN_FIELDS = ("currency", "exchange_rate")

    order_number = models.CharField(max_length=20, unique=True, null=True, blank=True)  # LP-{year}-{pk:05d}
    customer = models.ForeignKey("accounts.Customer", on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=20, choices=STATUSES, default=STATUS_PAYMENT_PENDING, db_index=True)
    currency = models.CharField(max_length=3)
    exchange_rate = models.DecimalField(max_digits=10, decimal_places=4)  # GEL per USD used; 1 for USD
    billing_snapshot = models.JSONField()  # G§7.12
    delivery_method = models.CharField(max_length=10, choices=[("pickup", "Pickup"), ("delivery", "Delivery")])
    pickup_point = models.ForeignKey("pricing.PickupPoint", on_delete=models.PROTECT, null=True, blank=True)
    delivery_city = models.CharField(max_length=60, blank=True)
    delivery_district = models.CharField(max_length=60, blank=True)
    delivery_address = models.CharField(max_length=255, blank=True)
    delivery_comment = models.TextField(blank=True)
    courier_fee = models.DecimalField(default=0, **MONEY)  # confirmed, order currency
    proposed_courier_fee_gel = models.DecimalField(null=True, blank=True, **MONEY)  # mirror of the pending proposal
    courier_fee_action_required = models.BooleanField(default=False)
    courier_fee_message = models.TextField(blank=True)
    courier_fee_confirmed_at = models.DateTimeField(null=True, blank=True)
    courier_fee_declined_at = models.DateTimeField(null=True, blank=True)
    payment_method = models.CharField(max_length=10, choices=[("card", "Card"), ("invoice", "Invoice")])
    payment_option = models.CharField(max_length=20, choices=[("full", "Full"), ("split_70_30", "70/30")])
    split_upfront_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)  # frozen (split)
    items_total = models.DecimalField(default=0, **MONEY)
    discount_total = models.DecimalField(default=0, **MONEY)
    total = models.DecimalField(default=0, **MONEY)
    amount_due_now = models.DecimalField(default=0, **MONEY)
    amount_paid = models.DecimalField(default=0, **MONEY)
    balance_due = models.DecimalField(default=0, **MONEY)
    terms_accepted_at = models.DateTimeField()
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.order_number or f"Order {self.pk}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            stored = Order.objects.filter(pk=self.pk).values(*self.FROZEN_FIELDS).first()
            if stored and any(stored[f] != getattr(self, f) for f in self.FROZEN_FIELDS):
                raise ValidationError("An order's currency and exchange rate are frozen at checkout.")
        super().save(*args, **kwargs)


class CourierFeeProposal(models.Model):
    """One version of a courier fee proposal (G§7.14, owner decision D21). Frozen when created: the GEL fee,
    for USD orders the NBG rate of that moment, and the amount in the order currency the customer will
    accept. Never edited afterwards except its status; a changed fee is a new proposal."""

    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_DECLINED = "declined"
    STATUS_SUPERSEDED = "superseded"
    STATUS_CANCELLED = "cancelled"
    STATUSES = [(s, s) for s in (STATUS_PENDING, STATUS_ACCEPTED, STATUS_DECLINED, STATUS_SUPERSEDED,
                                 STATUS_CANCELLED)]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="courier_fee_proposals")
    fee_gel = models.DecimalField(**MONEY)
    exchange_rate = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)  # USD orders
    amount = models.DecimalField(**MONEY)  # in currency, frozen
    currency = models.CharField(max_length=3)
    message = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=STATUS_PENDING, db_index=True)
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    proposed_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["proposed_at", "id"]

    def __str__(self):
        return f"{self.order} · {self.amount} {self.currency} · {self.status}"


class OrderItem(models.Model):
    STATUS_CREATED = "created"
    STATUS_PAYMENT_CONFIRMED = "payment_confirmed"
    STATUS_CHECKING = "checking"
    STATUS_ACTION_REQUIRED = "action_required"
    STATUS_PURCHASED = "purchased"
    STATUS_RECEIVED_USA = "received_usa"
    STATUS_SHIPPED_TO_GEORGIA = "shipped_to_georgia"
    STATUS_RECEIVED_GEORGIA = "received_georgia"
    STATUS_READY_FOR_PICKUP = "ready_for_pickup"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUSES = [(s, s) for s in (STATUS_CREATED, STATUS_PAYMENT_CONFIRMED, STATUS_CHECKING, STATUS_ACTION_REQUIRED,
                                 STATUS_PURCHASED, STATUS_RECEIVED_USA, STATUS_SHIPPED_TO_GEORGIA,
                                 STATUS_RECEIVED_GEORGIA, STATUS_READY_FOR_PICKUP, STATUS_COMPLETED, STATUS_CANCELLED)]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    source = models.CharField(max_length=20, blank=True)  # search / prepared_quote (from the cart line)
    offer_id = models.CharField(max_length=120)
    quote_id = models.CharField(max_length=120, blank=True)
    part_number = models.CharField(max_length=120)
    name = models.CharField(max_length=255)
    brand = models.CharField(max_length=120, blank=True)
    condition = models.CharField(max_length=80, blank=True)
    quantity = models.PositiveSmallIntegerField()
    vin = models.CharField(max_length=17, blank=True)
    weight_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True)
    weight_source = models.CharField(max_length=20, blank=True)
    eta_min_days = models.PositiveSmallIntegerField(null=True, blank=True)
    eta_max_days = models.PositiveSmallIntegerField(null=True, blank=True)
    expected_arrival_from = models.DateField(null=True, blank=True)
    expected_arrival_to = models.DateField(null=True, blank=True)
    # canonical USD snapshot (G§7.2)
    supplier_price_usd = models.DecimalField(**MONEY)
    shipping_usd_per_kg = models.DecimalField(max_digits=8, decimal_places=2)
    markup_percent = models.DecimalField(max_digits=6, decimal_places=2)
    vat_percent = models.DecimalField(max_digits=5, decimal_places=2)
    list_price_usd = models.DecimalField(**MONEY)
    discount_usd = models.DecimalField(**MONEY)
    unit_price_usd = models.DecimalField(**MONEY)
    discount = models.ForeignKey("pricing.Discount", on_delete=models.SET_NULL, null=True, blank=True)
    # frozen amounts in order.currency (converted with order.exchange_rate at checkout)
    list_unit_price = models.DecimalField(**MONEY)
    discount_unit_amount = models.DecimalField(**MONEY)
    unit_price = models.DecimalField(**MONEY)
    item_status = models.CharField(max_length=30, choices=STATUSES, default=STATUS_CREATED, db_index=True)
    action_required = models.BooleanField(default=False, db_index=True)
    action_type = models.CharField(max_length=40, blank=True)  # G§7.9
    action_message = models.TextField(blank=True)
    proposed_part_number = models.CharField(max_length=120, blank=True)
    proposed_name = models.CharField(max_length=255, blank=True)
    proposed_unit_price = models.DecimalField(null=True, blank=True, **MONEY)
    proposed_eta_min_days = models.PositiveSmallIntegerField(null=True, blank=True)
    proposed_eta_max_days = models.PositiveSmallIntegerField(null=True, blank=True)
    proposed_weight_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    purchased_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.part_number} x {self.quantity}"

    @property
    def line_total(self):
        return self.unit_price * self.quantity


class OrderItemEvent(models.Model):
    """System events store a text code + params (rendered per language); operator text goes to message."""

    ACTOR_SYSTEM = "system"
    ACTOR_CUSTOMER = "customer"
    ACTOR_OPERATOR = "operator"

    item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=60, db_index=True)
    text_code = models.CharField(max_length=60, blank=True)  # orders/texts.py::EVENT_TEXTS
    text_params = models.JSONField(default=dict, blank=True)
    message = models.TextField(blank=True)  # operator-written only (not translated)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    actor_type = models.CharField(max_length=30, default=ACTOR_SYSTEM)
    actor_name = models.CharField(max_length=120, blank=True)
    visible_to_customer = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.item.part_number} · {self.event_type}"


class OrderSupportMessage(models.Model):
    SENDER_CUSTOMER = "customer"
    SENDER_OPERATOR = "operator"
    SENDER_SYSTEM = "system"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="support_messages")
    item = models.ForeignKey(OrderItem, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="support_messages")
    sender_type = models.CharField(max_length=30, choices=[(SENDER_CUSTOMER, "Customer"), (SENDER_OPERATOR, "Operator"),
                                                           (SENDER_SYSTEM, "System")], default=SENDER_OPERATOR,
                                   db_index=True)
    sender_name = models.CharField(max_length=120, blank=True)
    message = models.TextField(blank=True)  # human messages as written
    text_code = models.CharField(max_length=60, blank=True)  # system messages, rendered per language
    text_params = models.JSONField(default=dict, blank=True)
    visible_to_customer = models.BooleanField(default=True)
    is_read_by_customer = models.BooleanField(default=False)
    is_read_by_operator = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.order} · {self.sender_type}"


EVENT_TYPES = ("price_ready", "action_required", "courier_fee_proposed", "support_reply", "status_update",
               "payment_update")  # G§7.11


class SupportTicket(models.Model):
    """G§7.16: from a customer or a guest (contact + session)."""

    STATUS_NEW = "new"
    STATUS_ANSWERED = "answered"
    STATUS_CLOSED = "closed"
    CONTEXTS = [("not_found", "Not found"), ("part", "Part"), ("general", "General")]

    customer = models.ForeignKey("accounts.Customer", on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="support_tickets")
    session_key = models.CharField(max_length=64, blank=True, db_index=True)
    contact = models.CharField(max_length=255, blank=True)  # guest email or phone
    language = models.CharField(max_length=8, default=settings.LANGUAGE_CODE)  # used to answer guests
    context = models.CharField(max_length=20, choices=CONTEXTS)
    part_number = models.CharField(max_length=120, blank=True)
    vin = models.CharField(max_length=17, blank=True)
    message = models.TextField()
    status = models.CharField(max_length=10, default=STATUS_NEW, db_index=True, choices=[
        (STATUS_NEW, "New"), (STATUS_ANSWERED, "Answered"), (STATUS_CLOSED, "Closed")])
    operator_reply = models.TextField(blank=True)
    replied_at = models.DateTimeField(null=True, blank=True)
    replied_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"#{self.pk} · {self.context} · {self.status}"


class Notification(models.Model):
    """G§7.11. Titles are rendered per language from event_type (never stored); `message` is operator text.
    `token` (uuid4, random) opens the public /n/ link for 30 days (owner decision D23)."""

    customer = models.ForeignKey("accounts.Customer", on_delete=models.CASCADE, null=True, blank=True,
                                 related_name="notifications")
    event_type = models.CharField(max_length=40, db_index=True, choices=[(e, e) for e in EVENT_TYPES])
    order = models.ForeignKey(Order, on_delete=models.CASCADE, null=True, blank=True)
    item = models.ForeignKey(OrderItem, on_delete=models.SET_NULL, null=True, blank=True)
    quote_request = models.ForeignKey("parts.PartQuoteRequest", on_delete=models.CASCADE, null=True, blank=True)
    support_message = models.ForeignKey(OrderSupportMessage, on_delete=models.SET_NULL, null=True, blank=True)
    support_ticket = models.ForeignKey(SupportTicket, on_delete=models.SET_NULL, null=True, blank=True)
    text_params = models.JSONField(default=dict, blank=True)
    message = models.TextField(blank=True)
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["customer", "read_at"])]

    def __str__(self):
        return f"{self.event_type} · {self.customer_id or 'guest'}"


class NotificationDelivery(models.Model):
    """One SMS/email attempt (never edited)."""

    STATUS_SENT = "sent"  # a real provider/backend accepted it
    STATUS_FAILED = "failed"  # an error or no confirmation
    STATUS_SKIPPED = "skipped"  # the channel is switched off here (no SMS provider, non-delivering email backend)

    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="deliveries")
    channel = models.CharField(max_length=10, choices=[("sms", "SMS"), ("email", "Email")])
    recipient = models.CharField(max_length=255)
    status = models.CharField(max_length=10, db_index=True, choices=[
        (STATUS_SENT, "Sent"), (STATUS_FAILED, "Failed"), (STATUS_SKIPPED, "Skipped (channel disabled)")])
    provider_message_id = models.CharField(max_length=120, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.channel} · {self.status}"


class NotificationChannelSetting(models.Model):
    CHANNELS = [("sms", "SMS"), ("email", "Email"), ("both", "SMS + email"), ("none", "None")]

    event_type = models.CharField(max_length=40, unique=True, choices=[(e, e) for e in EVENT_TYPES])
    channel = models.CharField(max_length=10, choices=CHANNELS, default="none")

    class Meta:
        ordering = ["event_type"]

    def __str__(self):
        return f"{self.event_type}: {self.channel}"


class NotificationTemplate(models.Model):
    """SMS/email text per event and language (LP-BE-1305); a missing language falls back to the default one.
    Django template syntax over a restricted context (D24): link, title, order.order_number,
    notification.quote_request.part_number."""

    event_type = models.ForeignKey(NotificationChannelSetting, on_delete=models.CASCADE, to_field="event_type",
                                   db_column="event_type", related_name="templates")
    language = models.CharField(max_length=8, choices=settings.LANGUAGES)
    sms_template = models.TextField(blank=True)
    email_subject = models.CharField(max_length=255, blank=True)
    email_template = models.TextField(blank=True)

    class Meta:
        ordering = ["event_type", "language"]
        constraints = [models.UniqueConstraint(fields=["event_type", "language"], name="one_template_per_language")]

    def __str__(self):
        return f"{self.event_type_id} · {self.language}"
