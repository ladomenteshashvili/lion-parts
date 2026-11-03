"""Orders (G§6.5). Money is frozen at checkout (G§7.17 / D16): the order keeps its currency and the
exchange rate used; every item keeps its canonical USD snapshot and its amounts in the order
currency. Exchange-rate changes never touch an order."""

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
