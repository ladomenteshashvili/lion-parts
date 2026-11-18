# Autonomous batch: contact page settings, `/api/meta/` contact, frontend readiness

Branch `feature/contact-api-and-frontend-audit`, from `main` at `f9a28f4`. Nothing was merged, deployed or restarted.

**Correction round.** The first commits (`3bce590`, `d8099db`) chose a contact schema without the Jira text and did
not match the fixed frontend contract. The owner supplied the Jira acceptance criteria for LPK-230, 231 and 232, and a
corrective commit on top of them brings the schema, API, translations, tests and this report in line. History was not
rewritten. The epic file named in the correction (`docs/JIRA_EPIC_LP-BE-14_contact.md`) does not exist in this
repository, so the owner's correction message is the specification used.

## A. Executive summary

- **LPK-230:** Site settings (the existing singleton, `pricing.SiteSettings`) has six optional fields for the contact
  page, edited in Django Admin under "Contact page" (right after "Invoice"): `contact_phone`, `contact_email`,
  `whatsapp_phone`, `viber_phone`, `facebook_url`, `instagram_url`. Empty means hidden. No business data was entered.
  Pickup point addresses and hours stay in Pickup points; the invoice seller's details are separate and stay internal.
- **LPK-231:** `GET /api/meta/` returns `contact` with exactly six keys (section D). Values are stored strings, the same
  in every language, `""` when empty. Existing keys are unchanged and there is no extra database query.
- **LPK-232:** Georgian and Russian texts for the fieldset, its description, the four new labels and the phone error.
- **LPK-224:** API compatibility matrix for the React v2 frontend (section G).
- **LPK-227:** not advanced (section B).

## B. Jira mapping

| Jira | Status | Evidence | Still open |
|---|---|---|---|
| LPK-230 Contact details in Site settings | Done against the supplied criteria | `pricing/models.py`, `pricing/admin.py`, `pricing/migrations/0003_site_contact_details.py`, `pricing/tests/test_contact.py` | Owner review |
| LPK-231 Contact data in `GET /api/meta/` | Done against the supplied criteria | `pricing/services.py`, `api/tests/contract.py`, `test_contract.py`, `test_meta.py`, `test_contact.py`; `docs/api-handoff.md`; Guide text in `docs/guide-update-contact.md` | Owner review |
| LPK-232 Georgian/Russian admin texts | Done | `locale/ka`, `locale/ru` (7 entries each), admin page test | A native-speaker read (as LPK-201) |
| LPK-224 React and backend gap matrix | Audit done, nothing built | Section G | The `/contact` page source (row 11) |
| LPK-227 Figma tokens and React map | Not advanced | `frontend/docs/DESIGN_TOKENS.md` and `FIGMA_MAPPING.md` on the React branch | Figma was not opened in this batch |
| LPK-233 Deployment and content | Out of scope | Section I | Owner |

## C. Files changed (against `main`)

| File | Change |
|---|---|
| `backend/pricing/models.py` | Six contact fields; shared phone validator; web-only URL validator |
| `backend/pricing/migrations/0003_site_contact_details.py` | Adds the six columns (branch only, regenerated in the correction) |
| `backend/pricing/admin.py` | "Contact page" fieldset after "Invoice" |
| `backend/pricing/services.py` | `contact` in the meta payload, from the settings row already read |
| `backend/locale/{ka,ru}/LC_MESSAGES/django.po` | 7 new translations each, appended; nothing else changed |
| `backend/pricing/tests/test_contact.py` | New: validation, API contract, languages, privacy, query count, admin, translations |
| `backend/pricing/tests/test_admin.py` | The admin form helper sends the six fields |
| `backend/api/tests/contract.py`, `test_contract.py`, `test_meta.py` | `contact` in `META`/`META_KEYS`; new `CONTACT` key set |
| `docs/api-handoff.md` | Meta row describes `contact` |
| `docs/guide-update-contact.md` | Exact replacement text for Guide §8.1 and the §16 log row |
| `docs/claude-autonomous-batch-report.md` | This report |

`AI_IMPLEMENTATION_GUIDE.md` is excluded from Git in this working copy (`.git/info/exclude`). It was updated locally,
and `docs/guide-update-contact.md` carries the same text for the master copy.

