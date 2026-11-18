"""Missing-weight queue (LPK-240): a part the supplier prices without a usable weight becomes one operator task, however
many customers search it. Approving a weight prices every waiting customer with their own tariff and the current
supplier price, through the existing quote request "price ready" flow (G§7.6): history, the /q/ link, the cart and the
price_ready notification work as they do for a quote the operator priced by hand. Nobody has to search again."""

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import PartMaster, PartQuoteRequest, PartWeight, WeightTask, WeightTaskRequest
from .registry import InvalidWeight, clean_weight, identity, is_plausible, record_weight


def queue_missing_weights(log, rows, customer):
    """After a search: an open task per part the supplier priced but no usable weight is known for, and the logged-in
    customer on it (guests are counted only: there is nobody to send a price to)."""
    done = set()
    for row in rows:
        part = row.get("part")
        if part is None or row["weight_kg"] is not None or part.pk in done:
            continue
        done.add(part.pk)
        with transaction.atomic():
            task = _open_task(part)
            WeightTask.objects.filter(pk=task.pk).update(request_count=F("request_count") + 1, last_search_log=log,
                                                         offer_id=row["offer_id"],
                                                         supplier_price_usd=row["supplier_price_usd"])
            if customer is not None:
                _wait(task, customer, log, row["offer_id"])


def _open_task(part):
    """-> the part's open task, locked. The lock waits for a pricing run in progress (price_task holds it); a task that
    run resolved no longer matches, so a new one opens and no customer is left on a resolved task."""
    open_tasks = WeightTask.objects.select_for_update().filter(part=part, status__in=WeightTask.OPEN_STATUSES)
    task = open_tasks.first()
    if task:
        return task
    try:
        with transaction.atomic():
            return WeightTask.objects.create(part=part)
    except IntegrityError:  # a concurrent search opened it first (one open task per part)
        return open_tasks.get()


def _wait(task, customer, log, offer_id):
    """One entry per customer and task, pointing at the customer's latest search of the part (the one priced). The
    task is locked, so a repeat or parallel search by the same customer updates their entry; an open task only has
    waiting entries (price_task sets the outcomes as it resolves the task)."""
    WeightTaskRequest.objects.update_or_create(task=task, customer=customer,
                                               defaults={"search_log": log, "offer_id": offer_id})


def approve_task(task_id, actor, weight=None, source=PartWeight.SOURCE_OPERATOR, evidence=""):
    """The operator approves a weight for the task's part and every waiting customer is priced. With no weight given,
    the part's approved weight on record is used (for example one approved on a quote request meanwhile).
    Raises OperatorError and changes nothing for a resolved or cancelled task, so a repeated approval is harmless.
    If pricing fails (supplier unavailable), the weight stays approved and the task waits for "retry pricing"."""
    from orders.services import OperatorError

    if source not in dict(WeightTask.PROPOSED_SOURCES):
        raise OperatorError("proposed_source", _("Choose operator or physically measured."))
    part_id = WeightTask.objects.values_list("part_id", flat=True).get(pk=task_id)
    with transaction.atomic():
        # The part before the task, the order a search takes them in (record_search, then the queue): no deadlock.
        PartMaster.objects.select_for_update().filter(pk=part_id).first()
        task = WeightTask.objects.select_for_update(of=("self",)).select_related("part__current_weight").get(pk=task_id)
        _check_open(task)
        if task.status == WeightTask.STATUS_AWAITING_WEIGHT:
            if weight is None:
                if task.part.current_weight is None:
                    raise OperatorError("proposed_weight_kg", _("Enter the weight to approve."))
                task.approved_weight = task.part.current_weight
            else:
                try:
                    weight = clean_weight(weight)
                except InvalidWeight:
                    raise OperatorError("proposed_weight_kg", _("Enter a weight greater than zero.")) from None
                if not is_plausible(weight):
                    raise OperatorError("proposed_weight_kg", _("The weight must be between 0.01 and 500 kg."))
                task.approved_weight = record_weight(task.part, weight, source, status=PartWeight.STATUS_APPROVED,
                                                     actor=actor, evidence=evidence,
                                                     note=f"Missing weight task #{task.pk}")
            task.status = WeightTask.STATUS_AWAITING_PRICING
            task.save(update_fields=["approved_weight", "status"])
    return price_task(task_id, actor)


def _check_open(task):
    from orders.services import OperatorError

    if task.status == WeightTask.STATUS_RESOLVED:
        raise OperatorError("status", _("This task is already resolved."))
    if task.status == WeightTask.STATUS_CANCELLED:
        raise OperatorError("status", _("This task is cancelled."))


