import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from accounts.validators import normalize_part_number


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



class PartSearchLog(models.Model):
    """One supplier search or weight calculation; normalized_response holds the priced offers
    (with snapshots) and is the source of truth for adding to the cart (G§6.3, G§7.6)."""

    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    PROVIDER_DEMO = "demo"
    PROVIDER_AMT = "amt"
    KIND_SEARCH = "search"
    KIND_WEIGHT_CALC = "weight_calc"

    customer = models.ForeignKey("accounts.Customer", on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="search_logs")
    session_key = models.CharField(max_length=64, blank=True, db_index=True)
    kind = models.CharField(max_length=20, choices=[(KIND_SEARCH, "Search"), (KIND_WEIGHT_CALC, "Weight calculation")],
                            default=KIND_SEARCH)
    provider = models.CharField(max_length=30, choices=[(PROVIDER_DEMO, "Demo"), (PROVIDER_AMT, "AutoMotoTrade")],
                                db_index=True)
    part_number = models.CharField(max_length=120, db_index=True)
    vin = models.CharField(max_length=40, blank=True)
    found_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=[(STATUS_SUCCESS, "Success"), (STATUS_FAILED, "Failed")],
                              default=STATUS_SUCCESS, db_index=True)
    raw_response = models.JSONField(default=dict, blank=True)
    normalized_response = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    currency = models.CharField(max_length=3, default="GEL")
    exchange_rate = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    price_valid_until = models.DateTimeField(null=True, blank=True)
    hidden_at = models.DateTimeField(null=True, blank=True)  # history cleared
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.provider} · {self.part_number} · {self.status}"

    def offers(self):
        return self.normalized_response.get("offers", [])

    def offer(self, offer_id):
        return next((o for o in self.offers() if o.get("offer_id") == offer_id), None)


class PartQuoteRequest(models.Model):
    """A customer's request for a price (G§6.3, G§7.6). When the operator marks it ready, the price
    snapshot (G§7.2) is stored here and the public /q/<token> link works."""

    REQUEST_TYPE_WEIGHT_PRICE = "weight_price"
    REQUEST_TYPE_MANUAL_SEARCH = "manual_search"
    STATUS_NEW = "new"
    STATUS_CONTACTED = "contacted"
    STATUS_RESOLVED = "resolved"
    STATUS_CANCELLED = "cancelled"
    OPEN_STATUSES = (STATUS_NEW, STATUS_CONTACTED)

    customer = models.ForeignKey("accounts.Customer", on_delete=models.CASCADE, related_name="quote_requests")
    search_log = models.ForeignKey(PartSearchLog, on_delete=models.SET_NULL, null=True, blank=True)
    request_type = models.CharField(max_length=20, choices=[
        (REQUEST_TYPE_WEIGHT_PRICE, "Weight price"), (REQUEST_TYPE_MANUAL_SEARCH, "Manual search")])
    part_number = models.CharField(max_length=120)
    vin = models.CharField(max_length=17, blank=True)
    comment = models.TextField(blank=True)
    offer_id = models.CharField(max_length=120, blank=True)
    quote_id = models.CharField(max_length=120, blank=True)
    name = models.CharField(max_length=255, blank=True)
    brand = models.CharField(max_length=120, blank=True)
    condition = models.CharField(max_length=80, blank=True)
    eta_min_days = models.PositiveSmallIntegerField(null=True, blank=True)
    eta_max_days = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, default=STATUS_NEW, db_index=True, choices=[
        (STATUS_NEW, "New"), (STATUS_CONTACTED, "Contacted"), (STATUS_RESOLVED, "Resolved"),
        (STATUS_CANCELLED, "Cancelled")])
    prepared_weight_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    supplier_price_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # Operator-approved canonical USD price (G§7.2). It stays the base while the quote is valid; a GEL
    # amount is always converted from it with the current rate when shown or added to a cart.
    shipping_usd_per_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    markup_percent = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    vat_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    list_price_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    discount_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    unit_price_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    discount = models.ForeignKey("pricing.Discount", on_delete=models.SET_NULL, null=True, blank=True)
    price_valid_until = models.DateTimeField(null=True, blank=True)  # business days from approval (G§7.5)
    currency = models.CharField(max_length=3, default="GEL")  # the customer's display currency
    operator_message = models.TextField(blank=True)
    notification_token = models.UUIDField(default=uuid.uuid4, unique=True)
    price_ready_at = models.DateTimeField(null=True, blank=True)
    seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.part_number} · {self.get_request_type_display()} · {self.status}"

    @property
    def is_price_ready(self):
        return self.price_ready_at is not None and self.unit_price_usd is not None


MAX_IMAGE_BYTES = 5 * 1024 * 1024


def validate_image_size(file):
    if file.size > MAX_IMAGE_BYTES:
        raise ValidationError(_("The image must not be larger than 5 MB."), code="file_too_large")


class PartImage(models.Model):
    part_number = models.CharField(max_length=120, db_index=True)  # normalized
    brand = models.CharField(max_length=120, blank=True)  # empty = any brand
    image = models.ImageField(upload_to="parts/", validators=[
        FileExtensionValidator(["jpg", "jpeg", "png", "webp"]), validate_image_size])
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.part_number} {self.brand}".strip()

    def save(self, *args, **kwargs):
        self.part_number = normalize_part_number(self.part_number)
        super().save(*args, **kwargs)