## D. API contract

`GET /api/meta/`, public, all existing keys unchanged, plus:

```json
"contact": {"phone": "", "email": "", "whatsapp": "", "viber": "", "facebook": "", "instagram": ""}
```

Filled in (placeholder values):

```json
"contact": {
  "phone": "+995 32 200 00 00",
  "email": "info@example.com",
  "whatsapp": "+995 555 12 34 56",
  "viber": "",
  "facebook": "https://www.facebook.com/example",
  "instagram": ""
}
```

| Key | Model field | Value |
|---|---|---|
| `phone` | `contact_phone` | Phone number as typed |
| `email` | `contact_email` | Email address |
| `whatsapp` | `whatsapp_phone` | Phone number; the frontend builds the WhatsApp link |
| `viber` | `viber_phone` | Phone number; the frontend builds the Viber link |
| `facebook` | `facebook_url` | `http`/`https` link |
| `instagram` | `instagram_url` | `http`/`https` link |

Rules:
- All six keys are always present; every value is a plain string; empty is `""`, never `null`.
- Values are not translated: identical for `Accept-Language` ka, en and ru. (`/api/meta/` takes its language from
  `Accept-Language`; `?lang=` is read only by the invoice page, and the tests check both give the same contact.)
- No address or working-hours keys: those are `pickup_points[].address` and `working_hours`.
- Phone numbers must match `^\+[0-9 ()-]{6,20}$` (the Jira pattern) and contain at least six digits, so that
  `+------` is refused. Error: "Enter a phone number like +995 555 12 34 56." For a link, keep `+` and digits only.
- Links: Django's URL check limited to `http` and `https`, so `javascript:`, `data:` and `ftp:` are refused.
- The invoice seller's phone, email, code and bank details are not published (tested).

## E. Database changes

One migration, `pricing.0003_site_contact_details`, on this branch only (not on `main`, never deployed). The
correction deleted the first version and regenerated it with `makemigrations`; `main` migrations are unchanged.
It adds six `varchar` columns (four of 30, `contact_email` 254, the two URLs 200) to the one-row
`pricing_sitesettings` table, each filled with `''`. No data migration, no change to existing columns. Reversible:
`migrate pricing 0002` drops them and loses only what an operator typed in.

## F. Test report

Environment: Python 3.14, Django 6.1, the project venv. No real AMT, SMS, payment or SMTP call.

| Run | Settings | Result |
|---|---|---|
| Full suite, disposable PostgreSQL 18 (`qa_regress` on 127.0.0.1:55432, checked before the run) | plain `config.settings` | 1,232 ran: 1,231 passed, 0 failed, 0 errors, 1 skipped (373 s) |
| Full suite, SQLite | `config.e2e_settings` | 1,232 ran: 1,159 passed, 0 failed, 0 errors, 73 skipped (43 s) |
| `check`, `makemigrations --check --dry-run`, `compilemessages` | plain `config.settings` | No issues; no changes detected; ka and ru compile |
| Contact, contract, meta, admin and i18n tests | `config.e2e_settings` | 65 tests, OK |

The one PostgreSQL skip is `api.tests.test_staging_kit.ProvisionFailureTests.test_a_failure_part_way_removes_nothing_and_points_to_the_runbook`,
skipped with "a real staging exists on this host": the provisioning test refuses to run beside the real staging on
this machine. It runs in CI.

The SQLite run skips the PostgreSQL-only tests (row locking and concurrency). GitHub CI has not run on this branch.

**Contract guard checked by mutation:** renaming `viber` in `pricing/services.py` made `test_contract` and four
contact tests fail; the code was then restored.

**Translations:** `makemessages -l ka -l ru` was run as a check. It found every string in the code translated, with
no fuzzy, empty or obsolete entry. Its rewrite of the files (about 340 lines of reordering and location comments in
unrelated entries) was not kept: recent batches append entries by hand, and the diff stays at the 7 new entries.

New and updated tests cover: six empty defaults; all six saved values returned as stored; identical contact in ka, en
and ru (by header and by `?lang=`); the exact key set, shared with the contract test; saving all fields empty in the
admin; valid international numbers; `555 12 34 56`, `call me`, `+995<script>`, `tel:` and others refused on all three
phone fields; `javascript:alert(1)`, `data:`, `ftp:` refused on both URL fields; field errors in the admin; invoice
seller data not published; the query count (5, as before); the fieldset's position and fields; Georgian and Russian
texts on the admin page.

