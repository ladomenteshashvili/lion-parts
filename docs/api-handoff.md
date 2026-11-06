# Lion Parts API: frontend handoff (LP-BE-1205)

For the frontend team. Self-contained; the response shapes are pinned by the backend's contract tests
(`backend/api/tests/contract.py`, one key set per object), so they won't change without an entry in §12.

## 0. Status and limitations

- **Card payments are simulated.** Only the fake payment provider exists (staging/demo). The real provider is
  not integrated yet, so production card payments are **not available** (release blocker). Invoice payments work.
- Staging URL, Mailpit URL and the staff test account are provided separately by the owner once a fresh staging
  exists (`docs/operations.md` §2). Nothing below depends on a specific host.
- The backend does not serve the frontend; all links it sends point to `FRONTEND_BASE_URL` (§10).

Journeys with real recorded answers: `docs/frontend-journeys.md`. Scenario coverage: `docs/uat-matrix.md`.

## 1. Transport, session, CSRF

| Environment | Base URL | Notes |
|---|---|---|
| Local | `http://localhost:8000` | `DEBUG=True`, cookies without `Secure` |
| Isolated staging | given by the owner once it exists | HTTPS, `Secure` cookies, demo codes, demo endpoints, fake payments, Mailpit |
| Production | not available | blocked by the release blockers; has no demo routes |

- Base path `/api/`, JSON in and out (`Content-Type: application/json`), UTF-8. Exception: the invoice
  (`text/html`).
- **Authentication = session cookie** (`sessionid`, HttpOnly). Send cookies on every request:
  `fetch(url, {credentials: "include"})` or axios `withCredentials: true`. No bearer tokens. Guests get a session
  too (it holds their cart, searches and tickets; all of it moves to the account at login/registration).
- **CSRF on every POST/PATCH/PUT/DELETE, anonymous ones included:**
  1. `GET /api/auth/csrf/` → `{"csrf_token": "…"}` (also sets the `csrftoken` cookie).
  2. Header `X-CSRFToken: <csrf_token>` on every unsafe request.
  3. The token rotates on login, registration, logout and password change; those responses carry the new
     `csrf_token`.
  4. Missing/invalid → `403 {"code": "csrf_failed"}`: fetch a new token and retry once.
  5. The browser's `Origin` must be the API's own origin or be listed in `CSRF_TRUSTED_ORIGINS`, or the request answers `csrf_failed` even with a good
     token. On HTTPS a non-browser client must send `Origin` or `Referer` itself.
- **Cookies and CORS.** `sessionid` and `csrftoken` are `Secure` outside local development, and `SameSite=Lax` by default.
  - Same origin as the API (recommended): no CORS is involved.
  - Another subdomain: the owner sets `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`, and `SESSION_COOKIE_DOMAIN`/`CSRF_COOKIE_DOMAIN`
    (`.lionparts.ge`). Credentials are allowed; `*` is never used. Read the token from `GET /api/auth/csrf/`: JavaScript cannot read a cookie set for
    another host unless the domain is shared.
- A guest's session cookie appears with the first request that needs it. Do not clear cookies at login: the guest's cart moves with the session.

## 2. Data formats

| Kind | Format |
|---|---|
| Money | decimal **string** with 2 decimals (`"145.00"`) plus a `currency` (`"GEL"`/`"USD"`) on the object or its parent. Never compute totals client-side. |
| Datetime / date | ISO 8601 `"2026-10-24T10:00:00Z"` / `"2026-09-24"`. Usually UTC, but a fresh search answers `price_valid_until` with its offset (`"2026-11-21T00:00:00+04:00"`), so always parse it; never compare the strings. |
| Phone | responses: 9 digits `"555123456"`; requests: any format (`+995 555 12 34 56`) |
| Ids | integers; `offer_id` is a string; link tokens are UUID strings |
| Enums / codes | lowercase snake_case, never translated; map them to UI labels yourself |
| Nullable | explicit `null`, never omitted |
| Lists | `?page=N` (20 per page) → `{"count", "next", "previous", "results"}` (+ aggregates per endpoint) |

## 3. Languages

- Languages come from `GET /api/meta/` (`languages`, `default_language`): `ka` (default), `en`, `ru`.
- Send `Accept-Language: <code>` on every request; responses carry `Content-Language`. Unknown → `ka`.
- **Localized by the backend:** error `detail` and field `message`, order `attention.message`, `timeline[].title`,
  system item event titles, system support messages, `customer_notice`, notification titles, pickup point,
  city and district names, tariff `benefits_text`, the invoice.
- **Never translated:** codes and enums; supplier part names/brands; text typed by operators or customers.
- SMS, email and notifications created later use the customer's `preferred_language`: send it at registration
  and update it with `PATCH /api/profile/ {"preferred_language": "en"}` when a logged-in user switches language.
