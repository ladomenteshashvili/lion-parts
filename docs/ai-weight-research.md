# AI part-weight research (LPK-247)

Backend and Django Admin only. No API field, value or endpoint is new, and neither frontend changes. **Off by default**
(`AI_WEIGHT_RESEARCH_ENABLED=False`). Builds on the Parts Master Registry (`docs/parts-registry.md`).

## 1. What it does

When an AMT search returns a part with no usable weight, the part already joins the missing-weight queue (LPK-240).
With this feature on, it also gets one **research job**. A cron command sends the job to an external research service
(an AI that searches the web), checks every observation the service returns, and records the result as a **pending AI
weight** with its sources, a confidence score and a written explanation. An operator approves or rejects it in the
admin. Until then it prices nothing: the offer stays `checking`, as today.

```
AMT search, no usable weight
  └─ WeightTask (LPK-240, unchanged) + WeightResearchJob (queued, once per part per 30 days)
        └─ cron: run_weight_research ──► research service (HTTPS, JSON)
              └─ checks here, in plain code ──► PartWeight(source=ai, status=pending) + WeightResearchSource rows
                    └─ operator: "Approve the AI weight and price the waiting customers"
                          └─ review_weight (LPK-239) + approve_task (LPK-240): customers priced with their own tariff
```

## 2. Rules

**What is sent.** Only the part number, manufacturer and the supplier's part name (`research_query`). Never a customer,
phone, email, VIN, search log or price. Logs name the job and an error type only, never a body, URL or key.

**When a job is queued** (`enqueue_research`, called from `queue_missing_weights`):

- the feature is on;
- the search used AMT (demo rows are invented and never trigger a paid search);
- the row has no usable weight (the same condition that opens a missing-weight task);
- the part has a manufacturer, so the evidence can be matched to it;
- the part has had no job in the last 30 days, whatever that job's outcome (`COOLDOWN`). The database allows one
  queued or running job per part, so concurrent searches create one job.

**Each observation counts only if** (`check`):

| Check | Refused with |
|---|---|
| The URL is https | "not an https URL" |
| The page names exactly this part number (normalized as everywhere: case, spaces, dashes, dots ignored) | "names a different part number" |
| The page names exactly this manufacturer (normalized as the registry does) | "names a different or no manufacturer" |
| The measurement is `net` or `shipping` weight | "measurement is not net or shipping weight" |
| The value and unit are understood: kg, g, lb, oz (and their spellings); `0,4` and `1 200` read correctly | "weight or unit not understood" |
| 0.01 to 500 kg, the registry's plausible range | "outside 0.01 to 500 kg" |

**Independence.** Observations are grouped by site (registrable domain, so `www.shop.example` and `parts.shop.example`
are one site). A site counts once, with the median of its values. Net and shipping weights are scored separately and
never mixed; shipping weight wins a tie, because the courier fee is charged on the shipped weight.

**Confidence** (`score`), every step written into the explanation:

| Step | Points |
|---|---|
| Sites agreeing within 10 % or 50 g of the median | at least 2 required, else no proposal |
| Base for 2 agreeing sites | 0.55 |
| Each further agreeing site | +0.10, at most +0.20 |
| Agreeing sites within 2 % of each other | +0.10 |
| Each site that disagrees | −0.15 |
| Cap | 0.95 |
| Below 0.50 | no proposal |

The proposed weight is the median of the agreeing sites, in kg with two decimals, like every weight field.

**Never overwrites.** A job for a part that already has an approved weight is skipped without a paid call. If a weight is
approved while the service is searching, the result is kept as evidence but not proposed. An approved AI weight ranks
lowest (AI < AMT < operator < physically measured, LPK-239), so approving it never displaces a stronger weight.

**Operator approval before prices.** The research only ever records `pending` AI weights. Only `review_weight`, run by an
operator in the admin, approves one. Pending weights are never used for pricing (LPK-239).

## 3. Limits

| Setting | Default | Meaning |
|---|---|---|
| `AI_WEIGHT_RESEARCH_ENABLED` | `False` | The feature flag. Off: nothing is queued and the command does nothing. |
| `AI_WEIGHT_RESEARCH_URL` | empty | The research service. Must be `https://` when on, or Django refuses to start. |
| `AI_WEIGHT_RESEARCH_API_KEY` | empty | Sent as `Authorization: Bearer`. Required when on. Lives in `.env` only. |
| `AI_WEIGHT_RESEARCH_TIMEOUT_SECONDS` | 60 | Read timeout per call (connect: 10 s). 1 to 300. |
| `AI_WEIGHT_RESEARCH_MAX_ATTEMPTS` | 3 | Attempts per job; retries after 10, then 20 minutes. 1 to 10. |
| `AI_WEIGHT_RESEARCH_JOBS_PER_RUN` | 10 | Jobs per cron run. 1 to 100. |
| `AI_WEIGHT_RESEARCH_MAX_COST_PER_JOB_USD` | 0.50 | Sent to the service as `max_cost_usd`. A reported cost above it discards the answer. |
| `AI_WEIGHT_RESEARCH_DAILY_BUDGET_USD` | 5.00 | A run stops before a job that could exceed it. |

