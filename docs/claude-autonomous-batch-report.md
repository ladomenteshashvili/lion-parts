# Autonomous batch: contact settings, `/api/meta/` contact, frontend readiness

Branch `feature/contact-api-and-frontend-audit`, from `main` at `f9a28f4`. Nothing was merged, deployed or restarted.
Jira could not be read from this environment, so LPK-230 to 232 were implemented from the story titles, the
Implementation Guide and the existing code. Compare the acceptance criteria in Jira before closing them.

## A. Executive summary

- **LPK-230:** Site settings (the existing singleton, `pricing.SiteSettings`) gets five optional contact fields, edited
  in Django Admin under "Contact details": phone, email, address (per language), working hours (per language) and a
  WhatsApp link. All are empty by default; no business data was entered.
- **LPK-231:** `GET /api/meta/` gets a `contact` object with those values. Existing keys are unchanged, and the
  response makes no extra database query.
- **LPK-232:** Georgian and Russian labels, help texts and validation messages for the new admin fields.
- **LPK-224:** API compatibility matrix for the React v2 frontend (section G), built from its source on the local
  branch `feature/react-v2-figma-integration` and checked against this backend's URL table.
- **LPK-227:** not advanced. The token and screen maps already exist on the React branch
  (`frontend/docs/DESIGN_TOKENS.md`, `FIGMA_MAPPING.md`). Figma has no contact screen, so there is nothing new to map.

Why one `contact` object in the existing endpoint: the frontend already loads `/api/meta/` once at start-up for
languages, currencies and pickup points, so the contact details arrive with it at no extra request.

## B. Jira mapping

| Jira | Status in this batch | Evidence | Still open |
|---|---|---|---|
| LPK-230 Contact details in Site settings | Implemented and tested | `pricing/models.py`, `pricing/admin.py`, `pricing/migrations/0003_site_contact_details.py`, `pricing/tests/test_contact.py` | Compare the fields with the Jira acceptance criteria; one phone number only (see H) |
| LPK-231 Contact data in `GET /api/meta/` | Implemented and tested | `pricing/services.py`, `api/tests/contract.py`, `api/tests/test_meta.py`, Guide §8.1 and §16 | Same |
| LPK-232 Georgian/Russian admin texts | Implemented for the new fields | `locale/ka`, `locale/ru` (12 entries each), admin label test | A native-speaker read (the same review as LPK-201) |
| LPK-224 React and backend gap matrix | Audit done, nothing built | Section G | Frontend work listed there |
| LPK-227 Figma tokens and React map | Not advanced | Existing React-branch docs | No new Figma access was used; a contact screen needs a design first |
| LPK-233 Deployment and content | Out of scope | Section I gives the steps | Owner |

Do not close any of these from this report alone: the acceptance criteria were not readable here.

## C. Files changed

| File | Change |
|---|---|
| `backend/pricing/models.py` | Five `contact_*` fields; phone format and WhatsApp-link validators |
| `backend/pricing/migrations/0003_site_contact_details.py` | Adds the five columns |
| `backend/pricing/admin.py` | "Contact details" fieldset; address and hours edited per language |
| `backend/pricing/services.py` | `contact` in the meta payload, from the settings row already read |
| `backend/locale/{ka,ru}/LC_MESSAGES/django.po` | 12 new translations each, appended; nothing else changed |
| `backend/pricing/tests/test_contact.py` | New: validation, API, privacy, query count, admin, translations |
| `backend/pricing/tests/test_admin.py` | The admin form helper sends the new fields too |
| `backend/api/tests/contract.py`, `test_contract.py`, `test_meta.py` | `contact` in the meta key sets |
| `docs/api-handoff.md` | Meta row mentions `contact` |
| `docs/claude-autonomous-batch-report.md` | This report |

`AI_IMPLEMENTATION_GUIDE.md` is excluded from Git in this working copy. Its §8.1 example and §16 change log were
updated locally only, and the owner should carry that into the guide's master copy.

## D. API contract

`GET /api/meta/`, public, unchanged apart from the new last key.

Nothing set yet (the state after deployment):

```json
"contact": {"phone": "", "email": "", "address": "", "working_hours": "", "whatsapp_url": ""}
```

Filled in (placeholder values), with `Accept-Language: ru`:

