from django.db import models

# Create your models here.
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

    payment_type = models.CharField(max_length=20, choices=PAYMENT_CHOICES, default=PAYMENT_FULL)
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default=STATUS_PAYMENT_PENDING)

    total_gel = models.DecimalField(max_digits=12, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.order_number


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")

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

    def __str__(self):
        return f"{self.part_number} x {self.quantity}"