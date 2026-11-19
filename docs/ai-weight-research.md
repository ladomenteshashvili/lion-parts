# AI part-weight research (LPK-247)

Backend and Django Admin only. No API field, value or endpoint is new, and nothing changes in the frontend. **Off by
default** (`AI_WEIGHT_RESEARCH_ENABLED=False`). Builds on the Parts Master Registry (`docs/parts-registry.md`).

## 1. What it does

When an AMT search returns a part with no usable weight, the part already joins the missing-weight queue (LPK-240).
With this feature on, it also gets one **research job**. A cron command asks a research provider (an AI that searches
the web) where the part's weight is published. **Nothing the provider says is trusted**: this server fetches every cited
page itself and checks that the page really shows the part number, the manufacturer and the weight. Only verified pages
count. The result is a **pending AI weight** with its sources, an evidence score and a written explanation, or **needs
review** with the reasons. An operator approves or rejects it in the admin. Until then it prices nothing and notifies
nobody: the offer stays `checking`, as today.

```
AMT search, no usable weight
  └─ WeightTask (LPK-240, unchanged) + WeightResearchJob (queued, once per part per 30 days)
        └─ cron: run_weight_research
              ├─ reserve the attempt's cost (daily budget, row-locked) ─► provider (PROVIDERS: "http" today)
              ├─ claims checked (exact part, manufacturer, unit, plausible weight)
              ├─ each cited page fetched here (parts.evidence: SSRF-safe) and checked
              └─ verified sites scored ─► PartWeight(source=ai, status=pending) + WeightResearchSource rows
                    ├─ operator: "Approve the AI weight only"            → nobody priced or notified
                    └─ operator: "Approve the AI weight and price …"     → confirmation page → approve_task (LPK-240)
```

## 2. Rules

**What is sent.** Only the part number, manufacturer and the supplier's part name (`research_query`), plus the cost limit
per attempt. Never a customer, phone, email, VIN, search log or price. Logs name the job and an error type only, never a
body, URL or key. Page fetches send no cookies or credentials, only `User-Agent: LionPartsWeightCheck/1.0`.

**When a job is queued** (`enqueue_research`, called from `queue_missing_weights`):

- the feature is on;
- the search used AMT (demo rows are invented and never trigger a paid search);
- the row has no usable weight (the same condition that opens a missing-weight task);
- the part has a manufacturer, so the evidence can be matched to it;
- the part has had no job in the last 30 days, whatever that job's outcome (`COOLDOWN`). The database allows one
  queued or running job per part, so concurrent searches create one job.

### 2.1 The provider's claims (`check`)

Before any page is fetched, each observation must:

| Check | Refused with |
|---|---|
| https URL on a public registrable domain | "not an https URL on a public site" |
| Exactly this part number (case, spaces, dashes, dots ignored) | "names a different part number" |
| Exactly this manufacturer (normalized as the registry does) | "names a different or no manufacturer" |
| Measurement `shipping`, `net` or unspecified | "measurement is not net, shipping or unspecified weight" |
| Value and unit understood: kg, g, lb, oz (and spellings); `0,4` and `1 200` read correctly | "weight or unit not understood" |
| 0.01 to 500 kg, the registry's plausible range | "outside 0.01 to 500 kg" |

Anything else the provider sends, such as `"verified": true`, is ignored.

### 2.2 Independent page verification (`parts/evidence.py`)

Every remaining observation's page is fetched by this server and checked. The page is **verified** only when its
visible text (scripts and styles ignored) shows:

- the exact part number, not as part of a longer code (`90915-YZZE12` does not match `90915YZZE1`);
- the manufacturer's name;
- a weight next to a weight word (weight, gewicht, poids, peso, вес, масса, წონა) that matches the claim within 3 % or
  10 g, read from the page itself.

The weight used is the one read from the page, never the provider's number. The measurement used is the one the page
labels (shipping/package/gross, or net). An unlabelled weight counts as "net or shipping not stated" and scores lower.