```json
{
  "languages": [{"code": "ka", "name": "ქართული"}, {"code": "en", "name": "English"}, {"code": "ru", "name": "Русский"}],
  "default_language": "ka",
  "currencies": [{"code": "GEL", "symbol": "₾"}],
  "multi_currency_enabled": false, "default_currency": "GEL", "base_currency": "USD", "price_valid_business_days": 10,
  "usd_rate": {"rate": "2.7000", "date": "2026-11-18"},
  "pickup_points": [{"id": 1, "name": "…", "address": "", "working_hours": ""}],
  "delivery": {"country": "GE", "cities": [], "districts": []},
  "otp_length": 6,
  "otp_resend_seconds": 60,
  "password_rules": {"min_length": 8, "upper": true, "lower": true, "digit": true, "symbol": true},
  "contact": {
    "phone": "+995 32 000 00 00",
    "email": "info@example.com",
    "address": "Адрес на русском",
    "working_hours": "Пн–Пт, 10:00–19:00",
    "whatsapp_url": "https://wa.me/995000000000"
  }
}
```

Rules:
- Every key is always present. An unset value is `""`, the same convention as `pickup_points[].address`. Hide a row
  whose value is empty; hide the whole block when all five are empty.
- `address` and `working_hours` follow the request language and fall back to Georgian, like other translated texts.
- `phone` is for display. For a `tel:` link, remove everything except `+` and digits.
- `whatsapp_url` is always `https://wa.me/…` or `https://api.whatsapp.com/…`; anything else is refused in the admin.
- Only these five fields are published. The invoice seller's phone, email, code and bank details stay internal (a
  test checks this).

TypeScript for the frontend:

```ts
contact: { phone: string; email: string; address: string; working_hours: string; whatsapp_url: string };
```

## E. Database changes

One migration, `pricing.0003_site_contact_details`: five `ADD COLUMN`s on the one-row `pricing_sitesettings` table,
three text columns defaulting to `''` and two JSON columns defaulting to `{}`. No data migration, no change to existing
columns, no lock of any size. It is reversible: `migrate pricing 0002` drops the five columns and loses only what an
operator typed into them.

## F. Test report

Environment: Python 3.14, Django 6.1, the project venv. No real AMT, SMS, payment or SMTP call.

| Run | Settings | Result |
|---|---|---|
| Full suite, disposable PostgreSQL 18 (`qa_regress` on 127.0.0.1:55432, checked before the run) | plain `config.settings` | 1,229 ran: 1,228 passed, 0 failed, 0 errors, 1 skipped (377 s) |
| Full suite, SQLite | `config.e2e_settings` | 1,229 ran: 1,156 passed, 0 failed, 0 errors, 73 skipped (42 s) |
| `check`, `makemigrations --check --dry-run`, `compilemessages` | plain `config.settings` | No issues; no changes detected; ka and ru compile |
| New tests `pricing.tests.test_contact` | both | 13 tests, OK |

The SQLite run skips the PostgreSQL-only tests (locking and concurrency). The one PostgreSQL skip is the staging
provisioning test, which refuses to run on a host that has a real staging; it runs in CI. GitHub CI has not run on
this branch.

