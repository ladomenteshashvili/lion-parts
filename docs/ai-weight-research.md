# AI part-weight research (LPK-247, LPK-248)

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
              ├─ reserve the attempt's calls (daily budget, row-locked) ─► provider, then fallbacks on failure
              │     (PROVIDERS: "anthropic" Claude, "openai", "http"; every call audited in WeightResearchCall)
              ├─ URLs the provider's own search never returned: dropped
              ├─ claims checked (exact part, manufacturer, unit, plausible weight)
              ├─ each cited page fetched here (parts.evidence: SSRF-safe) and checked
              └─ verified sites scored ─► PartWeight(source=ai, status=pending) + WeightResearchSource rows
                    ├─ operator: "Approve the AI weight only"            → nobody priced or notified
                    └─ operator: "Approve the AI weight and price …"     → confirmation page → approve_task (LPK-240)
```

## 2. Rules

**What is sent.** Only the part number, manufacturer and the supplier's part name (`research_query`), plus the number
of sources wanted (and, to the `http` service, the cost limit per call). Never a customer, phone, email, VIN, contact,
search log or price; a test checks the provider requests for them. Logs name the job, the provider and an error type
only, never a body, URL or key. Page fetches send no cookies or credentials, only `User-Agent: LionPartsWeightCheck/1.0`.

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
  10 g, read from the page itself;
- and the three **belong together**.

**Belonging together** is checked against the page's HTML structure. Being present somewhere on a category or listing
page is not enough. The weight counts only when one of these holds:

| The weight is … | together with |
|---|---|
| in the same table row or list item | the part number and the manufacturer |
| in the same product record: an `<article>`, a schema.org `Product`, or a list item or table row that is a product card (not a specification line) | the part number and the manufacturer |
| in a specification block (a two-column table, a definition list, a list of short "label: value" lines) | the part number, labelled as such ("Part number:", "OEM", "Артикул" …); the manufacturer is named outside other products' records |
| outside every product record, on a page with exactly one `<h1>` | that `<h1>` naming the part number; the manufacturer is named outside other products' records |

Anything else is **ambiguous**, for example:
- a listing card or table row of another product;
- a page with two product headings;
- a manufacturer named only in another product's card;
- a plain-text page, which has no structure to tie the weight to the part.

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
| not allowed by robots.txt | robots.txt disallows the page for `LionPartsWeightCheck`, or refuses access to itself (401, 403, other 4xx except 404/410) | no; the page is never requested |
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
- respects **robots.txt** (RFC 9309), checked before every request, redirect hops included, with the product token
  `LionPartsWeightCheck`:
  - a missing robots.txt (404, 410) allows everything;
  - a robots.txt that refuses access is treated as "disallowed";
  - one that cannot be read safely (5xx, timeout, over 512 KB, unsafe address) leaves the page unverified and unfetched;
  - each host's robots.txt is read once per job;
- ignores proxy environment variables (urllib3 directly) and verifies certificates against certifi.

**Independence.** Sites are grouped by registrable domain using the Public Suffix List (`backend/parts/data/
public_suffix_list.dat`, Mozilla, MPL 2.0, vendored, never fetched at run time). `www.shop.co.uk` and
`parts.shop.co.uk` are one site; `a.github.io` and `b.github.io` are two. A redirected page counts for the site it
landed on. A site counts once, with the median of its values; the same page cited twice is fetched once.

**Credibility** comes only from reviewed allowlists. A domain that merely looks like a brand (`toyota.com`, or
`toyota-parts.com`) is a standard source.
- `manufacturer_sites`: official manufacturer domains per brand, for example
  `{"Toyota": ["toyota.com"]}`. A domain counts for that brand only.
- `trusted_sites`: trusted catalogs.

Both are empty by default. Every entry must be exactly a registrable domain, or the rules fail to load. Either adds the
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
| An allowlisted manufacturer site or trusted catalog agrees (`trusted_bonus`) | +0.10 |
| Pages do not say net or shipping (`unspecified_penalty`) | −0.10 |
| Each verified site that disagrees (`disagree_penalty`) | −0.20 |
| Each page the provider misquoted (`misquote_penalty`) | −0.05 |
| Cap (`cap`) | 0.90 |
| Below `min_score`: no proposal, needs review | 0.60 |
| Trusted catalogs (`trusted_sites`) | none (owner decision) |
| Official manufacturer sites per brand (`manufacturer_sites`) | none (owner decision) |
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
| `AI_WEIGHT_RESEARCH_PROVIDER` | `http` | The primary provider: `http` (the LPK-247 default), `anthropic` or `openai` (§4). Claude and OpenAI are opted into explicitly. |
| `AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS` | empty | Comma-separated, tried in order when the one before fails (§3.1). |
| `AI_WEIGHT_RESEARCH_ANTHROPIC_API_KEY` | empty | Claude key. Required when `anthropic` is in use. `.env` only. |
| `AI_WEIGHT_RESEARCH_ANTHROPIC_MODEL` | `claude-opus-5-5` | Claude model id. |
| `AI_WEIGHT_RESEARCH_ANTHROPIC_SEARCH_TOOL` | `web_search_20260209` | Claude's web search tool version. |
| `AI_WEIGHT_RESEARCH_ANTHROPIC_TIMEOUT_SECONDS` | 180 | Per Claude call. 1 to 600. |
| `AI_WEIGHT_RESEARCH_OPENAI_API_KEY` | empty | OpenAI key. Required when `openai` is in use. `.env` only. |
| `AI_WEIGHT_RESEARCH_OPENAI_MODEL` | `gpt-6.2-astra` | OpenAI model id. |
| `AI_WEIGHT_RESEARCH_OPENAI_SEARCH_TOOL` | `web_search` | OpenAI's hosted web search tool type. |
| `AI_WEIGHT_RESEARCH_OPENAI_TIMEOUT_SECONDS` | 180 | Per OpenAI call. 1 to 600. |
| `AI_WEIGHT_RESEARCH_MAX_SEARCHES` | 5 | Web searches per call (`max_uses` / `max_tool_calls`). 1 to 20. |
| `AI_WEIGHT_RESEARCH_MAX_OUTPUT_TOKENS` | 8000 | Output tokens per call. 1000 to 64000. |
| `AI_WEIGHT_RESEARCH_PRICES` | `{}` | USD prices per model id for the cost estimate (§3.2). None built in. |
| `AI_WEIGHT_RESEARCH_URL` | empty | The `http` provider. Must be `https://` when it is in use, or Django refuses to start. |
| `AI_WEIGHT_RESEARCH_API_KEY` | empty | The `http` provider, sent as `Authorization: Bearer`. `.env` only. |
| `AI_WEIGHT_RESEARCH_TIMEOUT_SECONDS` | 60 | Read timeout per `http` call (connect: 10 s). 1 to 300. |
| `AI_WEIGHT_RESEARCH_MAX_ATTEMPTS` | 3 | Attempts per job; retries after 10, then 20 minutes. 1 to 10. |
| `AI_WEIGHT_RESEARCH_JOBS_PER_RUN` | 10 | Jobs per cron run. 1 to 100. |
| `AI_WEIGHT_RESEARCH_MAX_COST_PER_ATTEMPT_USD` | 0.50 | One provider call ("per call"). Reserved before the call. |
| `AI_WEIGHT_RESEARCH_MAX_COST_PER_JOB_USD` | 1.50 | All calls of one job together. |
| `AI_WEIGHT_RESEARCH_DAILY_BUDGET_USD` | 5.00 | All calls of one local day together. |