| Page outcome | Meaning | Counts? |
|---|---|---|
| verified | part, manufacturer and weight shown as claimed | yes |
| contradicted | the page shows a different weight: the provider misquoted it | no, and lowers the score |
| ambiguous | several weights without the claimed one labelled, or more than 4 different weights (a list of parts) | no |
| not shown on the page | the part number, the manufacturer or any weight is missing | no |
| no readable text | under 200 visible characters: JavaScript-only, empty or a challenge page | no |
| page not reachable | DNS failure, timeout, 404, 5xx, too large, too many redirects | no |
| blocked by the site | 401, 403, 429, 451 | no |
| unsafe address, not fetched | see below | no |
| not checked | the claim failed 2.1, or the job's 90 s page-checking time was used up | no |

**Fetching safely (SSRF).** An AI chooses these URLs, so every request:

- is https on port 443 to a host name; IP literals, `user:password@` and other ports are refused;
- resolves the name once and refuses it if **any** address is private, loopback, link-local, carrier-grade NAT,
  multicast, reserved or otherwise not global (IPv4-mapped IPv6 included);
- connects to that very address, with the name used only for TLS verification and the Host header. A DNS answer cannot
  change between the check and the connection, which blocks DNS rebinding;
- follows at most 3 redirects, each checked again in full (scheme, port, name, addresses);
- has a 5 s connect and 10 s read timeout, an overall deadline per job, a 2 MB size limit, and HTML or plain text only;
- ignores proxy environment variables (urllib3 directly) and verifies certificates against certifi.

**Independence.** Sites are grouped by registrable domain using the Public Suffix List (`backend/parts/data/
public_suffix_list.dat`, Mozilla, MPL 2.0, vendored, never fetched at run time). `www.shop.co.uk` and
`parts.shop.co.uk` are one site; `a.github.io` and `b.github.io` are two. A redirected page counts for the site it
landed on. A site counts once, with the median of its values; the same page cited twice is fetched once.

**Credibility.** A domain named like the manufacturer (`toyota.com` for Toyota) or listed in `trusted_sites` adds a
bonus. Marketplace listings (eBay, Amazon, AliExpress and similar, `marketplaces`) are shown to the operator but never
counted.

### 2.3 Evidence score (`score`, `Rules`)

Verified sites are scored per measurement (shipping, net, unspecified: never mixed). Every step is written into the
explanation. **The evidence score is a rule-based summary of the evidence, not a calibrated probability.** Bands:
strong ≥ 0.80, moderate ≥ 0.60, weak below.

| Rule (`Rules` field) | Default |
|---|---|
| Verified independent sites that must agree (`min_sources`) | 2 |
| Agreeing: within (`agree_ratio`) or (`agree_min_kg`) of the median | 10 % or 50 g |
| Base for `min_sources` agreeing sites (`base`) | 0.50 |
| Each further agreeing site (`per_extra_site`, at most `max_extra`) | +0.10, at most +0.20 |
| Agreeing sites within `tight_ratio` of each other (`tight_bonus`) | 2 %: +0.10 |
| The manufacturer's site or a trusted catalog agrees (`trusted_bonus`) | +0.10 |
| Pages do not say net or shipping (`unspecified_penalty`) | −0.10 |
| Each verified site that disagrees (`disagree_penalty`) | −0.20 |
| Each page the provider misquoted (`misquote_penalty`) | −0.05 |
| Cap (`cap`) | 0.90 |
| Below `min_score`: no proposal, needs review | 0.60 |
| Trusted catalogs (`trusted_sites`) | none (owner decision) |
| Page checks per job (`verify_seconds`) | 90 s |

Override any rule with `AI_WEIGHT_RESEARCH_RULES = {"min_sources": 3, "trusted_sites": ["rockauto.com"]}` in settings;
an unknown key fails loudly. Conflicting evidence (a verified site that disagrees, or a misquoted page) sets the job's
**conflicting evidence** flag, is named in the explanation and is shown in the missing-weight queue.

The measurement with a proposal and the higher score wins; shipping beats net beats unspecified on a tie, because the
courier fee is charged on the shipped weight. The others are noted as "also verified, not proposed". The proposed
weight is the median of the agreeing sites, in kg with two decimals, like every weight field.

**No reliable evidence means needs review**: no verified site, fewer than `min_sources` agreeing, or a score below
`min_score`. No weight is invented.

**Never overwrites.** A job for a part that already has an approved weight is skipped without a paid call. If a weight is
approved while the provider is searching, the result is kept as evidence but not proposed. An approved AI weight ranks
lowest (AI < AMT < operator < physically measured, LPK-239), so approving it never displaces a stronger weight.