- Links in SMS/email carry `lang=<code>`: open the site in that language. The invoice takes `?lang=`.

## 4. Errors

```json
{"code": "otp_invalid", "detail": "The code is incorrect.", "attempts_left": 2}
{"code": "validation_error", "detail": "Please check the highlighted fields.",
 "fields": {"delivery.district": [{"code": "required", "message": "This field is required."}]}}
```

`code` is stable (use it in logic); `detail`/`message` are in the request language and safe to show. Nested
fields use dots. HTTP: 400 input, 401 not logged in, 403 not allowed, **404 not found or not yours**, 409 state
conflict, 429 rate limited (`retry_after` seconds), 502 upstream failure.

| HTTP | `code` | Meaning | Extra |
|---|---|---|---|
| 400 | `validation_error` | Please check the highlighted fields. | `fields` |
| 400 | `invalid_credentials` | Incorrect email or password (same answer for unknown emails). | |
| 400 | `otp_invalid` / `otp_expired` / `otp_attempts_exceeded` | SMS code wrong / expired / too many attempts. | `attempts_left` |
| 400 | `registration_expired` | Registration session expired: fill the form again. | |
| 400 | `token_invalid` | The link is no longer valid. | |
| 401 | `not_authenticated` | Please sign in. | |
| 403 | `csrf_failed` | Refresh the CSRF token. | |
| 403 | `verification_required` | Phone **and** email must be verified (checkout, payments). | |
| 403 | `phone_verification_required` | Phone must be verified (price requests). | |
| 403 | `weight_entry_not_allowed` / `quote_request_not_allowed` | Not available for the customer's tariff. | |
| 403 | `permission_denied` | Not allowed (e.g. a staff session on the customer API). | |
| 404 | `not_found` | Not found, or belongs to someone else. | |
| 409 | `email_taken` / `already_verified` | | |
| 409 | `otp_resend_too_soon` | Wait before requesting a new code. | `resend_in` |
| 409 | `offer_expired` / `offer_not_ready` | Price expired (search again / refresh the line) / still being checked. | |
| 409 | `cart_empty` / `cart_invalid` | Checkout blocked. | `reasons` `[{item_id, code}]` |
| 409 | `no_pending_action` | Nothing to decide/accept any more (double submit). | |
| 409 | `not_cancellable` | Something was already purchased. | |
| 409 | `nothing_to_pay` / `payment_in_progress` | | `payment` (the blocking payment; resume or verify it, §8 B) |
| 409 | `account_has_open_orders` | Delete the account after the orders are completed. | |
| 429 | `throttled` | Too many attempts. | `retry_after` |
| 502 | `supplier_unavailable` / `rate_unavailable` / `sms_failed` | Try again in a few minutes. | |
| 201 | `payment_init_failed` | Inside a successful checkout: the payment page couldn't open; the order exists, retry the payment. | |

## 5. Objects

Key sets (all always present; values may be `null`):

- **Customer** `id, account_type (physical|legal), first_name, last_name, personal_id, company {official_name,
  identification_code, legal_address}|null, phone, is_phone_verified, email, is_email_verified, is_fully_verified,
  tariff {name, payment_terms (full|split_70_30), split_upfront_percent, can_enter_weight, can_request_quote,
  benefits_text}, marketing_opt_in, preferred_language, created_at`
- **Offer** `offer_id, name, brand, condition, status (ready|checking|unavailable), list_unit_price,
  discount_unit_amount, unit_price, currency, eta_min_days, eta_max_days, weight_kg, weight_source, requires_weight,
  in_cart_quantity, image_urls, customer_notice, note`
- **HistoryItem** `id ("log-…"|"qr-…"), kind (search|quote_request), search_id, quote_request_id, part_number, vin,
  name, brand, status (ready|checking|price_ready|price_changed|expired|not_found|purchased|cancelled), unit_price,
  previous_unit_price, currency, offers_count, eta_min_days, eta_max_days, weight_kg, weight_source,
  price_valid_until, is_new, purchased_order_number, operator_message, created_at`
- **QuoteRequest** `id, request_type (weight_price|manual_search), part_number, vin, name, brand, status
  (new|contacted|resolved|cancelled), is_price_ready, unit_price, currency, price_valid_until, weight_kg,
  operator_message, created_at`
- **Cart** `currency, items [CartItem], summary {items_count, items_total, discount_total, delivery_fee (null),
  total}, payment_terms, checkout_blocked, checkout_blocked_reasons [{item_id, code}], merge`
- **CartItem** `id, source (search|prepared_quote), part_number, name, brand, condition, quantity, vin,
  list_unit_price, discount_unit_amount, unit_price, previous_unit_price, line_total, status
  (ok|price_changed|expired|unavailable), eta_min_days, eta_max_days, weight_kg, weight_source, customer_notice,
  price_valid_until, image_urls`
