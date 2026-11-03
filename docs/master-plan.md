# Lion Parts backend: master plan

Version-controlled plan for building the whole v1 backend. Status per story lives in
[`progress.md`](progress.md); the legacy-test mapping lives in [`legacy-test-map.md`](legacy-test-map.md).

**Sources (not in Git, kept by the project owner):** `AI_IMPLEMENTATION_GUIDE.md` (the "Guide", G§x,
source of truth for contracts), `JIRA_BACKLOG.md` (76 stories, 13 epics, 6 milestones),
`backend-review.md` (historical audit), `CLAUDE.md` (agent rules). This file restates only what is
needed to plan; it never overrides the Guide.

Last updated: 2026-11-03 (M1 checkpoint review applied).

---

## 1. Where we are

| Item | State |
|---|---|
| Epic LP-BE-1 (101–107) + LP-BE-1301 | Done on `feature/lpk-83-platform-foundation` (PR #58, CI green on PostgreSQL 16, not merged) |
| Demo outbox hardening | Done, `09338f1`: staff only + boot guard (decision D1) |
| M1 | Epic 2 (201–213) + LP-BE-1302, 1303 done on `feature/m1-accounts-auth` (branched from the PR #58 head); approved for push and CI, not for merge |
| Next milestone | **M2**, not started (waits for the owner) |
| Legacy API | Unrouted since LP-BE-103; 126 tests skipped and mapped (`legacy-test-map.md`) |

## 2. Milestones and story order

† LP-BE-1102, 1104 and 1105 are **not assigned to a milestone in the backlog**; they stay in the plan and
are built where their dependencies allow (sprint plan: 1104 in sprint 6, 1102/1105 in sprint 7). All 76
stories are planned; `progress.md` has one row each.

Order inside each milestone is a valid topological order of the backlog's "Depends on" fields
(checked: no cycles, every dependency is in the same or an earlier milestone, except the items in §3).

| Milestone | Stories, in build order | Frontend can build |
|---|---|---|
| **M1** | 201 → 1303 → 202 → 203 → 1302 → 207 → 204 → 206 → 205 → 208 → 209 → 210 → 211 → 212 → 213 | registration, login, verification, reset, profile, language |
| **M2** | 301 → 304 → 302 → 303 → 401 → 1101 → 402 → 403 → 406 → 404 → 405 → 407 → 501 → 502 → 503 → 504 → 505 → 506 | search, history, weight/quote, cart, currency |
| **M3** | 601 → 602 → 604 → 701 → 702 → 603 → 703 → 704 → 705 → 706 → 1304 | checkout, payment result, invoice (orders freeze currency, rate and amounts: D16) |
| **M4** | 801 → 802 → 803 → 804 → 805 → 806 → 1103 → 1104† → 807 | orders, tracking, decisions, cancel, courier fee |
| **M5** | 901 → 1305 → 902 → 903 → 904 → 1001 → 1002 → 1102† → 1105† | notifications, badges, magic links, support |
| **M6** | 1201 → 1202 → 1203 → 1204 → 1205 → 1306 | final contract, ka/ru QA, production readiness |

### Dependency graph (all 76 stories)

```
101 ─┬─ 1301 ─┬─ 102 ─┬─ 103 ─┐            1302 ─┐
     │        │       └─ 105  │                  │
     │        ├─ 1302         ├─ 206 ─┬─ 208     ├─ 301 ─┬─ 304 ─┐
     │        └─ 1303 ─ 207 ──┤       ├─ 210 ─ 213 ─ 1102│       ├─ 303 ─┬─ 402 ─ 403 ─┬─ 404 (needs 406)
     ├─ 104 ─────────┐        │       └─ (506)           └─ 302 ─┘       │             ├─ 405, 406, 407 (needs 205)
     ├─ 106, 107     │        ├─ 209, 211                401 (201, 301) ─┤             └─ 502 (needs 406)
     └─ 201 ─ 202 ─ 203 ─ 204 ─ 205 (needs 207)          1101 (301, 401) └─ 501 ─ 502 ─ 503 ─┬─ 504, 505 (402)
                     └─ 212                                                                  └─ 506 (205, 206)
301 + 501 ─ 601 ─┬─ 602 (503) ─ 603 (701)        601 ─ 1304 (1301) ─┐
                 ├─ 604 ─────────────── 705      601 ─ 701 ─ 702 ─┬─ 703 ─ 706, 704, 705 (604)
                 └─ 901 ─ 1305 (1303) ─ 902 (207, 203)            └─ 801 (601, 1304) ─ 802, 803, 805, 806, 804 (702)
803 + 805 + 106 ─ 807        801 + 705 ─ 1103        406 + 303 ─ 1104        705 + 902 ─ 1105
902 ─┬─ 903 (1103, 1104, 702) ─ 1002 (1001)      902 + 104 ─ 1001
     └─ 904
all functional epics ─ 1201 ─┬─ 1202 ─ 1204 ─ 1205 (1203)
                             ├─ 1203
                             └─ 1306
```

## 3. Graph validation findings

| # | Finding | Handling |
|---|---|---|
| G1 | The milestone table leaves out **1102, 1104, 1105**. | Follow the sprint plan: 1104 in M4, 1102 and 1105 in M5. |
| G2 | **1304** is listed in M4, but the sprint plan puts it in sprint 5 (M3), and 801 (M4) depends on it. | Build it at the end of M3 so M4 can start with 801. |
| G3 | **213** "open-orders check becomes real after 601" (M3). | In M1, check the current `Order.customer` statuses (not `completed`/`cancelled`); re-test after 601. |
| G4 | **1302** includes the `SiteSettings` cities/districts editor, but `SiteSettings` comes with 301 (M2). | M1 delivers `tr()`, the widget and the form field; the cities editor ships with 301/1101. |
| G5 | **205/206** call `attach_guest_data()`, filled later by 407, 506 (M2) and 1001 (M5). | M1 ships it as a documented no-op hook. |
| G6 | **206** `/me` returns `cart_count`/`badges`, filled by 503 (M2) and 904 (M5). | `0` / `null` until then, as the backlog says. |
| G7 | **903** needs admin stories 1103/1104 (M4). **902** needs 203/207 (M1). | No action: both are earlier milestones. |
| G8 | **1306** and **1201** depend on "all functional epics". | Last; M6. |

## 4. Conflicts and decisions log

| # | Decision | Status |
|---|---|---|
| **D1** | `GET /api/demo/outbox/` requires a **staff** session (`IsAdminUser`). Reason: email bodies hold verification and password-reset links. Settings also refuse `ENABLE_DEMO_ENDPOINTS` with `SENDER_GE_ENABLED=True` or a non-`fake` `PAYMENT_PROVIDER`. Commit `09338f1`. | **Accepted (2026-11-03).** Local Guide updated: §4.10, §8.10 (Auth column), §12 note, §16 change log. |
| D2 | Django 6.1 forbids the `EMAIL_*` settings next to `MAILERS`. The G§12 env names are read into a `MAILERS` dict. | **Accepted (2026-11-03).** Implemented (LP-BE-101); local Guide §5.2 and §16 updated. |
| **D3** | No demo endpoint may change an order or a payment without authentication. Operator-side demo mutations (G§8.10 item status/action, courier fee, quote ready) require a **staff** session. The customer-facing `POST /api/payments/fake/<id>/complete/` requires the **logged-in owner** of the order (foreign → 404) and a **pending** payment (else 409). | **Accepted (2026-11-03).** Local Guide §4.10/§8.8/§8.10/§16 updated. Built in LP-BE-704 (fake complete) and LP-BE-807 (demo order routes); each needs anonymous, wrong-customer, non-staff and wrong-state tests. |
| D4 | LP-BE-201 deletes `CustomerSession` and `customer_sessions.py`, but the unrouted legacy views in `parts/`, `orders/`, `cart/` and the live admin still import from them until 1201. Plan: delete the legacy **accounts** views, serializers, URLs and their tests (all replaced inside M1). Keep `get_customer_for_session()` as a shim that returns `None` (legacy `session_id`s identify nobody) until 1201. Update live callers (admin, AMT/pricing code) to the new `Customer` fields. | Chosen for M1 (follows the backlog's "keep the imports compiling until then"). |
| D5 | Fresh `accounts/0001_initial` (G§6, LP-BE-201); orders 0014/0016 point at it. A database migrated before `befe29b` is **not upgradable** and the mismatch is silent: see [`migration-history.md`](migration-history.md). | Done in M1. **Owner: do not recreate, reset or migrate `lion_parts_v2`** (2026-11-03). PostgreSQL is tested only on ephemeral CI databases. |
| D6 | Until LP-BE-303, the legacy price code needs a markup. `TariffMarkup` replaces `CustomerTariff.markup_percent`: legacy code reads the tariff's **GEL** markup (seeded at 20). | M1. |
| D7 | Jira epic keys (verified by the owner, 2026-11-03): Epic 1 LPK-83, Epic 2 LPK-84, Epic 3 LPK-85, Epic 4 LPK-86, Epic 5 LPK-87, Epic 6 LPK-88, Epic 7 LPK-89, Epic 8 LPK-90, Epic 9 LPK-91, Epic 10 LPK-92, Epic 11 LPK-93, Epic 12 LPK-94, Epic 13 LPK-95. Story keys known: LP-BE-101..107 = LPK-96..102, LP-BE-1301 = LPK-166. | Commits use LP-BE keys; `progress.md` shows the epic key for each story and the story key where known (never guessed). |
| D8 | The legacy `send-code` returned `demo_code` whenever `SENDER_GE_ENABLED=False`. | LP-BE-203 uses the Guide rule only: `SMS_DEMO_MODE and (DEBUG or ENABLE_DEMO_ENDPOINTS)`. |
| **D10** | Fail-closed `DEPLOY_ENV` (`production` / `staging` / `development` / `test`; unset = production). Production refuses `DEBUG`, `ENABLE_DEMO_ENDPOINTS`, `SMS_DEMO_MODE`, `PARTS_PROVIDER=demo` and console/locmem/dummy/filebased mail backends. CI and test settings use `test`. | **Accepted and implemented (2026-11-03)**, `52ab640`, tests in `config/tests.py`. |
| D11 | Disabled SMS delivery is never silent: `send_sms` logs a warning when `SENDER_GE_ENABLED=False`; OTP sending returns `502 sms_failed` when delivery is off and demo codes are off. Expired registration drafts are deleted when found. | **Accepted (M1 audit fixes)**, `2c7781b`. |
| D12 | M2 implementation choices (Guide-compatible, recorded for review): (a) the quote "price ready" admin action prices through the engine already in LP-BE-401, because the old action depended on the replaced model (LP-BE-1104 adds notify + per-row errors); (b) demo offers use `DEMO-{n}-{part}` ids and a fourth demo row without weight so staging can exercise checking/weight/quote flows; (c) `image_urls` and `customer_notice` are added when presenting offers (request-dependent), not stored; (d) a search log with offers but none priced shows history status `checking`; (e) history `purchased` uses the legacy `OrderItem` until LP-BE-601; (f) `GET /api/cart/` without a cart returns an empty cart without creating rows; (g) cart refresh keeps customer/operator weights; (h) a rate outage during the login merge doesn't block login (lines listed under `problems` with `rate_unavailable`); (i) supplier-calling endpoints (search, calculate-price, cart refresh) share the `search` throttle. | Implemented in M2; open for owner review. |
| **D13** | Operator-approved prepared quotes keep the approved price (owner decision, M2 review). Implemented: adding copies the approved snapshot unchanged; the login merge doesn't reprice prepared-quote lines; a quote priced in another currency than the cart is refused (`400`, `fields.quote_request_id[0].code = "not_allowed"`, an existing G§9 field code) instead of being converted. **Open contract questions (not implemented, nothing invented):** (1) the Guide has no validity period for a prepared quote: a ready quote can be added at its approved price at any time (each cart line still expires after `price_valid_minutes`). Proposal: a configurable quote validity (e.g. `SiteSettings.quote_valid_days`) counted from `price_ready_at`; after it, adding returns `409 offer_expired` and the operator re-approves by running "price ready" again (that already recalculates). (2) Two explicit customer actions still recalculate a prepared-quote line: the cart currency switch (LP-BE-504) and the line refresh (LP-BE-505, supplier price with the operator weight). Options: keep (explicit actions, shown as `price_changed`), or refuse them for prepared-quote lines with `not_allowed`. | Fix implemented; (1) and (2) await the owner's decision. |
| **D14** | Pricing configuration (owner decision, M2 review): the default tariff keeps a markup for every active currency (admin-enforced); a missing markup is a logged configuration error (500), never a zero markup. Offers priced for someone else (guest search before login) are recalculated for the customer when added (`priced_for` marker on search logs). | Implemented (`acdbf76`, `409c538`). |
| **D15** | **Canonical USD pricing and currency mode (owner-approved 2026-11-03; supersedes D13/D14 where they conflict).** USD is the canonical price (one tariff markup, one VAT, USD fixed discounts, applied once); GEL = round2(USD x current NBG rate). `SiteSettings.multi_currency_enabled` (default off = GEL only, switching refused; on = USD default, USD or GEL), `vat_percent`, `price_valid_business_days` (10), `public_holidays`; `/api/meta/` exposes the mode. Uncommitted GEL amounts (search, history, quotes, cart) follow the current rate; canonical USD never does; no supplier call because of FX; `fetch_nbg_rates` re-converts GEL carts (updated / unchanged / unavailable); no current rate -> `502 rate_unavailable` for GEL, never an outdated amount. Validity: 10 Georgian business days (Asia/Tbilisi, Mon-Fri minus holidays, expired at 00:00 after the last day), also for operator-approved quotes; FX never extends it; expired quote -> `409 offer_expired` until the operator re-approves. Prepared quotes keep their USD base; GEL is recalculated (the D13 `not_allowed` rule is withdrawn; D13's validity question is answered by the 10 business days). Currency switch converts from USD (no `price_changed`). Compatibility: `TariffMarkup` -> `CustomerTariff.markup_percent` (USD value migrated), `CurrencySettings` removed, fixed discounts are USD, GEL may differ by a cent from the old per-currency formula. Guide §7.17 (local) is authoritative. | Implemented in M2 (`bd7f995`, `f8a0664`). |
| **D16** | **Orders are immutable (M3 contract).** At checkout (LP-BE-601/603) the order freezes its currency, the exchange rate used (1 for USD) and, per item, the canonical USD snapshot (supplier price, weight, shipping rate, markup, VAT, discount rule/amount) plus the final amounts in the order currency (list, discount, unit, line total), and the order totals incl. upfront/balance. Later NBG changes never modify an order, invoice (LP-BE-604/705) or payment amount (LP-BE-702/703), including orders awaiting payment; payments charge the frozen amounts in the order currency. Multi-currency off: order, payment and invoice currency are GEL. | Contract for M3; each M3 story needs a regression test "rate change after checkout changes nothing". |
| **D17** | **Order admin is read-only until M4 (owner decision, M3).** M3 ships the new order schema, a read-only order/item/event/support admin, the payment inline and actions (LP-BE-705: payment received, mark refunded, create balance invoice; they require `payments.change_payment` and go through `mark_payment` / `request_payment`) and the invoice (LP-BE-604). Item status, change requests and courier-fee tools return with LP-BE-1103 (M4). | Implemented in M3. |
| D18 | M3 implementation choices (Guide-compatible, recorded for review): (a) `POST /api/orders/<n>/payments/` while the order is still `payment_pending` creates an `initial` payment for `amount_due_now` (the retry that moves the order to processing); afterwards a `balance` payment for `balance_due`; a pending invoice/manual payment (any age) or a pending card payment younger than 30 min -> `409 payment_in_progress` (audit fix 1: an operator unblocks a stale invoice with the admin action "Cancel invoice"; "Payment received" confirms exactly the chosen payment and is refused when the order no longer owes its amount); (b) only `FakeProvider` exists: no real provider (Flitt) has been called, so its USD/GEL support, amount/currency echo in callbacks and refund behaviour are **unverified** and must be checked when its adapter is written (the adapter must verify signature, amount and currency); (c) the demo fake-complete endpoint answers `409 nothing_to_pay` for a payment that is not pending; (b2) `DEPLOY_ENV=production` refuses `PAYMENT_PROVIDER=fake` (audit fix 3), so production cannot start until a real adapter exists; an invoice-only production would need an explicit card-off switch (not built); (d) `reconcile_payments` is meant to run every 5 minutes (scheduling is an owner/ops action); (e) the callback log carries provider + traceback (there is no request-id infrastructure yet); (f) the fresh `orders`/`payments` migrations replace the legacy order tables, so an existing database with legacy orders cannot be migrated in place (owner action before any deployment). | Implemented in M3; open for owner review. |
| **D19** | **Operator cancellation authority (owner decision, M4).** Operators may cancel any non-terminal order (payment_pending / processing / action_required) or individual item, including already-purchased items; a reason is mandatory and every cancellation is an audited event (actor, reason, old/new status). Customers keep the G§7.13 rule (nothing purchased). No automatic refunds: open payments expire, collected money stays recorded and shows as `refund_pending`; refunds go through the manual admin workflow and can never exceed what is owed back or what was collected. Item snapshots (supplier USD price, markup, VAT) and payments are never deleted. Before the first payment, cancelling an item re-sets `amount_due_now` for the new total (split terms frozen at checkout) and expires the open payments (audit fix `d8b9d7d`). Accepted price/weight/alternative changes update the order-currency amounts; the checkout USD snapshot stays as the record of the original price, and every change is in the item events (old/new). | Implemented in M4 (LP-BE-804, LP-BE-1103). |
| **D20** | **Manual refunds are recorded, attributable and capped (owner requirements, M4).** `payments.Refund` records a (partial) refund of a paid payment (amount, reason, operator, time); `amount_paid` = paid payments minus their refunds (G§7.8 "minus refunded"). The order admin action "Record refund of the amount owed" records exactly `-balance_due` (e.g. an item or the order was cancelled), spread over the latest paid payments, never more than any payment still holds; with nothing owed it records nothing (idempotent). "Mark refunded" (whole payment) is refused unless the whole remaining amount is owed back. Item cancellation therefore owes back only what was collected beyond the new total (a split order may still owe money). Locks: paid payments, then the order. | Implemented in M4 (LP-BE-1103). |
| **D21** | **Courier fee on USD orders (owner decision, M4).** Fees are proposed in GEL. Each proposal is a versioned `CourierFeeProposal` frozen when created: GEL fee, for USD orders the NBG rate of that moment, the amount in the order currency (`round2`, half-up), the proposer and time. The customer accepts exactly that amount; later FX changes never alter it. A changed fee is a new proposal (the pending one becomes `superseded`; accepted ones are never edited). Accepting sets `courier_fee` (a separate money line); the order's checkout rate and item prices never change. GEL orders use the GEL fee as is. This corrects G§7.14's `proposed_gel / order.exchange_rate` (the rate of a USD order is 1). | Implemented in M4 (LP-BE-805). |
| **D22** | **Terminal cancellation (owner request, M4 review).** When the last active item is cancelled (operator status change or the customer's cancel decision), the order is cancelled in the same transaction through the same step as a whole-order cancellation (`close_cancelled_order`): open payments expire, a pending courier proposal is cancelled, a confirmed courier fee is released (nothing will be delivered; the accepted proposal stays in the history), collected money stays recorded so the order owes back exactly what was collected (`refund_pending`, refunded manually and capped per D20). An internal `order_cancelled` event keeps the before/after state (status, courier fee, amount paid, expired payments, reason, actor). Repeats are refused (closed order); parallel cancellations of the last items close the order once (row locks: open payments, order, item). | Implemented in M4 review. |
| **D23** | **Public notification links (owner decision, M5).** `GET /api/public/n/<token>/` returns only `event_type`, the localized title, `order_number`, `item_part_number`, `target` and `requires_login`; `message` is always empty (operator-written text needs login). Never names, phone, email, VIN, prices/amounts, addresses or billing. Tokens are uuid4 (122 random bits from the OS CSPRNG), expire 30 days after creation; unknown, malformed and expired tokens give the same 404. Throttled per IP. Tokens are masked in Django's request logs (web-server access logs are server configuration, out of scope). Private details only through the owner-scoped endpoints after login. | Implemented in M5 (LP-BE-904). |
| **D24** | **SMS/email content (owner decision, M5).** Customer notifications contain the event text, the order number / part number and the link only: no amounts, no operator-written text, no names or addresses. Templates get a restricted context (primitive values, no model objects) so an edited template can't reach other data. Guest ticket replies are the exception: the guest has no account, so the operator's reply goes to the contact they gave. Anonymizing a customer deletes their notifications and support tickets. | Implemented in M5. |
| D9 | Legacy tests replaced by a story are rewritten for the new contract and the legacy test is removed **in the same commit** as the code it tested (G§8.11: "delete together with their views, serializers and tests"). Scenarios are tracked in `legacy-test-map.md`; a legacy test is never deleted without its replacement. | Policy for all milestones. |

## 5. Security risk register

| # | Risk | Mitigation / story |
|---|---|---|
| S1 | Demo endpoints leak links or let anyone change orders and payments | D1 done; D3 accepted (built in 704/807); D10 production guard |
| S2 | Production started with demo settings | D10 (fail-closed `DEPLOY_ENV`) + the D1 real-SMS/payments guard |
| S3 | OTP brute force | 6 digits, hashed, 5 attempts, `select_for_update`, attempts counted **before** the check, `otp_verify` throttle (203) |
| S4 | SMS bombing / cost | `otp_send_phone` + `otp_send_ip` throttles, 60 s cooldown (203/204/212) |
| S5 | Account enumeration | Login and password reset: identical bodies (206/209). Registration returns `taken` by contract (G§8.2); limited by the `register` throttle. **Accepted by the contract.** Residual: password-reset response time is longer for known emails (synchronous SMTP); bounded by the `password_reset` throttle; closing it needs async sending (G§7.11 shortcut). |
| S6 | Session fixation / stale sessions | `login()` rotates the key (205/206 test); password reset and change rotate the auth hash (209/210); deletion removes sessions (213) |
| S7 | Email-link replay | `signing` with salt and `max_age`; the token embeds the email, so it dies after an email change (208/211) |
| S8 | Secrets in logs or DB | Never log OTP codes, API keys or tokens; drop `apikey` from stored SMS payloads (203) |
| S9 | Staff sessions on the customer API | `IsCustomer` (done, 103); login refuses staff (206) |
| S10 | Ownership leaks | Every owned query goes through the customer; foreign → 404 (M2–M4 tests) |
| S11 | Client-supplied money or state | Cart and checkout accept references only (502/603); server-side `Decimal` (303) |
| S12 | PII in public magic links | `/q/` and `/n/` payloads without PII (406/904) |
| S13 | `X-Forwarded-For` spoofing bypasses IP throttles | `NUM_PROXIES` from the environment (done, 104); verify the staging proxy config (1204) |
| S14 | Dead legacy code | Unrouted (test enforced) until 1201 removes it |

## 6. Production guard (D10, approved and implemented 2026-11-03)

The current guard (D1) catches production only when real SMS or real payments are on. A production
host with both off (e.g. before Flitt exists) could still start with demo endpoints.

**Proposal:** an explicit deployment environment variable, **failing closed**.

1. New env var `DEPLOY_ENV` with values `production` / `staging` / `development` / `test`. **When
   unset it means `production`**, so a forgotten value never enables demo behavior.
2. In `settings.py`, when `DEPLOY_ENV == "production"`, raise `ImproperlyConfigured` if any of these
   is true: `ENABLE_DEMO_ENDPOINTS`, `SMS_DEMO_MODE`, `DEBUG`, a `locmem`/`console` mail backend,
   `PARTS_PROVIDER="demo"`. Once the Flitt adapter exists, `PAYMENT_PROVIDER="fake"` joins the list.
3. Demo URLs are mounted only when `ENABLE_DEMO_ENDPOINTS and DEPLOY_ENV != "production"`, a second
   independent check.
4. `config/e2e_settings.py` and CI set `DEPLOY_ENV=test`; `.env.example` documents
   `DEPLOY_ENV=development`; the staging runbook (1204) sets `DEPLOY_ENV=staging`.
5. Tests: boot `production` + each demo flag → refused; `staging` + demo → starts; unset → behaves as
   production.

Implemented as proposed, except point 3: settings refuse to start in production with demo endpoints
on, so the URL-level second check would be dead code. **Operators: every existing `.env` must add
`DEPLOY_ENV`** (without it the app treats the host as production and refuses `DEBUG=True`).

## 7. Per-story definition of done (agent workflow)

1. Read the story, its Guide sections and the affected code; list the callers.
2. Implement every acceptance criterion; thin `@api_view` views, `services.py` logic, serializers for
   input, codes from G§9, English msgids with ka/ru translations in the `.po` files.
3. Tests: acceptance criteria, negative paths, security/ownership/money paths, and the mapped legacy
   scenarios (D9).
4. Run `check`, `makemigrations --check --dry-run`, `compilemessages`, and the full test suite
   (SQLite test settings locally, PostgreSQL in CI).
5. Ponytail review of the diff; fix significant findings.
6. One local commit per story (`LP-BE-xxx: …`, explicit file staging, no secrets).
7. Update `progress.md` with the commit and the evidence.
8. At the milestone end: full Ponytail audit, push the feature branch (no PR merge), stop for review.

## 8. Owner actions outside the agent's scope

- Done 2026-11-03: Guide updated locally for D1, D2, D3, D10 (the Guide stays untracked); D10 approved; epic keys provided (D7).
- Decide how the development database is replaced (D5, `migration-history.md`); the agent never touches it.
- Add `DEPLOY_ENV` to every existing `.env` (D10).
- Merge order: PR #58 (Epic 1) first, then the M1 branch.