## 3. Limits and spending

| Setting | Default | Meaning |
|---|---|---|
| `AI_WEIGHT_RESEARCH_ENABLED` | `False` | The feature flag. Off: nothing is queued and the command does nothing. |
| `AI_WEIGHT_RESEARCH_PROVIDER` | `http` | The only provider until LPK-248. |
| `AI_WEIGHT_RESEARCH_URL` | empty | The `http` provider. Must be `https://` when on, or Django refuses to start. |
| `AI_WEIGHT_RESEARCH_API_KEY` | empty | Sent as `Authorization: Bearer`. Required when on. Lives in `.env` only. |
| `AI_WEIGHT_RESEARCH_TIMEOUT_SECONDS` | 60 | Read timeout per provider call (connect: 10 s). 1 to 300. |
| `AI_WEIGHT_RESEARCH_MAX_ATTEMPTS` | 3 | Attempts per job; retries after 10, then 20 minutes. 1 to 10. |
| `AI_WEIGHT_RESEARCH_JOBS_PER_RUN` | 10 | Jobs per cron run. 1 to 100. |
| `AI_WEIGHT_RESEARCH_MAX_COST_PER_ATTEMPT_USD` | 0.50 | One provider call. Sent as `max_cost_usd`; reserved before the call. |
| `AI_WEIGHT_RESEARCH_MAX_COST_PER_JOB_USD` | 1.50 | All attempts of one job together. |
| `AI_WEIGHT_RESEARCH_DAILY_BUDGET_USD` | 5.00 | All calls of one local day together. |

Django refuses to start when on unless `per attempt ≤ per job ≤ daily budget`.

**How spending is enforced.** Before every paid call, the attempt's limit is **reserved** in `WeightResearchSpend` (one
row per local day). The reservation and the job's claim happen in one transaction that locks the day's row, so
overlapping cron runs are serialized: they can never reserve beyond the daily budget, and never run one job twice.
After the call:

- a reported cost replaces the reservation, on the job and on the day;
- a reported cost over the per-attempt limit is recorded in full and the answer is discarded ("cost limit exceeded");
- an unknown cost, a failure, a timeout or a crash keeps the full reservation, because it may have been billed.

A job whose next attempt could exceed its per-job limit fails ("job cost limit reached") without a call. A run that
dies mid-job leaves the job `running`. After 30 minutes the next run counts that as a failed attempt ("interrupted"),
keeps its reservation once, and queues it again with backoff. The day's total is under **AI research spending** in the
admin.

## 4. Providers (LPK-248 ready)

`PROVIDERS` maps a provider name to a dotted path of a function `query -> (observations, cost or None)` that raises
`ResearchError` on failure. Today there is one, `http`: a provider-neutral HTTPS service.

Request, `POST AI_WEIGHT_RESEARCH_URL`, JSON:

```json
{"part_number": "90915YZZE1", "manufacturer": "Toyota", "name": "OIL FILTER", "max_cost_usd": "0.50", "min_sources": 2}
```

Response, `200`, JSON (at most 512 KB and 20 observations; redirects and non-200 answers are errors):

```json
{"cost_usd": "0.04",
 "observations": [{"url": "https://…", "title": "…", "part_number": "90915-YZZE1", "manufacturer": "TOYOTA",
                   "value": "0.40", "unit": "kg", "measurement": "shipping", "excerpt": "Shipping weight: 0.40 kg"}]}
```

LPK-248 adds `anthropic` and `openai` the same way: a function in its own module, added to `PROVIDERS` and to the
settings check. All providers share the claim checks, page verification, scoring, budget and approval described here. A
structured catalog (RockAuto, Inter Cars, TecDoc and others) can be a provider too. If it has an authenticated API whose
answers need no page check, that check belongs in `verify()`, the single place where observations are confirmed. The job
records which provider ran it.

## 5. Operator workflow (Django Admin, section "Parts")

**AI weight research** (`/admin/parts/weightresearchjob/`): one row per job, with status, proposed weight, evidence
score, conflicting evidence, measurement, attempts, cost, provider and last error. Open a row to see the explanation, the
number of customers waiting, and every source with:

- the link;
- the page check;
- the source type;
- whether it counted, whether it agrees, and why;
- the weight and measurement found on the page, with the text found around it;
- what the provider claimed.