- **Order** `order_number, status, payment_status, currency, created_at, items_count, delivery_method,
  pickup_point {id, name, address, working_hours}|null, delivery {city, city_name, district, district_name,
  address, comment}|null, courier {fee, proposed_fee, action_required, message, confirmed_at, declined_at},
  payment_method, payment_option, items_total, discount_total, courier_fee, total, amount_due_now, amount_paid,
  balance_due, expected_arrival_from, expected_arrival_to, progress {step, of: 6}, attention {kind, message}|null,
  can_cancel, items [OrderItem]`; the **detail** adds `timeline [{type, title, at, amount?, item_id?}],
  payments [Payment], support_messages [{id, item_id, item_part_number, sender_type, message,
  is_read_by_customer, created_at}], support_unread_count, invoice_url`
- **OrderItem** `id, part_number, name, brand, condition, quantity, vin, list_unit_price, discount_unit_amount,
  unit_price, line_total, weight_kg, weight_source, item_status, eta_min_days, eta_max_days, expected_arrival_from,
  expected_arrival_to, action {type, message, old, new, decisions}|null, events [{id, event_type, title, message,
  created_at}], image_urls`
- **Payment** `id, kind (initial|balance), provider (flitt|invoice|manual|fake), status
  (created|pending|paid|failed|expired|refunded|unknown), amount, currency, checkout_url, paid_at, created_at`
- **Notification** `id, event_type, title, message, target, blocking, read_at, created_at`; `target` is
  `{screen: "search_history", quote_request_id}` | `{screen: "decision", order_number, item_id}` |
  `{screen: "order", order_number}` | `{screen: "support_ticket", ticket_id}`; `blocking` is true for
  `support_reply` (show a modal closed only by "Got it", then mark it read).

## 6. Endpoints

Auth: **P** public · **C** logged-in customer · **PV** + verified phone · **V** + verified phone and email ·
**staff** Django staff session (demo only). Customer endpoints answer anonymous users `401` and staff `403`;
anything owned by another customer is `404`.

### Session and account

| Method | Path | Auth | Request | Success |
|---|---|---|---|---|
| GET | `/api/auth/csrf/` | P | — | `{csrf_token}` |
| GET | `/api/auth/me/` | P | — | `{authenticated, customer|null, cart_count, badges {search_new, orders_action, orders_to_pay, notifications_unread}|null}` |
| POST | `/api/auth/register/` | P | physical: `account_type, first_name, last_name, personal_id, phone, email, password, terms_accepted, marketing_opt_in, preferred_language`; legal adds `company_official_name, company_identification_code` | `{masked_phone, expires_in, resend_in}` (SMS sent; `demo_code` only on demo staging) |
| POST | `/api/auth/register/resend-code/` | P | `{}` | as above / `409 otp_resend_too_soon` |
| POST | `/api/auth/register/verify-phone/` | P | `{code}` | `201 {csrf_token, customer, email_verification_sent}` (logged in) |
| POST | `/api/auth/login/` | P | `{email, password}` | `{csrf_token, customer}` |
| POST | `/api/auth/logout/` | C | `{}` | `{csrf_token}` |
| POST | `/api/auth/password-reset/` | P | `{email}` | always `200 {detail}` |
| POST | `/api/auth/password-reset/confirm/` | P | `{uid, token, new_password}` | `200` / `400 token_invalid` |
| POST | `/api/auth/email/send-verification/` | C | `{}` | `{sent_to, resend_in}` / `409 already_verified` |
| POST | `/api/auth/email/verify/` | P | `{token}` | `{verified: true, customer|null}` |
| GET / PATCH | `/api/profile/` | C | PATCH `{marketing_opt_in?, preferred_language?}` | `Customer` |
| POST | `/api/profile/password/` | C | `{current_password, new_password}` | `{customer, csrf_token}` |
| POST | `/api/profile/email/` → `/api/profile/email/confirm/` (P) | C | `{new_email, current_password}` → `{token}` | `{sent_to, resend_in}` → `{email}` |
| POST | `/api/profile/phone/` → `/api/profile/phone/verify/` | C | `{phone}` → `{code}` | `{masked_phone, expires_in, resend_in}` → `Customer` |
| DELETE | `/api/profile/` | C | `{current_password}` | `204` / `409 account_has_open_orders` |
| GET | `/api/meta/` | P | — | languages, currencies, `multi_currency_enabled`, `default_currency`, `usd_rate`, `price_valid_business_days`, pickup points, cities/districts, OTP and password rules |

### Search, prices, history

