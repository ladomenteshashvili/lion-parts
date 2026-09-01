from django.db import models

# Create your models here.
from django.db import models


class Cart(models.Model):
    session_id = models.CharField(max_length=120, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Cart {self.session_id}"


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")

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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["cart", "cart_item_id"]
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.part_number} x {self.quantity}"