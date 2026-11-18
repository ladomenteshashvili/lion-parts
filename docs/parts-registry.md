# Parts Master Registry (LPK-238, LPK-239, LPK-240)

Backend and Django Admin only. No API field, value or endpoint is new, and neither frontend changes.

## 1. What it does

| Story | Delivered |
|---|---|
| LPK-238 registry | `PartMaster`: one row per normalized part number **and** manufacturer. Every search links its supplier rows to their parts (`PartSupplierOffer`: supplier price history, per search log) and counts the demand (`search_count`, `vin_search_count`, `last_searched_at`). Weight-price quote requests link to their part. `backfill_part_registry` fills it from stored searches. |
| LPK-239 weights | `PartWeight` observations (`amt`, `ai`, `operator`, `physically_measured`) with status, observation date, reviewer, evidence, conflict and plausibility flags, and an audit trail (`PartWeightEvent`). The strongest approved weight is the part's current weight and is reused for pricing. |
| LPK-240 queue | `WeightTask`: one open task per part that the supplier prices with no usable weight, with the waiting customers (`WeightTaskRequest`). Approving a weight prices each waiting customer with their own tariff and the supplier's current price, through the existing quote request "price ready" flow, and notifies each once. |

### Identity

- Part number key: the existing `normalize_part_number` (upper case, no spaces, dashes or dots).
- Manufacturer key: letters and digits only, upper case, so `Mercedes-Benz` = `MERCEDES BENZ`. There is no alias table:
  `Toyota` and `Lexus` stay two parts even with the same number.
- The identity comes from the supplier row (its OEM number and brand), not from what the customer typed.
- A weight belongs to exactly one part and is never looked up by part number alone. A manual quote request names no
  manufacturer, so it is not linked to a part and does not create one.

### Which weight prices an offer

1. An approved operator or physically measured weight, when the part has one. A measurement beats an operator entry,
   and a newer one beats an older one of the same source.
2. Otherwise the supplier's own weight on this search, if it is between 0.01 and 500 kg and an operator has not
   rejected that value for this part.
3. Otherwise the part's approved weight on record, such as an earlier AMT weight or an approved AI estimate.
4. Otherwise none: the offer is `checking`, as before, and the part joins the missing-weight queue.

If the registry itself cannot be read during a search, no supplier weight is used: it cannot be checked against
rejections, so the offers are `checking` until the registry is back (fail closed). Pending and rejected weights are never used. Every reused weight is priced again for the customer who searches, with
their tariff, the current supplier price, shipping, VAT and discounts, through `pricing.engine.price_offer`. No price
is ever copied from another customer. Offers keep `weight_source` `api` for a supplier weight and show `operator` for
an operator-approved one, both existing values (G§8.0).

### Evidence rules

- Zero, negative, non-numeric and non-finite weights are refused everywhere; the database also refuses zero.
- AMT weights are recorded automatically: approved when plausible, pending when outside 0.01 to 500 kg.
- When AMT reports the same weight again, it is counted as seen again. An operator's rejection of that value stays in force.
- AI estimates are always pending until approved.
- Operator and physically measured weights entered in the admin are approved by the operator who enters them.
- A new weight that differs from the current one by more than 20 % and more than 0.05 kg is flagged as a conflict.
  A weaker weight never replaces a stronger one.
- Demo-provider weights are invented, so they are never recorded. The demo profile still exercises the queue.
- A weight the operator enters on a weight-price quote request becomes an approved operator weight for its part.
- A customer's own weight (calculate-price) is not evidence and is never recorded.

## 2. Operator workflow (Django Admin, section "Parts")

**Missing weight queue** (`/admin/parts/weighttask/`):

1. Filter **queue: open**. Each row is one part: status, waiting customers, searches (guests included), last supplier price.
2. Open a row. Enter **weight to approve (kg)**, choose the **weight source** (operator or physically measured), add
   **evidence** if any, and press **Save**.
3. In the list, select it and run **Approve the weight and price the waiting customers**. The message says how many
   customers were priced and notified, how many were not eligible, and how many already had a price.
   - **Not eligible:** the customer's phone is not verified, or the account is inactive or anonymized. This is the same
     rule as asking for a quote.
   - **Already had a price:** an operator priced their request by hand after the task opened.
4. If the supplier is unavailable, the weight stays approved and the status becomes **weight approved, pricing failed**
   with the error. Run the same action again later. A task the supplier no longer offers can be cancelled.
5. To use a weight already approved for the part, for example on a quote request, leave the weight empty and run the action.
6. Running the action again on a resolved task changes nothing and says so.

