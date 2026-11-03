"""Server-priced cart (G§6.4). Lines store the full price snapshot (G§7.2); prices never come from the client."""

from django.db import models


class Cart(models.Model):
    customer = models.OneToOneField("accounts.Customer", on_delete=models.CASCADE, null=True, blank=True,
                                    related_name="cart")
    session_key = models.CharField(max_length=64, null=True, blank=True, unique=True)  # guests
    currency = models.CharField(max_length=3, default="GEL")
    merge_summary = models.JSONField(null=True, blank=True)  # returned once after login, then cleared
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=models.Q(customer__isnull=False) | models.Q(session_key__isnull=False), name="cart_has_owner")]

    def __str__(self):
        return f"Cart {self.customer_id or self.session_key}"


class CartItem(models.Model):
    SOURCE_SEARCH = "search"
    SOURCE_PREPARED_QUOTE = "prepared_quote"
    STATUS_OK = "ok"
    STATUS_PRICE_CHANGED = "price_changed"
    STATUS_UNAVAILABLE = "unavailable"

    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    source = models.CharField(max_length=20, choices=[(SOURCE_SEARCH, "Search"), (SOURCE_PREPARED_QUOTE, "Prepared quote")])
    search_log = models.ForeignKey("parts.PartSearchLog", on_delete=models.SET_NULL, null=True, blank=True)
    quote_request = models.ForeignKey("parts.PartQuoteRequest", on_delete=models.SET_NULL, null=True, blank=True)
    offer_id = models.CharField(max_length=120)
    quote_id = models.CharField(max_length=120, blank=True)
    part_number = models.CharField(max_length=120)
    name = models.CharField(max_length=255)
    brand = models.CharField(max_length=120, blank=True)
    condition = models.CharField(max_length=80, blank=True)
    quantity = models.PositiveSmallIntegerField()  # 1..99
    vin = models.CharField(max_length=17, blank=True)
    weight_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True)
    weight_source = models.CharField(max_length=20, blank=True)  # api / customer / operator
    eta_min_days = models.PositiveSmallIntegerField(null=True, blank=True)
    eta_max_days = models.PositiveSmallIntegerField(null=True, blank=True)
    # canonical price snapshot (G§7.2, USD; never changed by the exchange rate)
    supplier_price_usd = models.DecimalField(max_digits=12, decimal_places=2)
    shipping_usd_per_kg = models.DecimalField(max_digits=8, decimal_places=2)
    markup_percent = models.DecimalField(max_digits=6, decimal_places=2)
    vat_percent = models.DecimalField(max_digits=5, decimal_places=2)
    list_price_usd = models.DecimalField(max_digits=12, decimal_places=2)
    discount_usd = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price_usd = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.ForeignKey("pricing.Discount", on_delete=models.SET_NULL, null=True, blank=True)
    # the same price in cart.currency, converted with exchange_rate (kept current, see cart.services)
    exchange_rate = models.DecimalField(max_digits=10, decimal_places=4)
    list_unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount_unit_amount = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    previous_unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    price_valid_until = models.DateTimeField()
    availability_status = models.CharField(max_length=20, default=STATUS_OK)  # ok / price_changed / unavailable
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("cart", "source", "offer_id")]
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.part_number} x {self.quantity}"

    def apply_snapshot(self, snapshot):
        """Store a canonical pricing.engine.PriceSnapshot on this line (not saved)."""
        for field in ("supplier_price_usd", "weight_kg", "shipping_usd_per_kg", "markup_percent", "vat_percent",
                      "list_price_usd", "discount_usd", "unit_price_usd"):
            setattr(self, field, getattr(snapshot, field))
        self.discount_id = snapshot.discount_id

    def apply_converted(self, converted):
        """Store the customer-currency amounts (pricing.engine.Converted, not saved)."""
        for field in ("exchange_rate", "list_unit_price", "discount_unit_amount", "unit_price"):
            setattr(self, field, getattr(converted, field))