Cost accounting is conservative: a failed, interrupted or unreported attempt counts at the per-job limit, because it
may have been billed. A job counts on the day of its latest attempt. Responses over 512 KB, redirects and non-200
answers are errors; at most 20 observations are read.

A run that dies mid-job (crash, kill) leaves the job `running`; after 30 minutes the next run counts that as a failed
attempt and queues it again. Two overlapping runs never run the same job (each claims it with one atomic update).

## 4. The research service contract

Django is provider-neutral: whichever AI and web search the owner chooses sits behind one HTTPS endpoint.

Request, `POST AI_WEIGHT_RESEARCH_URL`, JSON:

```json
{"part_number": "90915YZZE1", "manufacturer": "Toyota", "name": "OIL FILTER", "max_cost_usd": "0.50", "min_sources": 2}
```

Response, `200`, JSON:

```json
{"cost_usd": "0.04",
 "observations": [{"url": "https://…", "title": "…", "part_number": "90915-YZZE1", "manufacturer": "TOYOTA",
                   "value": "0.40", "unit": "kg", "measurement": "shipping", "excerpt": "Shipping weight: 0.40 kg"}]}
```

The service reports what each page says. It does not decide the weight: Django checks and scores everything itself, so a
service that hallucinates a value without a page naming the exact part and manufacturer contributes nothing.

## 5. Operator workflow (Django Admin, section "Parts")

**AI weight research** (`/admin/parts/weightresearchjob/`): one row per job, with status, proposed weight, confidence,
measurement, attempts, cost and last error. Open a row to see the explanation and every source: the link, what the page
said, the weight in kg, whether it counted, whether it agrees with the proposal, and why.

- **Approve the AI weight and price the waiting customers.** Approves the weight (audit entry under its name). If the part
  has an open missing-weight task, the task is resolved as if approved there: every waiting customer is priced with their
  own tariff and the supplier's current price, and notified once. Repeating it changes nothing and says so.
- **Reject the AI weight.** Never used; stays on record.
- **Research again.** A new job for the part (a paid search), unless one is already queued.
- Jobs cannot be edited or deleted: the evidence behind every AI weight stays on record.

**Missing weight queue** (`/admin/parts/weighttask/`): a new column **AI suggestion** shows the latest job's weight,
confidence and status, linked to it. The existing workflow is unchanged: the operator can still enter a weight
by hand.

**Part weights** (`/admin/parts/partweight/`): AI weights appear with source "AI estimate", status "awaiting approval",
and evidence naming the job, the confidence, the explanation and the sources. The existing **Approve** and **Reject**
actions work on them as before.

## 6. Customer effect

None until an operator approves. After approval, the same as an operator-entered weight (`docs/parts-registry.md` §3):
waiting logged-in customers get `price_ready` once, and later searches are priced directly. The public `weight_source`
of an approved AI weight is `operator`, an existing value (G§8.0), since an operator approved it. Optional
customer weight input (calculate-price), carts, orders and payments are untouched.

## 7. Deployment (later, manually; not run in this batch)

1. Deploy as usual (`docs/staging-runbook.md` §5). Migration `parts.0010_ai_weight_research` only creates two tables.
2. The feature stays off. To try it, the owner needs a research service and its key. Then, in `.env`:
   `AI_WEIGHT_RESEARCH_ENABLED=True`, `AI_WEIGHT_RESEARCH_URL=https://…`, `AI_WEIGHT_RESEARCH_API_KEY='…'`, and the
   limits above. Restart.
3. Add the cron line from `deploy/staging/crontab.example` (every 15 minutes).
4. Watch `/admin/parts/weightresearchjob/` and the cost column; `run_weight_research` prints what it ran.

**Rollback:** set `AI_WEIGHT_RESEARCH_ENABLED=False` and restart: nothing is queued or run, and pending AI weights stay
unused. Code rollback: the previous code ignores the two new tables. Schema rollback, only if needed and after a backup:
`manage.py migrate parts 0009` drops the two research tables. AI weights already recorded stay in `PartWeight`.

## 8. Decisions for the owner

1. **Which AI service.** Django calls one HTTPS endpoint (section 4). Building that service (which model, which web
   search, its prompt) and paying for it is a separate decision; nothing here calls a paid API.
2. **Shipping weight wins a tie with net weight**, because the courier fee is per shipped kg. A part with only net-weight
   evidence gets a net weight, marked as such.
3. **Thresholds**: 2 independent sites, 10 % or 50 g agreement, minimum confidence 0.50, one search per part per 30
   days, $0.50 per job and $5 per day. All are constants in `parts/weight_research.py` or settings.
4. **Approving an AI weight prices the waiting customers immediately**, as approving a task does today. The alternative is
   to approve the weight only and price from the task separately.
5. **Jira LPK-247 was not readable from the build environment**; this was built from the owner's written brief. Compare
   with the Jira acceptance criteria before merging.