def price_task(task_id, actor):
    """Price every waiting customer with the part's current approved weight and the supplier's current price. The
    supplier is asked with no lock open; the task is then locked and rechecked (LPK-220). -> the task."""
    from orders.services import OperatorError

    task = WeightTask.objects.select_related("part").get(pk=task_id)
    _check_open(task)
    if task.status != WeightTask.STATUS_AWAITING_PRICING:
        raise OperatorError("proposed_weight_kg", _("Approve a weight first."))
    asked_at = (task.status, task.approved_weight_id)
    try:
        supplier_price = _current_supplier_price(task)
    except OperatorError as error:
        WeightTask.objects.filter(pk=task.pk).update(last_error=error.message)
        raise
    with transaction.atomic():
        task = WeightTask.objects.select_for_update(of=("self",)).select_related("part__current_weight").get(pk=task_id)
        if (task.status, task.approved_weight_id) != asked_at:
            raise OperatorError("status", _("The task changed while the supplier was being asked; try again."))
        current = task.part.current_weight
        if current is None:
            raise OperatorError("proposed_weight_kg", _("The part has no approved weight any more."))
        waiting = task.requests.select_for_update(of=("self",)).filter(outcome=WeightTaskRequest.OUTCOME_WAITING)
        for entry in waiting.select_related("customer__user", "search_log"):
            entry.outcome, entry.quote_request = _price_for(entry, task, current.weight_kg, supplier_price)
            entry.save(update_fields=["outcome", "quote_request", "updated_at"])
        task.status, task.resolved_at, task.resolved_by, task.last_error = (
            WeightTask.STATUS_RESOLVED, timezone.now(), actor, "")
        task.supplier_price_usd = supplier_price
        task.save(update_fields=["status", "resolved_at", "resolved_by", "last_error", "supplier_price_usd"])
    return task


def _current_supplier_price(task):
    """The supplier's price now for this exact part (number and manufacturer), never the price seen at search time."""
    from orders.services import OperatorError

    from .providers import PartsProviderError, fetch_rows

    try:
        rows = fetch_rows(task.part.part_number)[2]
    except PartsProviderError:
        raise OperatorError("supplier_price_usd", _("The supplier is unavailable; retry the pricing later.")) from None
    wanted = (task.part.part_number_key, task.part.brand_key)
    row = next((r for r in rows if identity(r.get("oem", ""), r["brand"]) == wanted), None)
    if row is None:
        raise OperatorError("supplier_price_usd", _("The supplier no longer offers this part; cancel the task."))
    return row["supplier_price_usd"]


def _price_for(entry, task, weight, supplier_price):
    """-> (outcome, quote request). One price per customer and task: their open weight-price request for the part is
    priced, or one is made from their latest search of it; a customer the operator already priced is left alone."""
    from .services import create_quote_request, mark_quote_price_ready

    customer = entry.customer
    if customer.anonymized_at or not customer.user.is_active or not customer.is_phone_verified:
        return WeightTaskRequest.OUTCOME_NOT_ELIGIBLE, None  # the same rule as asking for a quote (IsPhoneVerified)
    requests = PartQuoteRequest.objects.filter(customer=customer, part=task.part)
    # Priced after the task opened (by hand), or still holding a valid price with this very weight (an earlier task
    # resolved while their repeat search was being queued): one price and one notification, not two.
    ready = requests.filter(Q(price_ready_at__gte=task.created_at)
                            | Q(prepared_weight_kg=weight, price_valid_until__gt=timezone.now()),
                            status=PartQuoteRequest.STATUS_RESOLVED, price_ready_at__isnull=False).first()
    if ready:
        return WeightTaskRequest.OUTCOME_ALREADY_PRICED, ready
    quote = requests.filter(status__in=PartQuoteRequest.OPEN_STATUSES).first()
    if quote is None:
        log = entry.search_log
        if log is None or log.offer(entry.offer_id) is None:
            return WeightTaskRequest.OUTCOME_NOT_ELIGIBLE, None
        quote = create_quote_request(customer, {"request_type": PartQuoteRequest.REQUEST_TYPE_WEIGHT_PRICE,
                                                "offer_id": entry.offer_id}, log)[0]
    quote = PartQuoteRequest.objects.select_for_update().get(pk=quote.pk)
    quote.part, quote.prepared_weight_kg, quote.supplier_price_usd = task.part, weight, supplier_price
    mark_quote_price_ready(quote)  # the customer's own tariff; price_ready is sent once the transaction commits
    return WeightTaskRequest.OUTCOME_PRICED, quote


def cancel_task(task_id, actor):
    """-> False when the task was no longer open."""
    with transaction.atomic():
        task = WeightTask.objects.select_for_update().get(pk=task_id)
        if task.status not in WeightTask.OPEN_STATUSES:
            return False
        task.status, task.resolved_at, task.resolved_by = WeightTask.STATUS_CANCELLED, timezone.now(), actor
        task.save(update_fields=["status", "resolved_at", "resolved_by"])
    return True