| Method | Path | Auth | Request | Success |
|---|---|---|---|---|
| POST | `/api/parts/search/` | P | `{part_number, vin?, currency?}` | `{search_id, part_number, vin, currency, exchange_rate, price_valid_until, not_found, offers [Offer]}` |
| POST | `/api/parts/calculate-price/` | C + tariff | `{search_id, offer_id, weight_kg, weight_consent: true}` | `{search_id (new), price_valid_until, offer}` |
| GET / POST | `/api/parts/quote-requests/` | C / PV + tariff | POST `{request_type: "manual_search", part_number, vin?, comment?}` or `{request_type: "weight_price", search_id, offer_id, comment?}` | `Paginated<QuoteRequest>` / `QuoteRequest` |
| GET | `/api/public/q/<token>/` | P | — | prepared price `{quote_request_id, part_number, name, brand, unit_price, currency, price_valid_until, eta_*, weight_kg, operator_message, requires_login}`; expired → `404` |
| GET / DELETE | `/api/parts/history/?filter=all|ready|checking|other` | C | — | `Paginated<HistoryItem> + new_count` / `204` |
| POST | `/api/parts/history/seen/` | C | `{}` | `204` |

### Cart and checkout

| Method | Path | Auth | Request | Success |
|---|---|---|---|---|
| GET / PATCH | `/api/cart/` | P | PATCH `{currency}` (multi-currency only) | `Cart` |
| POST | `/api/cart/items/` | P | `{source: "search", search_id, offer_id, quantity}` or `{source: "prepared_quote", quote_request_id, quantity}` — **never prices** | `201 Cart` |
| PATCH / DELETE | `/api/cart/items/<id>/` | P | `{quantity?, vin?}` | `Cart` |
| POST | `/api/cart/items/<id>/refresh/` | P | `{}` | `Cart` (new price for expired/changed lines) |
| POST | `/api/cart/apply-vin/` | P | `{vin}` | `Cart` |
| GET | `/api/checkout/options/` | V | — | `{currency, summary, payment_terms, payment_options [{value, due_now, balance, default}], payment_methods, payer, pickup_points, delivery, checkout_blocked, checkout_blocked_reasons}` |
| POST | `/api/orders/checkout/` | V | `{delivery_method: "pickup", pickup_point_id}` or `{delivery_method: "delivery", delivery: {city, district, address, comment}}`, plus `payment_option, payment_method (card|invoice), terms_accepted: true` | `201 {order, payment}` (+ `payment_init_failed` body) |

### Orders and payments

| Method | Path | Auth | Request | Success |
|---|---|---|---|---|
| GET | `/api/orders/?group=all|action|to_pay|to_pickup|active|completed|cancelled` | C | — | `Paginated<Order> + group_counts` (needing action first, then to pay) |
| GET | `/api/orders/<n>/` | C | — | Order detail |
| GET | `/api/orders/<n>/invoice/?lang=` | C | — | `text/html` |
| POST | `/api/orders/<n>/cancel/` | C | `{reason?}` | Order / `409 not_cancellable` |
| POST | `/api/orders/<n>/payments/` | V | `{method: card|invoice}` | `201 Payment` / `409 nothing_to_pay`, `409 payment_in_progress (+payment)` |
| GET | `/api/orders/<n>/payments/<id>/` | C | — | `Payment` (poll after the payment page) |
| POST | `/api/orders/items/<id>/decision/` | C | `{decision: accept|cancel|acknowledge}` (from `action.decisions`) | Order / `409 no_pending_action` |
| POST | `/api/orders/<n>/courier-fee/accept/` · `/decline/` | C | `{}` | Order / `409 no_pending_action` |
| POST | `/api/orders/<n>/support/messages/` | C | `{message, item_id?}` | `201 Order` |
| POST | `/api/orders/<n>/support/acknowledge/` | C | `{}` | Order (`support_unread_count` → 0) |

### Notifications and support

| Method | Path | Auth | Request | Success |
|---|---|---|---|---|
| GET | `/api/notifications/?unread=1` | C | — | `Paginated<Notification> + unread_count` |
| POST | `/api/notifications/<id>/read/` | C | `{}` | `204` (a support reply also marks the order message read) |
| GET | `/api/public/n/<token>/` | P | — | `{event_type, title, message: "", order_number, item_part_number, target, requires_login: true}` — no personal data; valid 30 days; otherwise `404` |
| POST | `/api/support/tickets/` | P | `{context: not_found|part|general, part_number?, vin?, message, contact}` (`contact` = email or Georgian mobile, required for guests, ignored for customers) | `201 {id, context, status, created_at}` |
| GET | `/api/support/tickets/` | C | — | `Paginated<{id, context, part_number, vin, message, status, operator_reply, replied_at, created_at}>` |

### Demo / staging only (`ENABLE_DEMO_ENDPOINTS`, never in production)