## G. Frontend gap matrix (LPK-224)

**Sources.** The React v2 app on the local, unpushed branch `feature/react-v2-figma-integration` (`3a8612d`):
`frontend/src/api/endpoints.ts`, `client.ts`, `types.ts`, `App.tsx`, the pages, and its own `frontend/docs/BACKEND_GAPS.md`.
The `frontend/` on `main` is the old app and was not audited.

**Confirmed here.** All 51 calls in `endpoints.ts`, plus the CSRF call in `client.ts`, resolve to an existing backend
route on this branch (checked with Django's URL resolver). Request and response shapes were checked only by reading and
by the React branch's mock contract tests. **No flow has been run against a real backend.**

Auth: P = public, C = signed-in customer (session cookie plus `X-CSRFToken` on writes), G = guest or customer
(session). Gap numbers refer to the React branch's `frontend/docs/BACKEND_GAPS.md`.

| # | Flow | Frontend (React v2) | Backend endpoint, method and request | Expected response | Auth | Status | Missing work | Priority |
|---|---|---|---|---|---|---|---|---|
| 1 | Registration and sign-in | `RegisterPage.tsx`, `AuthPages.tsx`, `components/verification.tsx`, `state/session.tsx` | `GET /api/auth/csrf/`, `GET /api/auth/me/`; `POST /api/auth/register/` (account type, names, phone, email, password, consents), `register/resend-code/`, `register/verify-phone/ {code}`, `login/ {email, password}`, `logout/`, `password-reset/ {email}`, `password-reset/confirm/ {uid, token, new_password}`, `email/send-verification/`, `email/verify/ {token}` | `me`: `{authenticated, customer, cart_count, badges}`; OTP: masked phone and timers; errors `{code, detail, fields}` | P; logout and email sending C | Compatible by reading | Reloading the SMS step restarts registration (Q12). The reset page cannot show the email (the link has only `uid` and `token`). The real-SMS test (`TESTING.md` R1) awaits owner approval | P2 |
| 2 | Customer profile | `ProfilePage.tsx` | `PATCH /api/profile/ {marketing_opt_in?, preferred_language?}`, `POST /api/profile/password/`, `email/` + `email/confirm/`, `phone/` + `phone/verify/`, `DELETE /api/profile/ {current_password}` | Customer, or `204` on delete | C | Compatible | Name, personal ID and company details have no update endpoint (gap 7). A customer cannot re-verify their own unverified number (gap 13) | P3 |
| 3 | Part search (VIN per item) | `SearchPage.tsx` | `POST /api/parts/search/ {part_number, currency?}`; VIN later via `PATCH /api/cart/items/<id>/ {vin}` or `POST /api/cart/apply-vin/ {vin}` | `{search_id, part_number, vin, currency, exchange_rate, price_valid_until, not_found, offers[]}`; `502 supplier_unavailable` when the supplier fails | G | Compatible | Every currency switch re-runs the search (one supplier lookup and one count against the search limit) | P3 |
| 4 | Results and missing weight | `components/parts.tsx`, `SearchPage.tsx` | Offer `requires_weight`, `weight_source`; `POST /api/parts/calculate-price/ {search_id, offer_id, weight_kg, weight_consent}` when the tariff allows weight entry | `{search_id, price_valid_until, offer}` | C | Compatible | No guest SMS verification from search (gap 14): the UI shows a sign-in link instead | P3 |
| 5 | Manual quote requests | `SearchPage.tsx`, `HistoryPage.tsx`, `PublicLinks.tsx` | `POST /api/parts/quote-requests/ {request_type: manual_search, part_number, vin, comment}` or `{request_type: weight_price, search_id, offer_id}`; `GET /api/public/q/<uuid>/`; `GET/DELETE /api/parts/history/`, `POST history/seen/` | QuoteRequest (`201`, or `200` for an open duplicate); paginated history | C; public link P | Compatible | History can only be cleared all at once (gap 2) | P3 |
| 6 | Cart | `CartPage.tsx` | `GET/PATCH /api/cart/ {currency}`, `POST /api/cart/items/ {source, …, quantity}`, `PATCH/DELETE /api/cart/items/<id>/`, `POST items/<id>/refresh/`, `POST /api/cart/apply-vin/` | Cart `{currency, items, summary, payment_terms, checkout_blocked, checkout_blocked_reasons, merge}` | G | Compatible, with a frontend workaround | `GET /api/checkout/options/` clears the one-time `merge` summary without returning it (gaps 1 and 15). The frontend keeps it in memory; the clean fix is in the backend | P2 |
| 7 | Checkout and orders | `CheckoutPage.tsx`, `OrdersPage.tsx`, `OrderDetailPage.tsx`, `DecisionPage.tsx`, `OrderSupportPage.tsx` | `GET /api/checkout/options/`, `POST /api/orders/checkout/`, `GET /api/orders/?group=`, `GET /api/orders/<n>/`, `POST …/cancel/`, `POST /api/orders/items/<id>/decision/`, `POST …/courier-fee/accept|decline/`, `POST …/support/messages/`, `…/support/acknowledge/`; invoice via `invoice_url` | Order and order detail per Guide §8 | C | Compatible | No item cancel outside an operator decision (gap 3) | P3 |
| 8 | Payments and statuses | `CheckoutPage.tsx`, `PaymentResultPage.tsx`, `OrderDetailPage.tsx` | `POST /api/orders/<n>/payments/ {method: card|invoice}`, `GET /api/orders/<n>/payments/<id>/` (polling); `POST /api/payments/fake/<id>/complete/` only with demo routes | Payment `{id, kind, provider, status, amount, currency, checkout_url, paid_at, created_at}`; `409 payment_in_progress` | C | Compatible on the demo profile only | On the live staging (real SMS and AMT) demo routes are off, so a fake card payment cannot complete and blocks the order after 30 minutes; use invoice there (owner decision, see H). A real provider's return is unverified (no real provider exists). The balance-payment method list is not on the order (gap 6) | P1 |
| 9 | Pricing and currency | `Layout.tsx`, `CartPage.tsx`, `state/session.tsx` | `GET /api/meta/` (`multi_currency_enabled`, `default_currency`, `currencies`, `usd_rate`), `PATCH /api/cart/ {currency}` | As listed | P / G | Compatible | Only GEL has been exercised. With the mode on, the default is USD, so the first paint can show USD | P3 |
| 10 | Localization | `i18n/{ka,en,ru}.ts`, `client.ts` (`Accept-Language`) | Every endpoint; `GET /api/meta/` `languages`, `default_language` | Texts and errors in the request language; `Content-Language` | P | Compatible | Supplier texts (`name`, `brand`, `condition`) arrive untranslated (Q11) | P3 |
| 11 | Contact page and `/api/meta/` | **Not in the source available here.** Per Jira LPK-229 the frontend has a `/contact` page (desktop footer, mobile profile tab) showing phone, email, WhatsApp, Viber, Facebook, Instagram, pickup points and the support form, and it tolerates a missing `contact` object. That page is in none of: the local React branch (latest `3a8612d`, 2026-11-13, routes in `App.tsx` have no `/contact`), any local or GitHub branch of this repository (searched for `ContactPage` and `"/contact"` across all refs), or this machine's home directory | `GET /api/meta/` → `contact` {`phone`, `email`, `whatsapp`, `viber`, `facebook`, `instagram`} (this batch) | Section D | P | Backend matches the Jira contract; frontend unverified | Point this audit at the revision that holds the `/contact` page, then check its key names and how it builds `tel:`, WhatsApp and Viber links from the numbers. Values stay empty until the owner fills them in (LPK-233) | P2 |
| 12 | Errors, loading and empty states | `client.ts` (`ApiError`: `status`, `code`, `fields`), `components/ui.tsx` (skeletons, toasts), `useResource.ts` | Every endpoint: `{code, detail[, fields: {name: [{code, message}]}]}`; `429` with `retry_after`; `502 supplier_unavailable`, `rate_unavailable`, `sms_failed` | As listed | — | Compatible by reading | Only exercised against the mock | P3 |
| 13 | Support tickets | `SupportPage.tsx` | `POST /api/support/tickets/ {context, part_number?, vin?, message, contact (guests)}`, `GET /api/support/tickets/` | `201 {id, context, status, created_at}` | G | Compatible | One message and one reply per ticket (gap 4) | P3 |
| 14 | Notifications and public links | `NotificationsPage.tsx`, `PublicLinks.tsx` | `GET /api/notifications/?unread=1`, `POST /api/notifications/<id>/read/`, `GET /api/public/n/<token>/` | Paginated notifications | C; link P | Compatible | — | — |
| 15 | Terms and privacy | `LegalPages.tsx` | None | — | — | Placeholder text | Approved texts in ka, en and ru (owner content, not code) | P1 before a public launch |
| 16 | Serving the app on staging | `frontend/docs/DEPLOY_STAGING.md` | nginx: `/` answers 404 today | — | — | Not served | An SPA location with an `index.html` fallback, keeping `/api/`, admin, `/static/`, `/media/` first (LPK-225, owner's nginx change) | P1 for any browser test |

**Integration plan for the frontend developer, in order:**
1. Check the `/contact` page against section D: six keys, plain numbers (no prebuilt links), `""` hidden (row 11).
2. Keep using invoice on the live staging until the owner decides on the fake card route (row 8).
3. After LPK-225 serves the app, run `frontend/tests/real-e2e` once with the owner's phone, with owner approval
   (real SMS).
4. Drop the merge workaround once the backend stops clearing `merge` in `checkout/options` (row 6), a separate story.

**Assumptions, marked as such.** Shapes in the "Expected response" column come from the Guide and the backend source;
they were not observed on a live server. Figma was not opened. An earlier version of this report said a contact design
was needed; that was wrong: Jira LPK-229 says the page exists, and only its source is missing from this workspace.

## H. Owner decisions

1. **Contact content (LPK-233).** The real numbers, email and pages are for the owner to type into the admin after
   deployment. None was invented.
2. **Fake card payments on the live staging** (recorded in the previous batch; not changed here, by instruction).
   Evidence: `config/urls.py` mounts `POST /api/payments/fake/<id>/complete/` only when `ENABLE_DEMO_ENDPOINTS=True`,
   and `deploy/staging/stage.sh` requires `ENABLE_DEMO_ENDPOINTS=False` with real SMS. A fake card payment therefore
   stays pending, becomes `unknown` after 30 minutes and blocks further payments on that order. Options: mount only
   that route whenever `PAYMENT_PROVIDER=fake` outside production, or keep using invoice there.

## I. Deployment handoff (not executed)

As `lionparts-stage` on the staging host, after the owner merges the PR:

```bash
cd ~/lion-parts && deploy/staging/stage.sh backup              # dump before the migration
deploy/staging/stage.sh deploy <merged-main-sha>               # checks target, migrates (pricing.0003), compiles translations, runs checks
# as the owner:
sudo systemctl restart lionparts-stage
deploy/staging/stage.sh health
curl -s https://test2.lionparts.ge/api/meta/ | python3 -c 'import json,sys; print(json.load(sys.stdin)["contact"])'
# expect six empty strings; then fill "Contact page" in the admin (Site settings) and repeat the curl
```

Check also: the admin "Site settings" page shows "Contact page" after "Invoice" in Georgian and Russian, and
`555 12 34 56` is refused with the phone message.

## J. Risks and rollback

- **Risk:** low. Additive key, additive columns, defaults empty. A client that checks exact key sets of `/api/meta/`
  would notice the new key; per LPK-229 the frontend tolerates it being missing.
- **Rollback of code and schema:**
  ```bash
  deploy/staging/stage.sh backup
  venv/bin/python manage.py migrate pricing 0002     # from the new code, before switching back; drops the 6 columns
  deploy/staging/stage.sh rollback <previous-sha>
  # owner: sudo systemctl restart lionparts-stage
  ```
  Old code also runs with the new columns left in place: it only reads and updates the one existing settings row,
  and an update names only the columns it knows. (It could not insert a new row, because Django leaves no database
  default on the new columns, but the row always exists.) Skipping the `migrate` step therefore keeps the typed
  contact data.
- **Rollback in Git:** revert the batch's commits on `main`; nothing else depends on them.