**Parts registry** (`/admin/parts/partmaster/`): search by number or manufacturer, and filter by **weight: known / no approved
weight** or by source. The page shows demand counts and the weight history. It names no customer.

**Part weights** (`/admin/parts/partweight/`):

- **Add a weight:** use the add page. A correction is a new observation.
- **Review:** filter by status, source, conflict or plausibility. Use **Approve** or **Reject the selected weights**.
- **History:** every change is in the weight's history.
- **Deleting:** not allowed, so the audit trail stays complete.

## 3. Customer effect

- A customer who searched a `checking` part while logged in finds a `price_ready` item in history, and gets the
  `price_ready` notification once per part. They do not search again.
- The price follows their own tariff and validity, and the item is added to the cart as a prepared quote, as before.
- Guests are counted but cannot be notified. Their next search is priced with the approved weight.
- Searches after approval are priced directly, with no new task.

## 4. Backfill (owner, on staging)

`backfill_part_registry` reads stored successful searches and:

- creates parts and supplier offers;
- counts demand;
- records AMT weights from the stored offers (supplier weights only, never customer weights);
- links weight-price quote requests to their part.

It never changes stored offers or their price snapshots, opens no tasks and notifies nobody. A search already linked is
skipped, so running it again adds nothing. Without `--apply` it runs in a transaction that is rolled back and prints
what it would do.

```bash
cd ~/lion-parts/backend
venv/bin/python manage.py backfill_part_registry            # dry run: prints counts, saves nothing
venv/bin/python manage.py backfill_part_registry --apply    # writes; safe to repeat
```

## 5. Deployment (later, manually; not run in this batch)

As `lionparts-stage`, after the branch is merged and `<new-ref>` is the merge commit:

```bash
cd ~/lion-parts
git rev-parse HEAD                                   # note it as <old-ref>
deploy/staging/stage.sh check
deploy/staging/stage.sh backup                       # note the dump path
deploy/staging/stage.sh deploy <new-ref>             # shows the plan (parts.0009_parts_registry), migrates, checks
```

Then, as the owner: `systemctl restart lionparts-stage`, and as `lionparts-stage`:

```bash
deploy/staging/stage.sh health
cd backend && venv/bin/python manage.py backfill_part_registry && venv/bin/python manage.py backfill_part_registry --apply
```

The migration only creates tables and adds one nullable column (`parts_partquoterequest.part_id`). It rewrites no table
and deletes nothing.

## 6. Rollback

- **Code only (preferred):** `git checkout --detach <old-ref>` and restart. The old code ignores the new tables and the
  nullable column, and nothing is lost. `stage.sh rollback <old-ref>` also works; it warns that the migrations differ,
  which is expected here.
- **Schema too:** only if really needed, after a backup: `venv/bin/python manage.py migrate parts 0008`. This **drops
  the registry tables and their data** (parts, weights, queue). Searches, quote requests, carts and orders are untouched.

## 7. Limits

- The search does a few more queries per call, batched per search. The registry is written after the supplier call
  and outside any lock (LPK-220). A registry failure is logged and never fails a search.
- Recording a search is idempotent: the search log is locked, and a log already recorded (a retry, a parallel call or
  the backfill) adds no counts, offers, weights or queue entries.
- A search that reaches the queue while a pricing run is resolving the same part's task waits for it and then opens a
  new task, never joins the resolved one. A customer who already holds a valid price with the same weight is reported
  as "already had a price" there, so nobody is priced or notified twice.
- Two simultaneous searches (different logs) may both record the same new AMT weight once. That is harmless: same value and same rank.
- The waiting-customer list keeps each customer's latest search of the part only.
- A part number with several supplier rows of the same manufacturer is one part; the latest row's price is the "last
  supplier price".
- The plausibility range (0.01 to 500 kg) and the conflict threshold (20 % and 0.05 kg) are constants in
  `parts/registry.py`.

## 8. Decisions for the owner

1. **An operator or measured weight beats the supplier's weight on later searches**, which changes the price from what
   AMT's weight alone would give. That is the purpose of a correction, but it is a pricing behaviour change.
2. **Implausible AMT weights (< 0.01 kg or > 500 kg) are no longer priced** and wait for review instead.
3. **Customers are priced and notified without asking**, if they searched the part while logged in and their phone is
   verified. The alternative is to price only those who sent a quote request.
4. **The backfill opens no tasks** for old `checking` searches, so nobody is notified about old searches.
5. **AI estimates have no generator yet.** The source exists and an operator can record one; no AI service is called.