| Method | Path | Auth | Request | Success |
|---|---|---|---|---|
| POST | `/api/payments/fake/<id>/complete/` | C, owner | `{status: paid|failed|expired}` | `Payment` (`409` if not pending) |
| GET | `/api/demo/outbox/` | staff | — | last 50 emails (in-memory backend only) |
| POST | `/api/demo/orders/<n>/items/<id>/status/` | staff | `{item_status, reason?}` | Order |
| POST | `/api/demo/orders/<n>/items/<id>/action/` | staff | `{action_type, message, proposed_*}` | Order |
| POST | `/api/demo/orders/<n>/courier-fee/` | staff | `{proposed_fee_gel, message}` | Order |
| POST | `/api/demo/quote-requests/<id>/ready/` | staff | `{weight_kg, operator_message}` | QuoteRequest |

## 7. States

- **Order.status:** `payment_pending` → (first payment paid) `processing` ⇄ `action_required` (a decision or the
  courier fee is pending) → `completed` (all items completed); `cancelled` (customer before purchase, or the
  operator; also when the last item is cancelled).
- **payment_status:** `unpaid` · `partially_paid` · `paid` · `refund_pending` (cancelled with money paid, or paid
  more than the new total; refunds are made by the operator).
- **OrderItem.item_status:** `created` → `payment_confirmed` → `checking` → `purchased` → `received_usa` →
  `shipped_to_georgia` → `received_georgia` → `ready_for_pickup` → `completed`; side states `action_required`,
  `cancelled`. `progress.step` is the least advanced item (0–6).
- **attention.kind** (one banner, highest first): `action_required` > `courier_fee` > `balance_due` >
  `ready_for_pickup`.
- **Item decisions:** `action.type` ∈ `price_change, eta_change, alternative_required, fitment_issue, weight_change,
  other`; `decisions` is `["accept", "cancel"]`, or `["acknowledge"]` for a weight change after purchase.