When on, Django refuses to start unless:
- `per call ≤ per job ≤ daily budget`;
- the primary and fallback providers are known and distinct, and each one in use has its key (and model or URL);
- the search, token and timeout limits are in range, and `AI_WEIGHT_RESEARCH_PRICES` is valid.

**How spending is enforced.** Before any paid call, the attempt **reserves** the per-call limit for each provider it
may call, in `WeightResearchSpend` (one row per local day). The reservation and the job's claim happen in one
transaction that locks the day's row, so overlapping cron runs are serialized: they can never reserve beyond the daily
budget, and never run one job twice. After the calls:

- an estimated or reported cost replaces a call's reservation, on the job and on the day;
- a provider that was not needed (an earlier one answered) gives its reservation back;
- a call never sent (no key configured) costs nothing;
- a cost over the per-call limit is recorded in full and the answer is discarded ("cost limit exceeded");
- an unknown cost, a failure, a timeout or a crash keeps the full reservation, because it may have been billed.

### 3.1 Fallback

`AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS` (for example `openai` behind `anthropic`) are tried, in order, when the provider
before them fails: a timeout, a rate limit (HTTP 429), another HTTP error, a missing key, a refusal, an answer cut off
or malformed, or web search unavailable. A provider that answers, even with nothing found, ends the attempt.

