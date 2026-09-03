from decimal import Decimal

from django.db import models


class CarrierService(models.Model):
    name = models.CharField(max_length=120, unique=True)

    usd_per_kg = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("8.00"),
    )

    min_eta_days = models.PositiveIntegerField(default=10)
    max_eta_days = models.PositiveIntegerField(default=14)

    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "name"]

    def __str__(self):
        return f"{self.name} · ${self.usd_per_kg}/kg"

    @classmethod
    def get_default(cls):
        return cls.objects.filter(is_active=True, is_default=True).first()


class PartQuoteRequest(models.Model):
    STATUS_NEW = "new"
    STATUS_CONTACTED = "contacted"
    STATUS_RESOLVED = "resolved"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_NEW, "New"),
        (STATUS_CONTACTED, "Contacted"),
        (STATUS_RESOLVED, "Resolved"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    session_id = models.CharField(max_length=255, db_index=True)
    part_number = models.CharField(max_length=100)
    vin = models.CharField(max_length=17, blank=True)
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=50)
    comment = models.TextField(blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_NEW,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.part_number} - {self.customer_phone}"