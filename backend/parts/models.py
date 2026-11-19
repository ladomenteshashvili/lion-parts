import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.utils import timezone
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
    # LPK-238: the registry identity when known (a search offer). Manual requests name no manufacturer, so none.
    part = models.ForeignKey("parts.PartMaster", on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="quote_requests")
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


# Parts Master Registry (LPK-238 to LPK-240). Knowledge about a part, shared by every customer, so nothing here names a
# customer except the operator queue (WeightTaskRequest), which only the admin shows.

def normalize_brand(value):
    """Manufacturer matching key: letters and digits only, upper-case ("Mercedes-Benz" = "MERCEDES BENZ"). Never an
    alias table: two manufacturers stay two parts even when their part numbers match."""
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


class PartMaster(models.Model):
    """One part: a normalized part number from one manufacturer."""

    part_number = models.CharField(_("part number"), max_length=120)  # as first seen
    brand = models.CharField(_("manufacturer"), max_length=120, blank=True)  # as first seen
    part_number_key = models.CharField(max_length=120, editable=False)
    brand_key = models.CharField(max_length=120, blank=True, editable=False)
    name = models.CharField(_("name"), max_length=255, blank=True)
    # The strongest approved weight (registry.refresh_current_weight); the only weight reused for pricing.
    current_weight = models.ForeignKey("parts.PartWeight", on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="+", verbose_name=_("current weight"))
    search_count = models.PositiveIntegerField(_("searches"), default=0)
    vin_search_count = models.PositiveIntegerField(_("searches with a VIN"), default=0)
    last_supplier_price_usd = models.DecimalField(_("last supplier price (USD)"), max_digits=12, decimal_places=2,
                                                  null=True, blank=True)
    first_seen_at = models.DateTimeField(_("first seen"), default=timezone.now)
    last_searched_at = models.DateTimeField(_("last searched"), null=True, blank=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("part")
        verbose_name_plural = _("parts registry")
        ordering = ["part_number_key", "brand_key"]
        constraints = [models.UniqueConstraint(fields=["part_number_key", "brand_key"],
                                               name="parts_partmaster_identity")]

    def __str__(self):
        return f"{self.part_number} · {self.brand or '—'}"

    def save(self, *args, **kwargs):
        self.part_number_key = normalize_part_number(self.part_number)
        self.brand_key = normalize_brand(self.brand)
        super().save(*args, **kwargs)


class PartSupplierOffer(models.Model):
    """A supplier row seen in a search: links the part to the search log and keeps the supplier price history."""

    part = models.ForeignKey(PartMaster, on_delete=models.CASCADE, related_name="supplier_offers")
    search_log = models.ForeignKey(PartSearchLog, on_delete=models.CASCADE, related_name="part_offers")
    offer_id = models.CharField(max_length=120)
    provider = models.CharField(max_length=30)
    supplier_price_usd = models.DecimalField(_("supplier price (USD)"), max_digits=12, decimal_places=2)
    supplier_weight_kg = models.DecimalField(_("supplier weight (kg)"), max_digits=9, decimal_places=3, null=True,
                                             blank=True)
    observed_at = models.DateTimeField(_("seen"), default=timezone.now)

    class Meta:
        verbose_name = _("supplier offer")
        verbose_name_plural = _("supplier offers")
        ordering = ["-observed_at"]
        constraints = [models.UniqueConstraint(fields=["search_log", "offer_id"],
                                               name="parts_supplieroffer_once_per_log")]
        indexes = [models.Index(fields=["part", "-observed_at"], name="parts_supplieroffer_part_seen")]

    def __str__(self):
        return f"{self.offer_id} · ${self.supplier_price_usd}"


class PartWeight(models.Model):
    """One weight observation for a part, in kilograms. Only approved observations are reused; the strongest source
    wins (RANK), the newest within a source."""

    SOURCE_AMT = "amt"
    SOURCE_AI = "ai"
    SOURCE_OPERATOR = "operator"
    SOURCE_PHYSICAL = "physically_measured"
    SOURCES = [(SOURCE_AMT, _("AMT supplier")), (SOURCE_AI, _("AI estimate")), (SOURCE_OPERATOR, _("operator")),
               (SOURCE_PHYSICAL, _("physically measured"))]
    RANK = {SOURCE_AI: 1, SOURCE_AMT: 2, SOURCE_OPERATOR: 3, SOURCE_PHYSICAL: 4}
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUSES = [(STATUS_PENDING, _("awaiting approval")), (STATUS_APPROVED, _("approved")),
                (STATUS_REJECTED, _("rejected"))]

    part = models.ForeignKey(PartMaster, on_delete=models.CASCADE, related_name="weights", verbose_name=_("part"))
    weight_kg = models.DecimalField(_("weight (kg)"), max_digits=8, decimal_places=2,  # as every other weight field
                                    validators=[MinValueValidator(Decimal("0.01"))])
    source = models.CharField(_("source"), max_length=20, choices=SOURCES)
    status = models.CharField(_("status"), max_length=20, choices=STATUSES, default=STATUS_PENDING, db_index=True)
    observed_at = models.DateTimeField(_("observed"), default=timezone.now)
    last_seen_at = models.DateTimeField(_("last seen"), default=timezone.now)
    seen_count = models.PositiveIntegerField(_("times seen"), default=1)  # the same AMT weight again
    search_log = models.ForeignKey(PartSearchLog, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    evidence = models.TextField(_("evidence"), blank=True)
    conflict = models.BooleanField(_("conflicts with the current weight"), default=False)
    implausible = models.BooleanField(_("outside the plausible range"), default=False)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+", verbose_name=_("reviewed by"))
    reviewed_at = models.DateTimeField(_("reviewed"), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("part weight")
        verbose_name_plural = _("part weights")
        ordering = ["-observed_at", "-id"]
        indexes = [models.Index(fields=["part", "status"], name="parts_partweight_part_status")]
        constraints = [models.CheckConstraint(condition=models.Q(weight_kg__gt=0), name="parts_partweight_positive")]

    def __str__(self):
        return f"{self.weight_kg} kg · {self.get_source_display()} · {self.get_status_display()}"

    @property
    def rank(self):
        return self.RANK[self.source]


class PartWeightEvent(models.Model):
    """Audit history of a weight observation: who recorded, approved or rejected it, and when."""

    ACTION_RECORDED = "recorded"
    ACTION_APPROVED = "approved"
    ACTION_REJECTED = "rejected"
    ACTIONS = [(ACTION_RECORDED, _("recorded")), (ACTION_APPROVED, _("approved")), (ACTION_REJECTED, _("rejected"))]

    weight = models.ForeignKey(PartWeight, on_delete=models.CASCADE, related_name="events")
    action = models.CharField(_("action"), max_length=20, choices=ACTIONS)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="+", verbose_name=_("by"))
    note = models.TextField(_("note"), blank=True)
    created_at = models.DateTimeField(_("when"), auto_now_add=True)

    class Meta:
        verbose_name = _("weight history entry")
        verbose_name_plural = _("weight history")
        ordering = ["created_at", "id"]


class WeightTask(models.Model):
    """LPK-240: a part the supplier prices but no usable weight is known for. One open task per part, however many
    customers search it; the operator approves a weight once and every waiting customer gets a price."""

    STATUS_AWAITING_WEIGHT = "awaiting_weight"
    STATUS_AWAITING_PRICING = "awaiting_pricing"
    STATUS_RESOLVED = "resolved"
    STATUS_CANCELLED = "cancelled"
    STATUSES = [(STATUS_AWAITING_WEIGHT, _("awaiting weight")),
                (STATUS_AWAITING_PRICING, _("weight approved, pricing failed")),
                (STATUS_RESOLVED, _("resolved")), (STATUS_CANCELLED, _("cancelled"))]
    OPEN_STATUSES = (STATUS_AWAITING_WEIGHT, STATUS_AWAITING_PRICING)
    PROPOSED_SOURCES = [(PartWeight.SOURCE_OPERATOR, _("operator")),
                        (PartWeight.SOURCE_PHYSICAL, _("physically measured"))]

    part = models.ForeignKey(PartMaster, on_delete=models.CASCADE, related_name="weight_tasks", verbose_name=_("part"))
    status = models.CharField(_("status"), max_length=20, choices=STATUSES, default=STATUS_AWAITING_WEIGHT,
                              db_index=True)
    supplier_price_usd = models.DecimalField(_("last supplier price (USD)"), max_digits=12, decimal_places=2,
                                             null=True, blank=True)
    last_search_log = models.ForeignKey(PartSearchLog, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="+", verbose_name=_("last search"))
    offer_id = models.CharField(_("last offer"), max_length=120, blank=True)
    request_count = models.PositiveIntegerField(_("searches waiting"), default=0)  # guests included
    proposed_weight_kg = models.DecimalField(_("weight to approve (kg)"), max_digits=8, decimal_places=2, null=True,
                                             blank=True, validators=[MinValueValidator(Decimal("0.01"))])
    proposed_source = models.CharField(_("weight source"), max_length=20, choices=PROPOSED_SOURCES,
                                       default=PartWeight.SOURCE_OPERATOR)
    evidence = models.TextField(_("evidence"), blank=True)
    approved_weight = models.ForeignKey(PartWeight, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="tasks", verbose_name=_("approved weight"))
    last_error = models.TextField(_("last error"), blank=True)
    created_at = models.DateTimeField(_("created"), auto_now_add=True)
    resolved_at = models.DateTimeField(_("resolved"), null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+", verbose_name=_("resolved by"))

    class Meta:
        verbose_name = _("missing weight task")
        verbose_name_plural = _("missing weight queue")
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["part"], condition=models.Q(status__in=("awaiting_weight",
                                                                                              "awaiting_pricing")),
                                               name="parts_weighttask_one_open_per_part")]

    def __str__(self):
        return f"{self.part} · {self.get_status_display()}"


class WeightTaskRequest(models.Model):
    """A registered customer waiting on a task: priced with their own tariff when the weight is approved."""

    OUTCOME_WAITING = ""
    OUTCOME_PRICED = "priced"
    OUTCOME_ALREADY_PRICED = "already_priced"
    OUTCOME_NOT_ELIGIBLE = "not_eligible"
    OUTCOMES = [(OUTCOME_WAITING, _("waiting")), (OUTCOME_PRICED, _("priced and notified")),
                (OUTCOME_ALREADY_PRICED, _("already had a price")), (OUTCOME_NOT_ELIGIBLE, _("not eligible"))]

    task = models.ForeignKey(WeightTask, on_delete=models.CASCADE, related_name="requests")
    customer = models.ForeignKey("accounts.Customer", on_delete=models.CASCADE, related_name="+",
                                 verbose_name=_("customer"))
    search_log = models.ForeignKey(PartSearchLog, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
                                   verbose_name=_("search"))
    offer_id = models.CharField(max_length=120, blank=True)
    quote_request = models.ForeignKey(PartQuoteRequest, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="+", verbose_name=_("quote request"))
    outcome = models.CharField(_("outcome"), max_length=20, choices=OUTCOMES, blank=True, default=OUTCOME_WAITING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("waiting customer")
        verbose_name_plural = _("waiting customers")
        ordering = ["created_at", "id"]
        constraints = [models.UniqueConstraint(fields=["task", "customer"], name="parts_weighttaskrequest_once")]


class WeightResearchJob(models.Model):
    """LPK-247: an AI search for a part's weight, run in the background (`run_weight_research`). Its result is a
    pending AI weight that only an operator can approve; it never prices anything by itself."""

    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_DONE = "done"
    STATUS_NO_RESULT = "no_result"
    STATUS_SKIPPED = "skipped"
    STATUS_FAILED = "failed"
    STATUSES = [(STATUS_QUEUED, _("queued")), (STATUS_RUNNING, _("running")),
                (STATUS_DONE, _("weight proposed")), (STATUS_NO_RESULT, _("needs review: no reliable evidence")),
                (STATUS_SKIPPED, _("skipped")), (STATUS_FAILED, _("failed"))]
    ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)

    part = models.ForeignKey(PartMaster, on_delete=models.CASCADE, related_name="research_jobs",
                             verbose_name=_("part"))
    status = models.CharField(_("status"), max_length=20, choices=STATUSES, default=STATUS_QUEUED, db_index=True)
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    next_attempt_at = models.DateTimeField(_("next attempt"), default=timezone.now, db_index=True)
    started_at = models.DateTimeField(_("started"), null=True, blank=True)
    finished_at = models.DateTimeField(_("finished"), null=True, blank=True)
    cost_usd = models.DecimalField(_("cost (USD)"), max_digits=8, decimal_places=4, default=Decimal("0"))
    provider = models.CharField(_("provider"), max_length=30, blank=True)
    # A rule-based summary of the evidence (parts.weight_research.Rules), not a calibrated probability.
    confidence = models.DecimalField(_("evidence score"), max_digits=3, decimal_places=2, null=True, blank=True)
    measurement = models.CharField(_("measurement"), max_length=20, blank=True)
    conflicting = models.BooleanField(_("conflicting evidence"), default=False)
    explanation = models.TextField(_("explanation"), blank=True)
    proposed_weight = models.ForeignKey(PartWeight, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="research_jobs", verbose_name=_("proposed weight"))
    last_error = models.CharField(_("last error"), max_length=200, blank=True)  # an error type, never a body
    created_at = models.DateTimeField(_("created"), auto_now_add=True)

    class Meta:
        verbose_name = _("AI weight research")
        verbose_name_plural = _("AI weight research")
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["part"], condition=models.Q(status__in=("queued", "running")),
                                               name="parts_weightresearchjob_one_active_per_part")]

    def __str__(self):
        return f"#{self.pk} · {self.part} · {self.get_status_display()}"