A fallback runs **only if the budget covers every call that could happen**: at the claim, the attempt reserves one
per-call limit per provider, but only as far as both the job's remaining limit and the day's remaining budget allow. If
only the primary fits, the attempt has no fallback; if not even that fits, the job waits for the next day (daily) or
fails ("job cost limit reached").

Each call's reservation is replaced by its cost as soon as the call returns. That update and the check behind it run in
the same transaction, locked on the day's budget row like the claim. A fallback is sent only if the job's total and the
day's total, with the earlier calls' actual costs and the fallback's own reservation included, still fit their limits.
So when an earlier call cost more than its reservation (its estimate came out above the per-call limit), the fallback
is skipped, recorded as `not_sent` ("skipped: budget used by the calls before") and costs nothing. A concurrent job's
overrun counts the same way. The calls already made keep their audit records and their costs.

These limits are enforced on **estimates** (or, for an unknown cost, on the per-call limit). An estimate can be below
the bill (§3.2), and a single call can cost more than its reservation before Django sees its usage. Only the spend
limits set in the providers' consoles are guaranteed billing limits. The SDKs do not retry by themselves (`max_retries=0`): a failed attempt is retried by
the job's own backoff, which keeps every request inside a reservation.

### 3.2 Cost estimate and reconciliation

No price is built in, and no response is assumed to carry complete billing. Each call's cost is **estimated** from the
usage the response reports and `AI_WEIGHT_RESEARCH_PRICES`, which the owner copies from the providers' current price
lists:

```json
{"claude-opus-5-5": {"input_per_mtok": "4", "output_per_mtok": "20", "search_per_1k": "10"},
 "gpt-6.2-astra":   {"input_per_mtok": "…", "output_per_mtok": "…", "search_per_1k": "…"}}
```

estimate = input tokens × input price + output tokens × output price + web searches × search price.

- **Claude:** input tokens include cache reads (priced as input, an overestimate); a cache write or a web fetch in the
  usage makes the cost unknown. Searches come from `usage.server_tool_use.web_search_requests`.
- **OpenAI:** input tokens include cached input (priced as full input, an overestimate). Every `web_search_call` item
  counts as a search, `open_page` and `find_in_page` included. Search content tokens that a model bills outside
  `usage`, or at a separate rate, are **not** in the estimate: put them into `search_per_1k` with a margin.
- An unpriced model, a missing `usage` or an unknown search count: the cost is **unknown** and the full per-call
  reservation stands.

Every call is recorded in `WeightResearchCall` (admin: inside each job): provider, model actually used, the provider's
request id, outcome, error, searches, the usage exactly as reported, citations, dropped URLs, reservation, estimate and
a note on what the estimate covers. **Reconcile monthly** against each provider's billing console or usage export, by
request id and day:
- adjust the prices in `.env` if the estimates drift;
- the daily budget is enforced on estimates, so keep a margin between it and any hard spend cap set at the provider;
- set a hard monthly spend limit in each provider's console as the final safety net.

A job whose next attempt could exceed its per-job limit fails ("job cost limit reached") without a call. A run that
dies mid-job leaves the job `running`. After 30 minutes the next run counts that as a failed attempt ("interrupted"),
keeps its reservation once, and queues it again with backoff. The day's total is under **AI research spending** in the
admin.

## 4. Providers (LPK-248)

`PROVIDERS` maps a provider name to a dotted path of a function `query -> Answer` that raises `ResearchError` when
nothing was answered (`sent=False`: no request left the server). An `Answer` holds the observations plus what the audit
records; its `error` marks an answer that was billed but is unusable.