- **Payment.status:** `created` → `pending` → `paid` | `failed` | `expired`; `paid` → `refunded`; `unknown` when
  the provider gave no final answer in time, then `paid` | `failed` | `expired` once the provider answers (a callback,
  the 5-minute reconciliation, or an operator's re-check). It can last indefinitely: age never closes it. `unknown` never returns
  to `pending`, and `failed` or `expired` never return to `pending`. `failed` or `expired` can still become `paid` if
  the provider confirms the money moved, or `unknown` if its answers conflict, so re-read the order before showing a
  retry. `pending` and
  `unknown` block new payments on the order whatever their age (§8 B); `failed` and `expired` do not.
- **Ticket.status:** `new` → `answered` → `closed`.
- **Prices:** offers, cart lines and quotes are valid until `price_valid_until` (10 Georgian business days);
  afterwards `offer_expired` / line `expired` / quote link `404` until the operator re-approves.
- **Currency:** USD is canonical; GEL follows the current NBG rate until checkout. An order freezes its currency,
  rate and amounts; later rate changes never touch it. A courier fee on a USD order is converted when the
  operator proposes it, and the customer accepts exactly that amount.

## 8. Journeys

**A. Guest search → register → card payment**
```
GET  /api/auth/csrf/
POST /api/parts/search/ {"part_number": "G9010-60020"}               → search_id, offers
POST /api/cart/items/ {"source": "search", "search_id": 9812, "offer_id": "AMT-1-G9010-60020", "quantity": 1}
GET  /api/auth/me/                                                    → authenticated false → auth screen
POST /api/auth/register/ {...}                                        → SMS code
POST /api/auth/register/verify-phone/ {"code": "481203"}              → logged in, cart merged, new csrf_token
(email link /verify-email?token=…&lang=ka) POST /api/auth/email/verify/ {"token": "…"}
GET  /api/checkout/options/
POST /api/orders/checkout/ {"delivery_method": "pickup", "pickup_point_id": 1, "payment_option": "full",
                            "payment_method": "card", "terms_accepted": true}
     → 201 {"order": {...}, "payment": {"status": "pending", "checkout_url": "https://…"}}
(redirect to checkout_url; the provider returns to /payment-result?order=LP-2026-00012&payment=34)
GET  /api/orders/LP-2026-00012/payments/34/   (every 3 s, up to 60 s) → "paid" → order "processing"
```
On demo staging `checkout_url` is `/payment-result?…&fake=1`; finish with
`POST /api/payments/fake/34/complete/ {"status": "paid"}`.

**B. Invoice and split payment (70/30 tariff)**: checkout with `payment_method: "invoice"` → `payment.status
"pending"`, `checkout_url: null`; show `order.invoice_url` (add `?lang=`). The operator confirms the transfer.
After the first payment, `balance_due` > 0 → `POST /api/orders/<n>/payments/ {"method": "card"|"invoice"}`.
`409 payment_in_progress` returns the blocking `payment`, for either `method`. Never offer to pay again while it
blocks: the earlier payment may still complete, and a second one could charge the customer twice.
- `pending` (any age): resume it. Card: open its `checkout_url`; if it is `null` the page is no longer the right one to
  pay, so tell the customer the payment is being checked. Invoice (`checkout_url: null`): show `order.invoice_url`.
- `unknown` (any age): the result is being verified, and `checkout_url` is `null`. Tell the
  customer the payment is being checked and read `GET /api/orders/<n>/payments/<id>/` (poll no harder than in A;
  it can take much longer, so check again on the customer's next visit). `paid` moves the order on as usual;
  after `failed` or `expired`, send the payment request again and read the answer.
- `failed` or `expired`: the payment ended on our side, but its provider page may still take money, so we are waiting
  for the provider to confirm that it has closed. No client action is needed. Tell the customer the previous payment is
  being checked, and try again later. If it turns out `paid`, it is counted as usual.
- `created`: a payment being started. Retry shortly. A card payment stops blocking 30 minutes after creation.

Never decide from a stored `failed` or `expired` status that a retry will work: send the request and read the answer.

A `201` can carry `status: "failed"` or `"expired"` with `checkout_url: null`: the page could not be opened, or the
payment was closed meanwhile (for example the order was cancelled, or a newer payment exists). Read `status` before
redirecting.

**C. Price change decision**: notification `action_required` (target `decision`) → `GET /api/orders/<n>/` →
item `action` `{"type": "price_change", "old": {"unit_price": "320.00"}, "new": {"unit_price": "348.00"},
"decisions": ["accept", "cancel"]}` → `POST /api/orders/items/<id>/decision/ {"decision": "accept"}` → updated
order (`balance_due` grows; pay it as in B). A second submit → `409 no_pending_action`.

**D. Courier fee**: `courier.action_required` true and `attention.kind "courier_fee"` → show `courier.proposed_fee`
and `courier.message` → `/courier-fee/accept/` (fee added to the total) or `/decline/` (switches to pickup).

**E. Cancellation**: show the button only when `can_cancel`. `POST /api/orders/<n>/cancel/` → `status
"cancelled"`; if money was paid, `payment_status "refund_pending"` (the refund is manual).

**F. Weight and price requests** (tariff flags): an offer with `requires_weight` → ask the weight with consent →
`/api/parts/calculate-price/` → add with the new `search_id`. Or request a price (`quote-requests`); when it's
ready the customer gets a `price_ready` notification / SMS link `/q/<token>` → `GET /api/public/q/<token>/` → login
→ `POST /api/cart/items/ {"source": "prepared_quote", "quote_request_id": 18, "quantity": 1}`.

**G. Support**: guests `POST /api/support/tickets/` with `contact` (the reply goes to that email/phone);
customers without `contact` (the reply is a blocking `support_reply` notification). Order questions:
`/api/orders/<n>/support/messages/`; operator replies appear in `support_messages`; acknowledge with
`/support/acknowledge/` or by reading the notification.

**H. Notifications and badges**: refresh `GET /api/auth/me/` (badges) after actions and every ~60 s; list
`/api/notifications/`; navigate with `target`; mark read with `/read/`. An SMS/email link `/n/<token>` →
`GET /api/public/n/<token>/` (no personal data) → login → open `target`.

**I. Currency (only when `multi_currency_enabled`)**: send `currency` in the search; `PATCH /api/cart/
{"currency": "GEL"}` converts the cart; checkout charges the cart currency.

**J. Account deletion**: `DELETE /api/profile/ {"current_password": "…"}` → `204` (logged out everywhere) or
`409 account_has_open_orders`.

## 9. Rate limits

Per IP unless noted: OTP sending (also per phone), OTP checks, login (also per email), password reset,
registration, search and price calculation, guest support tickets, public links; per customer: support tickets
and order messages, verification emails. `429` carries `retry_after` seconds. Limits now hold exactly under parallel
requests, so a client that relied on occasional overshoot will see `429` sooner. If the rate-limit store is unavailable,
the answer is `429`, not success.

## 10. Routes the frontend must implement (links sent by the backend)

| Path | Purpose | Call |
|---|---|---|
| `/verify-email?token=…&lang=` | email verification | `POST /api/auth/email/verify/ {token}` |
| `/reset-password?uid=…&token=…&lang=` | password reset | `POST /api/auth/password-reset/confirm/` |
| `/profile/email/confirm?token=…&lang=` | email change | `POST /api/profile/email/confirm/ {token}` |
| `/q/<token>?lang=` | prepared price | `GET /api/public/q/<token>/` |
| `/n/<token>?lang=` | order notification | `GET /api/public/n/<token>/` |
| `/payment-result?order=<n>&payment=<id>` | return from the payment page | poll `GET /api/orders/<n>/payments/<id>/` |

## 11. Integration requirements and known limitations

**Demo behaviour (staging only).**

- **OTP:** `demo_code` appears in the answers of `register`, `resend-code` and `profile/phone` when `SMS_DEMO_MODE` is on. Production never has it: do not build a
  screen that depends on it. No SMS is sent. Limits still apply: 60 s between codes, 5 attempts per code, 5 minutes' validity.
- **Email:** captured by Mailpit, nothing leaves the server. The link format is in §10. Link parameters are URL-encoded: read them with `URLSearchParams` and
  send the decoded value.
- **Fake payments:** `checkout_url` is `<frontend>/payment-result?order=<n>&payment=<id>&fake=1`. Your page plays the provider with
  `POST /api/payments/fake/<id>/complete/ {"status": "paid"|"failed"|"expired"}`; a payment not `pending` answers `409 nothing_to_pay`. No money moves. After `failed` or `expired` a
  new payment is allowed at once, which a real provider's page will not always allow: handle `409 payment_in_progress` as in §8 B.
- **Supplier:** the demo supplier answers for any part number; one starting with `NF` is not found. Prices and names are not real.
- **Operator steps:** `/api/demo/...` with a staff session, or the Django admin.

**Pitfalls.**

- Registration needs `terms_accepted: true`; checkout needs its own `terms_accepted: true` and a fully verified customer (`403 verification_required`).
- `POST /api/cart/items/` answers `201` with the whole cart; `register/verify-phone` answers `201`. Accept any 2xx.
- Replace the stored CSRF token whenever an answer carries `csrf_token`.
- Limits are exact and per IP on a shared office or mobile network: show `retry_after`, and do not retry `429` automatically. See §9.
- `502 sms_failed` does not mean the SMS was not sent: the next attempt can answer `409 otp_resend_too_soon`. Show `resend_in`.
- Never show a retry button from a stored payment status: send the request and read the answer (§8 B).
- `merge` in the cart appears once: show it when it arrives.
- A status can change between screens: re-read the order after any action, and use the answer's `can_cancel` and `action.decisions`.
- The invoice is `text/html`, not JSON, and needs the session: open it in the browser rather than fetching it into JSON handling.
- `checkout_url` can be `null` on a `pending` payment (§12).

**Requirements.**

- Always send cookies, `X-CSRFToken` and `Accept-Language`; never send prices or totals.
- Treat `404` on owned resources as "not found" (it may be someone else's).
- Card payments: fake provider only (§0). Staging only: demo codes, fake payments, demo endpoints.
- SMS/email are generic (event, order or part number, link): details are read after login.
- Public `/n/` links never show operator text and expire after 30 days.
- The operator works in the Django admin (Georgian by default); there is no operator API.

## 12. Contract change log (since the Guide's §8 baseline)

| Date | Change | Breaking |
|---|---|---|
| 2026-11-03 | Public notification link: `message` always `""`, link valid 30 days, same `404` for unknown/expired (D23). | No (field kept) |
| 2026-11-03 | USD orders: the courier fee is converted at the NBG rate when proposed (frozen per proposal) (D21). | No |
| 2026-11-03 | `GET /api/support/tickets/` answers a staff session `403` (was `401`), like every customer endpoint. | No |
| 2026-11-03 | Support tickets and order messages: per-customer rate limit (`429 throttled`). | No |
| 2026-11-05 | `POST /api/orders/<n>/payments/`: `409 payment_in_progress` is now also returned for an `unknown` payment (any provider, any age) and a `pending` card payment older than 30 minutes (previously these allowed a new payment). They must be resumed or verified, not paid again (§8 B). Same path, error code and `payment` body. | No (behaviour: handle `unknown` in the existing `409`) |
| 2026-11-05 | A payment closed while its provider page was opening keeps its closed status and gets no `checkout_url`, so a `201` from `POST /api/orders/<n>/payments/` can carry `status: "expired"` and `checkout_url: null`. | No (same shape as the existing `failed` case) |
| 2026-11-05 | An `unknown` payment can now resolve later to `paid`, `failed` or `expired` without any client action (reconciliation or an operator's re-check). A `failed` or `expired` payment can become `paid` if the provider confirms it, or `unknown` if the provider's answers conflict; `unknown` then blocks new payments with the existing `409`. No endpoint, field or error code changed. | No |
| 2026-11-06 | `POST /api/orders/<n>/payments/`: `409 payment_in_progress` is now also returned for a `failed` or `expired` card payment whose provider page may still take money, until the provider confirms it has closed. Previously those always allowed a retry. A payment that never got a provider page still allows one. | Yes: client handling above |
| 2026-11-06 | `checkout_url` is now returned only for a `pending` payment that is still what the order needs: `null` once the payment is closed, the order is cancelled, the balance no longer covers the amount, or an `initial` payment is overtaken by money already paid. The key stays in every response. A page the customer already opened can still complete; that result is counted, and any excess shows as `refund_pending`. | Yes: never assume a `pending` card payment has a link |
| 2026-11-06 | `POST /api/auth/register/resend-code/` and other OTP sends: after `502 sms_failed` whose delivery outcome is unknown, the next send answers `409 otp_resend_too_soon` with `resend_in` until the cooldown ends. Parallel sends: one succeeds, the others answer `409`. Same codes and fields. | No |
| 2026-11-06 | `POST /api/cart/items/<id>/refresh/`: a line removed during the supplier lookup answers `404 not_found` and is not restored; a quantity changed meanwhile is kept. A slower answer than a refresh already applied is dropped: `200` with the current cart. | No |
| 2026-11-06 | Supplier lookups stop at the configured limit (default 30 s) and answer `502 supplier_unavailable`. A non-XML supplier response now also gives that `502` instead of a server error. | No |
| 2026-11-06 | OTP sends: a Sender.ge answer other than `statusId 1` now keeps the cooldown, because the SMS may have been sent. Same `502 sms_failed`; the next send can answer `409 otp_resend_too_soon` instead of sending again at once. | No |
| 2026-11-06 | A supplier answer with an unexpected XML shape now gives `502 supplier_unavailable` instead of a server error, and a single plain row is read as one offer. A slow supplier body now stops at the lookup limit too. | No |
| 2026-11-06 | Cart add, cart refresh and checkout: if the exchange rate expires in the instant before the cart is locked, the answer is the existing `502 rate_unavailable`; the next attempt fetches the rate. Very rare. | No |
| 2026-11-06 | Demo quote approval: if the request changes during the supplier call, it answers the usual validation error on `supplier_price_usd`, and nothing changes. Staging only; the Django admin shows the same message. | No |
| 2026-11-06 | All responses carry `Content-Security-Policy-Report-Only`. Emails carry a translated footer and, when configured, `Reply-To`. No JSON changed. | No |

## 13. Design review (LPK-207)

**Blocked:** the 13-item Figma gap list from LPK-207 is in Jira, and neither Jira nor Figma could be read when this was written, so no item is judged
here and the visual check is pending. To finish it, take each item to the table below. If its screen has no row, or needs a field not in §5, the question goes
to the product owner and the designer. Do not assume a backend field that is not listed.

| Screen area | Backend capability | Endpoints | Limits the design must respect |
|---|---|---|---|
| Registration, OTP | Physical and legal accounts, six-digit code, resend timer | `/api/auth/register/`, `verify-phone/`, `resend-code/` | Timer from `resend_in`; attempts from `attempts_left` |
| Login, reset, verification | Session cookie, emailed links | `/api/auth/login/`, `password-reset/`, `email/verify/` | Same error for wrong password and unknown email |
| Profile | Name and ID are read-only after registration; language, marketing, email, phone, password, deletion | `/api/profile/...` | No endpoint changes name, personal ID or company |
| Search, offers | Offers with status, price, ETA, images | `/api/parts/search/` | `checking` has no price; ETA can be `null` |
| Weight, price requests | Depend on the tariff | `calculate-price/`, `quote-requests/`, `/api/public/q/<token>/` | Hidden unless the tariff allows |
| History | Searches and requests with `is_new` | `/api/parts/history/` | Filters: all, ready, checking, other |
| Cart | Lines, statuses, merge summary, one VIN per line or for all | `/api/cart/...` | Totals come from the server; quantity 1 to 99 |
| Checkout | Pickup or delivery, full or split, card or invoice | `/api/checkout/options/`, `/api/orders/checkout/` | Options come from the server; delivery fee is proposed later by the operator |
| Orders | Groups, progress 0 to 6, one attention banner, timeline, per-item decisions | `/api/orders/...` | One banner, chosen by the backend |
| Payment result | Polling, `failed`, `expired`, `unknown` | `/api/orders/<n>/payments/<id>/` | Needs "being checked" wording (§8 B) |
| Invoice | Server-rendered page in three languages | `/api/orders/<n>/invoice/` | Layout comes from the backend, not Figma |
| Notifications, badges | List, read state, blocking support reply, badges | `/api/notifications/`, `/api/auth/me/` | SMS and email text are generic |
| Support | Guest and customer tickets, order messages | `/api/support/tickets/`, `/api/orders/<n>/support/...` | Guests get the reply by email or SMS only |
| Operator screens | Django admin only | none | No operator API, so no custom operator design |

Open questions for the designer and product owner regardless of the list: wording for "payment being checked", the states after `502 sms_failed`, and how a
`429` looks. These come from behaviour in §8 and §11, not from the list.
