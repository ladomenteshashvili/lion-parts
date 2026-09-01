from django.db import models


class Order(models.Model):
    STATUS_PAYMENT_PENDING = "payment_pending"
    STATUS_PAID = "paid"
    STATUS_PROCESSING = "processing"
    STATUS_ACTION_REQUIRED = "action_required"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PAYMENT_PENDING, "Payment pending"),
        (STATUS_PAID, "Paid"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_ACTION_REQUIRED, "Action required"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    PAYMENT_FULL = "full"

    PAYMENT_CHOICES = [
        (PAYMENT_FULL, "100% payment"),
    ]

    order_number = models.CharField(max_length=40, unique=True, db_index=True)
    session_id = models.CharField(max_length=120, db_index=True)

    customer_name = models.CharField(max_length=120)
    customer_phone = models.CharField(max_length=40)
    vin = models.CharField(max_length=40, blank=True)
    note = models.TextField(blank=True)

    payment_type = models.CharField(
        max_length=20,
        choices=PAYMENT_CHOICES,
        default=PAYMENT_FULL,
    )

    status = models.CharField(
        max_length=40,
        choices=STATUS_CHOICES,
        default=STATUS_PAYMENT_PENDING,
        db_index=True,
    )

    total_gel = models.DecimalField(max_digits=12, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.order_number


class OrderItem(models.Model):
    ITEM_STATUS_CREATED = "created"
    ITEM_STATUS_PAYMENT_CONFIRMED = "payment_confirmed"
    ITEM_STATUS_CHECKING = "checking"
    ITEM_STATUS_ACTION_REQUIRED = "action_required"
    ITEM_STATUS_PURCHASED = "purchased"
    ITEM_STATUS_RECEIVED_USA = "received_usa"
    ITEM_STATUS_SHIPPED_TO_GEORGIA = "shipped_to_georgia"
    ITEM_STATUS_RECEIVED_GEORGIA = "received_georgia"
    ITEM_STATUS_READY_FOR_PICKUP = "ready_for_pickup"
    ITEM_STATUS_COMPLETED = "completed"
    ITEM_STATUS_CANCELLED = "cancelled"

    ITEM_STATUS_CHOICES = [
        (ITEM_STATUS_CREATED, "Created"),
        (ITEM_STATUS_PAYMENT_CONFIRMED, "Payment confirmed"),
        (ITEM_STATUS_CHECKING, "Checking"),
        (ITEM_STATUS_ACTION_REQUIRED, "Action required"),
        (ITEM_STATUS_PURCHASED, "Purchased"),
        (ITEM_STATUS_RECEIVED_USA, "Received in USA"),
        (ITEM_STATUS_SHIPPED_TO_GEORGIA, "Shipped to Georgia"),
        (ITEM_STATUS_RECEIVED_GEORGIA, "Received in Georgia"),
        (ITEM_STATUS_READY_FOR_PICKUP, "Ready for pickup"),
        (ITEM_STATUS_COMPLETED, "Completed"),
        (ITEM_STATUS_CANCELLED, "Cancelled"),
    ]

    ACTION_TYPE_NONE = ""
    ACTION_TYPE_PRICE_CHANGE = "price_change"
    ACTION_TYPE_ETA_CHANGE = "eta_change"
    ACTION_TYPE_WEIGHT_CHANGE = "weight_change"
    ACTION_TYPE_FITMENT_ISSUE = "fitment_issue"
    ACTION_TYPE_ALTERNATIVE_REQUIRED = "alternative_required"
    ACTION_TYPE_OTHER = "other"

    ACTION_TYPE_CHOICES = [
        (ACTION_TYPE_NONE, "No action"),
        (ACTION_TYPE_PRICE_CHANGE, "Price change"),
        (ACTION_TYPE_ETA_CHANGE, "ETA change"),
        (ACTION_TYPE_WEIGHT_CHANGE, "Weight/dimensions change"),
        (ACTION_TYPE_FITMENT_ISSUE, "Fitment issue"),
        (ACTION_TYPE_ALTERNATIVE_REQUIRED, "Alternative required"),
        (ACTION_TYPE_OTHER, "Other"),
    ]

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )

    cart_item_id = models.CharField(max_length=255)
    quote_id = models.CharField(max_length=120)
    part_option_id = models.CharField(max_length=120)
    part_number = models.CharField(max_length=120)

    name = models.CharField(max_length=255)
    condition = models.CharField(max_length=80, blank=True)
    brand = models.CharField(max_length=120, blank=True)
    availability = models.CharField(max_length=120, blank=True)
    eta_days = models.PositiveIntegerField(null=True, blank=True)

    final_price_gel = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="GEL")
    note = models.TextField(blank=True)

    quantity = models.PositiveIntegerField(default=1)

    item_status = models.CharField(
        max_length=40,
        choices=ITEM_STATUS_CHOICES,
        default=ITEM_STATUS_CREATED,
        db_index=True,
    )

    action_required = models.BooleanField(default=False, db_index=True)

    action_type = models.CharField(
        max_length=40,
        choices=ACTION_TYPE_CHOICES,
        default=ACTION_TYPE_NONE,
        blank=True,
    )

    action_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.part_number} x {self.quantity}"