**`anthropic` and `openai`** (`parts/ai_providers.py`) use the official SDKs (`anthropic`, `openai`, pinned in
`requirements.txt`), with an explicit key, base URL and timeout, and `max_retries=0`.

| | Claude (`anthropic`) | OpenAI (`openai`) |
|---|---|---|
| API | Messages API | Responses API, `store=False` |
| Search | server tool `web_search_20260209`, `max_uses` | hosted tool `web_search`, `max_tool_calls`, sources included |
| Answer | strict client tool `report_weights` | strict function tool `report_weights` |
| Citations kept | `web_search_result_location` (URL, title, cited text) | `url_citation` (URL, title, cited text) |
| Search location | approximate, US | approximate, US |

Both get the same fixed instructions and one user message with the part number, manufacturer and supplier part name.
The model must call `report_weights` once with every page it found (URL as the search returned it, title, part number
and manufacturer as written, value and unit as written, `shipping` / `net` / empty, a verbatim excerpt).

Nothing in the answer is trusted:
1. an observation whose URL is not among the search results or citations of that same response is **dropped** and
   counted (`unsourced`); it lowers the score like a misquoted page and marks the evidence as conflicting;
2. the rest go through the same claim checks and **independent page verification** as every provider (§2);
3. a refusal, an answer cut off at the token limit, a server-side search that did not finish (`pause_turn`), a missing
   or malformed `report_weights` call, or a search that only returned errors makes the call "unusable", and the next
   provider is tried.

Failures (timeouts, HTTP 429 and other errors, connection errors) are recorded by type and status only: the provider's
error body is never stored or logged, since it may echo the request.

**`http`** is the provider-neutral HTTPS service from LPK-247:

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

All providers share the claim checks, page verification, scoring, budget and approval described here. The job records
which provider answered. A structured catalog (RockAuto, Inter Cars, TecDoc and others) is out of scope; it could become
a provider the same way.

## 5. Operator workflow (Django Admin, section "Parts")

**AI weight research** (`/admin/parts/weightresearchjob/`): one row per job, with status, proposed weight, evidence
score, conflicting evidence, measurement, attempts, cost, provider and last error. Open a row to see the explanation, the
number of customers waiting, every provider call (§3.2), and every source with:

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

1. Deploy as usual (`docs/staging-runbook.md` §5). `pip install -r requirements.txt` adds the two SDKs. Migration
   `parts.0011_ai_research_calls` only creates one table.
2. The feature stays off. **API keys** (owner, once per environment):
   - Claude: in the Claude Console, create a workspace for Lion Parts research, a key in that workspace, and a monthly
     spend limit on the workspace. Check that web search is enabled for the organization.
   - OpenAI: in the OpenAI platform, create a project for Lion Parts research, a key restricted to that project, and a
     project budget.
   - One key per environment (staging, production). Never reuse a developer's key or the shell's `ANTHROPIC_API_KEY` /
     `OPENAI_API_KEY`: the settings read only the `AI_WEIGHT_RESEARCH_*` names.
   - Put keys only into the server's `.env` (mode 600, owned by the app user), never into Git, tickets or chat.
     Rotate by replacing the value and restarting; revoke the old key in the console.
3. **Staging first.** In the staging `.env`:
   - `AI_WEIGHT_RESEARCH_ENABLED=True`, `AI_WEIGHT_RESEARCH_PROVIDER=anthropic` (or `openai`; it must be set, the
     default is `http`), optionally
     `AI_WEIGHT_RESEARCH_FALLBACK_PROVIDERS=openai`;
   - the key(s), and `AI_WEIGHT_RESEARCH_PRICES` from the current price lists;
   - a small budget at first, for example `AI_WEIGHT_RESEARCH_DAILY_BUDGET_USD=2.00` and `_JOBS_PER_RUN=2`.

   Then restart, and run `manage.py run_weight_research --limit 1` by hand before adding cron. Staging uses the real
   providers, so it spends real money: only after the owner's approval.
4. The server must be able to reach public https sites (outbound port 443): the two APIs and the pages it checks. It
   needs no other access.
4. Add the cron line from `deploy/staging/crontab.example` (every 15 minutes).
5. Watch these, and the output of `run_weight_research`:
   - `/admin/parts/weightresearchjob/`
   - the explanations
   - **AI research spending**