What the new tests cover: empty defaults; valid and invalid phone numbers, emails and WhatsApp links (other hosts,
`http`, ports, user info, `javascript:`); `contact` empty when unset; values in ka, en and ru with fallback; invoice
data not published; the query count of `/api/meta/` (5, the same as before); public and read-only; admin save and
clear; admin field errors; the Georgian-required rule for translated fields; Georgian and Russian labels on the admin
page; the contract key sets.

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
| 11 | Contact page and `/api/meta/` | **None:** no route, no page, and `Meta` in `types.ts` has no `contact` | `GET /api/meta/` → `contact` (this batch) | Section D | P | Backend ready, frontend missing | A design (Figma has no contact screen: its frame list and texts were searched), then a route or footer block, and `contact` in the `Meta` type. Values stay empty until the owner fills them in (LPK-233) | P2 |
| 12 | Errors, loading and empty states | `client.ts` (`ApiError`: `status`, `code`, `fields`), `components/ui.tsx` (skeletons, toasts), `useResource.ts` | Every endpoint: `{code, detail[, fields: {name: [{code, message}]}]}`; `429` with `retry_after`; `502 supplier_unavailable`, `rate_unavailable`, `sms_failed` | As listed | — | Compatible by reading | Only exercised against the mock | P3 |
| 13 | Support tickets | `SupportPage.tsx` | `POST /api/support/tickets/ {context, part_number?, vin?, message, contact (guests)}`, `GET /api/support/tickets/` | `201 {id, context, status, created_at}` | G | Compatible | One message and one reply per ticket (gap 4) | P3 |
| 14 | Notifications and public links | `NotificationsPage.tsx`, `PublicLinks.tsx` | `GET /api/notifications/?unread=1`, `POST /api/notifications/<id>/read/`, `GET /api/public/n/<token>/` | Paginated notifications | C; link P | Compatible | — | — |
| 15 | Terms and privacy | `LegalPages.tsx` | None | — | — | Placeholder text | Approved texts in ka, en and ru (owner content, not code) | P1 before a public launch |
| 16 | Serving the app on staging | `frontend/docs/DEPLOY_STAGING.md` | nginx: `/` answers 404 today | — | — | Not served | An SPA location with an `index.html` fallback, keeping `/api/`, admin, `/static/`, `/media/` first (LPK-225, owner's nginx change) | P1 for any browser test |

**Integration plan for the frontend developer, in order:**
1. Add `contact` to the `Meta` type and show it where the design puts it, once a design exists (row 11).
2. Keep using invoice on the live staging until the owner decides on the fake card route (row 8).
3. After LPK-225 serves the app, run `frontend/tests/real-e2e` once with the owner's phone, with owner approval
   (real SMS).
4. Drop the merge workaround once the backend stops clearing `merge` in `checkout/options` (row 6), a separate story.

**Assumptions, marked as such.** Shapes in the "Expected response" column come from the Guide and the backend source;
they were not observed on a live server. Figma was not opened in this batch; the statement that it has no contact
screen comes from the offline export (`design-reference/scratchpad/frames.tsv` and `spec/texts.md`).

## H. Owner decisions

1. **Contact schema.** One phone number only. If the shop needs two (for example sales and service), say which, and
   it becomes a list. Viber, Facebook or other links were not added because no requirement names them.
2. **Contact content (LPK-233).** Real phone, email, address, hours and WhatsApp number are for the owner to type into
   the admin after deployment. None was invented.
3. **Fake card payments on the live staging** (from the previous batch, still open): mount only the fake completion
   route whenever `PAYMENT_PROVIDER=fake` outside production, or keep using invoice there. It is a payment-policy
   change, so it was not made.

## I. Deployment handoff (not executed)

As `lionparts-stage` on the staging host, after the owner merges the PR:

```bash
cd ~/lion-parts && deploy/staging/stage.sh backup              # dump before the migration
deploy/staging/stage.sh deploy <merged-main-sha>               # checks target, migrates (pricing.0003), compiles translations, runs checks
# as the owner:
sudo systemctl restart lionparts-stage
deploy/staging/stage.sh health
curl -s -H 'Accept-Language: ka' https://test2.lionparts.ge/api/meta/ | python3 -c 'import json,sys; print(json.load(sys.stdin)["contact"])'
# expect five empty strings; then fill "Contact details" in the admin (Site settings) and repeat the curl
```

Check also: the admin "Site settings" page opens in Georgian and Russian with the new labels, and a wrong WhatsApp link
is refused with a message.

## J. Risks and rollback

- **Risk:** low. Additive key, additive columns, defaults empty. A client that checks exact key sets of `/api/meta/`
  would notice the new key; the React app does not.
- **Rollback of code and schema:**
  ```bash
  deploy/staging/stage.sh backup
  venv/bin/python manage.py migrate pricing 0002     # from the new code, before switching back; drops the 5 columns
  deploy/staging/stage.sh rollback <previous-sha>
  # owner: sudo systemctl restart lionparts-stage
  ```
  Old code also runs with the new columns left in place: it only reads and updates the one existing settings row,
  and an update names only the columns it knows. (It could not insert a new row, because Django leaves no database
  default on the new columns, but the row always exists.) Skipping the `migrate` step therefore keeps the typed
  contact data.
- **Rollback in Git:** revert the batch's commits on `main`; there are no other dependents.