class WeightResearchSource(models.Model):
    """One observation the provider claimed, what this server found on the page itself, and whether it counted and
    why (parts.evidence)."""

    VERIFICATIONS = [("verified", _("verified on the page")),
                     ("contradicted", _("the page shows a different weight")),
                     ("ambiguous", _("ambiguous page")), ("not_confirmed", _("not shown on the page")),
                     ("unreadable", _("no readable text")), ("inaccessible", _("page not reachable")),
                     ("blocked", _("blocked by the site")), ("disallowed", _("not allowed by robots.txt")),
                     ("refused", _("unsafe address, not fetched")),
                     ("not_checked", _("not checked"))]
    CREDIBILITY_MANUFACTURER, CREDIBILITY_TRUSTED = "manufacturer", "trusted"
    CREDIBILITY_STANDARD, CREDIBILITY_MARKETPLACE = "standard", "marketplace"
    CREDIBILITIES = [(CREDIBILITY_MANUFACTURER, _("manufacturer's site")), (CREDIBILITY_TRUSTED, _("trusted catalog")),
                     (CREDIBILITY_STANDARD, _("other site")), (CREDIBILITY_MARKETPLACE, _("marketplace (not counted)"))]

    job = models.ForeignKey(WeightResearchJob, on_delete=models.CASCADE, related_name="sources")
    url = models.URLField(_("URL"), max_length=1000)
    domain = models.CharField(_("site"), max_length=255)
    title = models.CharField(_("title"), max_length=300, blank=True)
    stated_part_number = models.CharField(_("part number on the page"), max_length=120, blank=True)
    stated_manufacturer = models.CharField(_("manufacturer on the page"), max_length=120, blank=True)
    stated_value = models.CharField(_("stated weight"), max_length=40, blank=True)
    stated_unit = models.CharField(_("unit"), max_length=20, blank=True)
    stated_measurement = models.CharField(_("stated measurement"), max_length=20, blank=True)
    excerpt = models.TextField(_("excerpt quoted by the provider"), blank=True)
    verification = models.CharField(_("page check"), max_length=20, choices=VERIFICATIONS, default="not_checked")
    measurement = models.CharField(_("measurement on the page"), max_length=20, blank=True)
    weight_kg = models.DecimalField(_("weight on the page (kg)"), max_digits=9, decimal_places=3, null=True,
                                    blank=True)
    page_excerpt = models.TextField(_("text found on the page"), blank=True)
    credibility = models.CharField(_("source type"), max_length=20, choices=CREDIBILITIES, blank=True)
    accepted = models.BooleanField(_("counted"), default=False)
    agrees = models.BooleanField(_("agrees with the proposal"), default=False)
    reason = models.CharField(_("assessment"), max_length=200, blank=True)

    class Meta:
        verbose_name = _("research source")
        verbose_name_plural = _("research sources")
        ordering = ["id"]

    def __str__(self):
        return self.domain


class WeightResearchSpend(models.Model):
    """LPK-247: research spending per local day: every attempt reserves its limit before the paid call, so overlapping
    runs never exceed the daily budget; a reported cost then replaces the reservation."""

    day = models.DateField(_("day"), unique=True)
    reserved_usd = models.DecimalField(_("spent or reserved (USD)"), max_digits=10, decimal_places=4,
                                       default=Decimal("0"))
    updated_at = models.DateTimeField(_("updated"), auto_now=True)

    class Meta:
        verbose_name = _("AI research spending")
        verbose_name_plural = _("AI research spending")
        ordering = ["-day"]

    def __str__(self):
        return f"{self.day}: ${self.reserved_usd}"