**Rollback:**
- **Feature:** set `AI_WEIGHT_RESEARCH_ENABLED=False` and restart. Nothing is queued or run, and pending AI weights stay
  unused. To leave one provider, change `AI_WEIGHT_RESEARCH_PROVIDER` / `_FALLBACK_PROVIDERS` and restart.
- **Keys:** revoke them in the providers' consoles; the app fails no request because of it (calls fail and are retried,
  or the feature is off).
- **Code:** LPK-247 code ignores the `WeightResearchCall` table and knows only the `http` provider (the default in both
  versions): set `AI_WEIGHT_RESEARCH_PROVIDER=http` (or remove it) and clear `_FALLBACK_PROVIDERS` before rolling back
  with the feature on.
- **Schema** (only if needed, and after a backup): `manage.py migrate parts 0010` drops the call audit;
  `manage.py migrate parts 0009` drops all research tables. AI weights already recorded stay in `PartWeight`.

## 8. Decisions for the owner

1. **Providers.** Claude and OpenAI are built (LPK-248) and tested only against mocked responses; no paid call has
   been made. Choose the primary, the fallback, and the model ids (defaults: `claude-opus-5-5`, `gpt-6.2-astra`).
   Activation and billing are approved separately; run the benchmark (§9) first.
2. **Allowlists** are empty by default. Which catalogs (`trusted_sites`) and which official manufacturer domains
   (`manufacturer_sites`) earn the +0.10 is a business decision, and each entry needs review.
3. **Thresholds** in `Rules` and the limits in §3. They are deliberately conservative:
   - 2 verified sites;
   - score at least 0.60;
   - $0.50 per attempt, $1.50 per job, $5 per day.
4. **Shipping weight wins a tie with net weight**, because the courier fee is per shipped kg. A part with only
   net-weight evidence gets a net weight, marked as such.
5. **Some pages never verify**, which may leave some parts at "needs review". This is the intended conservative
   outcome. They are pages that:
   - need JavaScript;
   - are PDFs;
   - are disallowed by robots.txt;
   - are unreachable when robots.txt can't be read;
   - don't tie the weight to the part in their HTML structure, for example a product page whose `<h1>` lacks the part
     number and has no labelled part number in its specification block.

   Each cited page is fetched once per job and identified as `LionPartsWeightCheck/1.0`. Limitations:
   - `Crawl-delay` is not applied. At most a few pages per site are fetched per job.
   - Terms of service, login walls and `X-Robots-Tag` / meta robots indexing directives are not interpreted. Logins and
     refusals end as "blocked".
6. **Jira LPK-247 was not readable from the build environment.** This was built from the owner's written briefs.
   Compare it with the Jira acceptance criteria before merging.

## 9. Benchmark (prepared, not run)

`backend/parts/data/weight_benchmark_us.csv` lists 62 distinct US-market part numbers from 30 manufacturers and
aftermarket brands: oil, air and cabin filters, spark plugs, a sensor, brake pads, belts, wiper blades, a hub, a bearing
set and a shock absorber. The list was compiled from widely sold catalog numbers; check that each row exists before a
paid run. The `reference_kg` column is empty: fill it with measured weights (or AMT weights) to compare against.

```
manage.py benchmark_weight_research                                  # dry run: prompt, call count, worst case; sends nothing
manage.py benchmark_weight_research --providers anthropic,openai --run --confirm-spend 62 > results.csv
```

- **Dry run is the default.** It needs no keys and sends nothing.
- `--run` needs `--confirm-spend` of at least the worst case (parts × providers × per-call limit). It stops before a call
  that could exceed the confirmed amount, and counts unknown costs at the per-call limit.
- It asks each provider separately, with no fallback, and checks every answer exactly as a job does: page
  verification, robots.txt, scoring.
- It writes one CSV row per part and provider: model, error, searches, estimated cost, observations, dropped URLs,
  verified pages, measurement, proposal, evidence score and the difference to `reference_kg`.
- It saves nothing to the database and is **outside the daily research budget**: `--confirm-spend` is its only limit.
- Compare providers on verified proposals per part, agreement with the reference weights, and cost per verified
  proposal, then reconcile the estimates with the providers' billing (§3.2).