| Action | Effect | Permission |
|---|---|---|
| **Approve the AI weight only (nobody is priced or notified)** | Approves the weight (audit entry). Later searches use it unless a stronger weight exists. Waiting customers stay on the missing-weight task; the message says how many. | change part weight |
| **Approve the AI weight and price the waiting customers…** | A confirmation page names how many customers will be priced and notified; the operator must tick "I understand … cannot be undone". Then the weight is approved and the open missing-weight task is resolved as if approved there: each waiting customer is priced with their own tariff and the supplier's current price, and notified once. | change part weight and change missing-weight task |
| **Reject the AI weight** | Never used; stays on record. | change part weight |
| **Research again** | A new job for the part (a paid search), unless one is already queued. | add research job |

Repeating an approval changes nothing and says so; a resolved task is never priced twice. Jobs cannot be edited or
deleted: the evidence behind every AI weight stays on record.

**Missing weight queue** (`/admin/parts/weighttask/`): the **AI suggestion** column shows the latest job's weight,
evidence score, a conflicting-evidence warning and the weight's status, linked to the job. The existing workflow is
unchanged: the operator can still enter a weight by hand, or run the task's own approve action, which uses the part's
approved weight on record (an approved AI weight included).

**Part weights** (`/admin/parts/partweight/`): AI weights appear with source "AI estimate", status "awaiting approval",
and evidence naming the job, the evidence score, the explanation and the verified sources. The existing **Approve** and
**Reject** actions work on them as before; like "approve only", they price nobody.

## 6. Customer effect

None until an operator approves; no AI result prices or notifies anybody by itself. After "approve only", later searches
of the part are priced with it, if no stronger weight exists. After "approve and price", the same as an
operator-entered weight (`docs/parts-registry.md` §3): waiting logged-in customers get `price_ready` once. The public
`weight_source` of an approved AI weight is `operator`, an existing value (G§8.0), since an operator approved it.
Optional customer weight input (calculate-price), carts, orders and payments are untouched.

## 7. Deployment (later, manually; not run in this batch)

1. Deploy as usual (`docs/staging-runbook.md` §5). Migration `parts.0010_ai_weight_research` only creates three tables.
2. The feature stays off. To try it, the owner needs a research service and its key. Then, in `.env`:
   - `AI_WEIGHT_RESEARCH_ENABLED=True`
   - `AI_WEIGHT_RESEARCH_URL=https://…`
   - `AI_WEIGHT_RESEARCH_API_KEY='…'`
   - the limits above.

   Then restart.
3. The server must be able to reach public https sites (outbound port 443) to check pages. It needs no other access.
4. Add the cron line from `deploy/staging/crontab.example` (every 15 minutes).
5. Watch these, and the output of `run_weight_research`:
   - `/admin/parts/weightresearchjob/`
   - the explanations
   - **AI research spending**

**Rollback:**
- **Feature:** set `AI_WEIGHT_RESEARCH_ENABLED=False` and restart. Nothing is queued or run, and pending AI weights stay
  unused.
- **Code:** the previous code ignores the three new tables.
- **Schema** (only if needed, and after a backup): `manage.py migrate parts 0009` drops the research tables. AI weights
  already recorded stay in `PartWeight`.

## 8. Decisions for the owner

1. **Providers.** LPK-248 integrates Anthropic Claude and OpenAI; this PR builds neither and calls no paid API.
2. **Trusted catalogs** (`trusted_sites`) are empty by default. Which catalogs earn the +0.10 is a business decision.
3. **Thresholds** in `Rules` and the limits in §3. They are deliberately conservative:
   - 2 verified sites;
   - score at least 0.60;
   - $0.50 per attempt, $1.50 per job, $5 per day.
4. **Shipping weight wins a tie with net weight**, because the courier fee is per shipped kg. A part with only
   net-weight evidence gets a net weight, marked as such.
5. **Pages that need JavaScript, block robots, or are PDFs never verify.** That may leave some parts at "needs review",
   which is the intended conservative outcome. robots.txt is not consulted. Each cited page is fetched once per job and
   identified as `LionPartsWeightCheck/1.0`.
6. **Jira LPK-247 was not readable from the build environment.** This was built from the owner's written briefs.
   Compare it with the Jira acceptance criteria before merging.
