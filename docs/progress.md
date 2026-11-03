# Progress tracker

One row per backlog story (76). Status: `done` (committed, tests green), `in progress`, `todo`.
"M0" = Epic 1 foundation (PR #58). Jira: the epic key (verified by the owner) / the story key where known;
"—" = story key not known yet, never guessed (master plan D7). † = not assigned to a milestone in the
backlog; planned with that milestone (master plan G1). Evidence = commit(s) + the test modules that prove the acceptance criteria.

| Story | Jira (epic / story) | Title | Pts | Milestone | Depends on | Status | Commit | Evidence |
|---|---|---|---|---|---|---|---|---|
| LP-BE-101 | LPK-83 / LPK-96 | Settings & configuration baseline | 3 | M0 | — | done | bd996df | PR #58, CI green (PostgreSQL 16) |
| LP-BE-102 | LPK-83 / LPK-97 | Error envelope, ApiError and translated messages | 5 | M0 | 101, 1301 | done | 458a2fd | PR #58, CI green (PostgreSQL 16) |
| LP-BE-103 | LPK-83 / LPK-98 | Session authentication with CSRF for all unsafe requests + permissions | 3 | M0 | 102 | done | 59cba2f | PR #58, CI green (PostgreSQL 16) |
| LP-BE-104 | LPK-83 / LPK-99 | Throttle classes | 3 | M0 | 101 | done | a02d675, d73336a | PR #58, CI green (PostgreSQL 16) |
| LP-BE-105 | LPK-83 / LPK-100 | Pagination, meta and health | 2 | M0 | 102 | done | df3fbf3 | PR #58, CI green (PostgreSQL 16) |
| LP-BE-106 | LPK-83 / LPK-101 | Demo endpoints switch & email outbox | 2 | M0 | 101 | done | 8b1b11d, 09338f1 | PR #58, CI green (PostgreSQL 16) |
| LP-BE-107 | LPK-83 / LPK-102 | CI update | 1 | M0 | 101 | done | 0816312 | PR #58, CI green (PostgreSQL 16) |
| LP-BE-201 | LPK-84 / — | Account models, fresh migrations, seed | 5 | M1 | 101 | done | befe29b | accounts/test_models.py; legacy accounts views/serializers/urls deleted (routes frozen in api/legacy.py); orders migrations repointed; D4 shim |
| LP-BE-202 | LPK-84 / — | Validators & normalizers | 2 | M1 | 201 | done | 918168f | accounts/test_validators.py (phone formats incl. AC cases, IDs, VIN, part number, every password code) |
| LP-BE-203 | LPK-84 / — | OTP service & SMS wrapper | 5 | M1 | 202 | done | 309d0a2 | accounts/test_otp.py: 5 wrong -> attempts_left 0 then otp_attempts_exceeded; 409 resend_in; demo_code matrix; sms_failed stores nothing; parallel guesses (PostgreSQL/CI) |
| LP-BE-204 | LPK-84 / — | Registration step 1 (data + SMS) and resend | 5 | M1 | 203, 104 | done | 81489dd | accounts/test_register.py: physical/legal 200, taken+required together, no User/Customer after step 1, only password hash in session, resend/cooldown/expired, 6th code per phone -> 429, CSRF; legacy send_phone_verification_code_* (3) replaced |
| LP-BE-205 | LPK-84 / — | Registration step 2 (verify phone → create account → login) | 5 | M1 | 204, 207 | done | 7503ba4 | accounts/test_register_verify.py: one User+Customer(+company), authenticated session, verify email in outbox, session key rotates, wrong code keeps draft, race -> 409 email_taken; 5 verify_phone_code_* + 16 legacy legal-entity tests replaced |
| LP-BE-206 | LPK-84 / — | Login, logout, me, csrf | 3 | M1 | 103, 201 | done | 058c9e3 | accounts/test_login.py: identical body for unknown/wrong/staff/inactive/anonymized, old sessionid rejected after logout, staff can't log in, session key rotates, CSRF required + rotated, login_ip/login_email throttles; 2 legacy login tests replaced |
| LP-BE-207 | LPK-84 / — | Email infrastructure | 2 | M1 | 101, 1301, 1303 | done | 26c0d53 | api/tests/test_mail.py: 4 templates reach the outbox with links; ru email + lang=ru; SMTP failure -> False + log; every template translated ka/ru |
| LP-BE-208 | LPK-84 / — | Email verification (send & verify) | 3 | M1 | 207, 206 | done | c305319 | accounts/test_email_verification.py: tampered/48h+/pre-email-change/inactive/anonymized -> token_invalid, idempotent, customer only for own session, already_verified 409, email_send throttle |
| LP-BE-209 | LPK-84 / — | Password reset (request & confirm) | 3 | M1 | 207 | done | 45b3648 | accounts/test_password_reset.py: identical body known/unknown, no email for unknown/inactive/anonymized, single-use token, other sessions end, no login, new_password field errors, throttle; legacy reset test replaced |
| LP-BE-210 | LPK-84 / — | Profile: read, update, change password | 2 | M1 | 206 | done | 63e5834 | accounts/test_profile.py: exact Customer keys + tariff/company shapes, PATCH ignores other keys, xx -> invalid, password change keeps this session/ends others; legacy profile (3) + password (2) tests replaced; demo_login (5) removed per G§8.11 (unreachable route asserted in api/tests/test_legacy.py) |
| LP-BE-211 | LPK-84 / — | Email change | 3 | M1 | 207 | done | 2a024dd | accounts/test_email_change.py: login only with old email until confirm, then only new; notice to old address; taken/incorrect/invalid; 409 email_taken at confirm; 48h/tampered; stale links die; verify link dies after change |
| LP-BE-212 | LPK-84 / — | Phone change | 3 | M1 | 203 | done | b196058 | accounts/test_phone_change.py: changes only after the correct code, taken (before send and at verify), invalid_phone, SMS in preferred language, pending phone single use |
| LP-BE-213 | LPK-84 / — | Account deletion (anonymize) + retention command | 5 | M1 | 210 (open-orders check becomes real after LP-BE-601) | done | f100f2c | accounts/test_delete_account.py: wrong password / open order change nothing, anonymized fields, company+OTP deleted, all sessions removed, login fails, orders keep snapshot, purge only touches anonymized customers' orders >5y, idempotent. Re-test open-orders after LP-BE-601 (plan G3) |
| LP-BE-301 | LPK-85 / — | Pricing app & models | 3 | M2 | 201, 1302 | done | d113070 | pricing/tests/test_models.py: seed (GEL/USD VAT 18, pickup point, Tbilisi districts), singleton refuses a second row, discount normalize/validation, /api/meta/ en/ru names, active-only currencies/points, latest rate |
| LP-BE-302 | LPK-85 / — | NBG exchange rate client & job | 3 | M2 | 301 | done | c0e4a3f | pricing/tests/test_rates.py: recorded NBG response -> 2.5971 for 2026-10-24, quantity division + upsert, stale+down -> RateUnavailable (502 rate_unavailable), fresh -> no HTTP, idempotent command; requests mocked |
| LP-BE-303 | LPK-85 / — | Price engine & snapshots | 5 | M2 | 302, 304 | done | 1e7cbbe | pricing/tests/test_engine.py: worked example 427.07 GEL / 164.26 USD, USD markup changes only USD, VAT per currency, partner + default-markup fallback, discounts snapshotted, reprice identical without changes / new rate / currency switch, JSON round trip, ROUND_HALF_UP |
| LP-BE-304 | LPK-85 / — | Discount engine | 3 | M2 | 301 | done | 19679b6 | pricing/tests/test_discounts.py: 5% customer vs 10% part -> 10%, fixed 50 GEL at 2.5 -> 20.00 USD, tie -> lower id, guests get no customer/tariff rules, inactive/expired/future ignored, cap, rounding |
| LP-BE-401 | LPK-86 / — | Parts model changes & part images | 3 | M2 | 201, 301 | done | 8c1c150 | parts/test_images.py: .gif and 6 MB rejected, normalized part number, brand-specific before generic (absolute URLs); parts/test_admin.py: price-ready action via engine (replaces 3 legacy admin tests); Pillow 12.3.0; parts 0007/0008 |
| LP-BE-402 | LPK-86 / — | Offer builder (AMT + demo) on the price engine | 5 | M2 | 303, 401 | done | c9000b1 | parts/test_offers.py: demo unit_price == engine output, AMT/demo rows without weight -> checking with null prices, offer keys exactly G§8.0 Offer (supplier price/snapshot never public), supplier error -> 502 supplier_unavailable, rate error -> rate_unavailable, ETA from default carrier |
| LP-BE-403 | LPK-86 / — | Search endpoint | 3 | M2 | 402, 104 | done | f1fa4d0 | parts/test_search.py: exact G§8.4 keys incl. search_id/price_valid_until, partner vs guest prices, log owned by customer or session, validation (required/invalid_vin/inactive currency), 502 supplier (logged as failed)/rate, CSRF, throttle; 6 legacy search_* tests replaced |
| LP-BE-404 | LPK-86 / — | Search history, seen, clear | 8 | M2 | 403, 406 | done | fb3b3eb | parts/test_history.py: 17940-25020 + 1794025020 -> one item, price_changed vs the previous search, ordered part -> purchased + order number (legacy OrderItem until LP-BE-601), all statuses + filters (other = expired/not_found/purchased/cancelled), seen -> new_count 0, DELETE hides searches and keeps quote requests, own history only, pagination; 5 legacy feed_* tests replaced |
| LP-BE-405 | LPK-86 / — | Weight price calculation | 3 | M2 | 403 | done | 515e31b | parts/test_calculate_price.py: no permission -> 403 weight_entry_not_allowed, foreign search/unknown offer -> 404, fresh supplier price with the customer weight (weight_source=customer), disclaimer rendered in the request language and not stored, invalid_weight/required; cart add of the result in LP-BE-502 tests; 3 legacy calculate_part_price_* replaced |
| LP-BE-406 | LPK-86 / — | Quote requests & public prepared-quote link | 3 | M2 | 403 | done | 6a95376 | parts/test_quote_requests.py: duplicate open request returns the existing one (200), foreign/unknown search or offer 404, PV/IsCustomer, manual needs can_request_quote (403), per-type required fields, own list paginated, public /q/ only when ready and without customer data; 10 legacy tests replaced |
| LP-BE-407 | LPK-86 / — | Attach guest search logs on login | 1 | M2 | 205, 403 | done | a9f46ee | parts/test_guest_attach.py: guest search appears in history after login and registration, usable for a quote request in the new session; other browsers' and other customers' logs untouched |
| LP-BE-501 | LPK-87 / — | Cart models & cart resolution | 3 | M2 | 303, 401 | done | 6955b2d | cart/test_models.py: guest and customer get distinct carts, the same session resolves the same guest cart, one cart per customer/session, owner required; account deletion deletes the cart |
| LP-BE-502 | LPK-87 / — | Add to cart by reference (never by price) | 5 | M2 | 501, 403, 406 | done | dbf3375 | cart/test_add.py: unit_price/final_price_gel in the body have no effect, another session's search_id -> 404, expired -> 409 offer_expired, checking -> 409 offer_not_ready, sum + cap 99 (invalid_quantity), prepared quote (owned, ready), currency repricing, weight-calc result keeps the customer weight (LP-BE-405 AC), in_cart_quantity; 3 legacy add tests replaced |
| LP-BE-503 | LPK-87 / — | Cart read & line operations | 5 | M2 | 502 | done | c2c8c7f | cart/test_cart.py: exact Cart/CartItem keys, totals incl. discount, expired + unavailable block (price_changed doesn't), PATCH quantity/VIN (invalid_vin, invalid_quantity, "" clears), DELETE, foreign lines 404, apply-vin, /me cart_count = sum of quantities, payment_terms by tariff, merge once; last 4 legacy cart tests replaced (cart/tests.py removed) |
| LP-BE-504 | LPK-87 / — | Cart currency switch | 3 | M2 | 503 | done | ae20095 | cart/test_currency.py: GEL -> USD -> GEL returns the same GEL prices (same rate), USD prices = engine output incl. fixed GEL discount converted, validity/status kept, unsupported or inactive currency -> validation_error, guest without a cart |
| LP-BE-505 | LPK-87 / — | Refresh a cart line | 3 | M2 | 503, 402 | done | 9b4f5ed | cart/test_refresh.py: same price -> ok + new validity + new log, supplier price change -> price_changed with previous_unit_price, offer gone -> unavailable + checkout blocked, same-brand fallback, supplier failure -> 502 with the line unchanged, foreign line 404, customer weight kept |
| LP-BE-506 | LPK-87 / — | Guest cart merge on login/registration | 5 | M2 | 503, 205, 206 | done | e021427 | cart/test_merge.py: guest 1x A + customer 1x A -> 2x A with summed {PART-A, 1, 2}, merge once then null, partner sees tariff prices (price_changed), cap 99, guest currency kept for a new cart, no guest cart -> no-op, registration merges, rate outage doesn't break login (problems) |
| LP-BE-601 | LPK-88 / — | Order & OrderItem schema and core services | 5 | M3 | 301, 501 | todo |  |  |
| LP-BE-602 | LPK-88 / — | Checkout options | 3 | M3 | 601, 503 | todo |  |  |
| LP-BE-603 | LPK-88 / — | Create order (checkout) | 8 | M3 | 602, 701 | todo |  |  |
| LP-BE-604 | LPK-88 / — | Invoice HTML update | 3 | M3 | 601 | todo |  |  |
| LP-BE-701 | LPK-89 / — | Payment model, provider contract, FakeProvider | 3 | M3 | 601 | todo |  |  |
| LP-BE-702 | LPK-89 / — | `mark_payment` service | 5 | M3 | 701 | todo |  |  |
| LP-BE-703 | LPK-89 / — | Start payments & order payment endpoints | 5 | M3 | 702 | todo |  |  |
| LP-BE-704 | LPK-89 / — | Provider callback endpoint | 2 | M3 | 702 | todo |  |  |
| LP-BE-705 | LPK-89 / — | Invoice payments & manual confirmation | 3 | M3 | 702, 604 | todo |  |  |
| LP-BE-706 | LPK-89 / — | Payment reconciliation job | 3 | M3 | 703 | todo |  |  |
| LP-BE-801 | LPK-90 / — | Order serializers (list & detail) | 5 | M4 | 601, 702, 1304 | todo |  |  |
| LP-BE-802 | LPK-90 / — | Orders list: groups, counts, priority | 3 | M4 | 801 | todo |  |  |
| LP-BE-803 | LPK-90 / — | Customer decision endpoint | 5 | M4 | 801 | todo |  |  |
| LP-BE-804 | LPK-90 / — | Customer order cancellation | 3 | M4 | 801, 702 | todo |  |  |
| LP-BE-805 | LPK-90 / — | Courier fee accept/decline | 3 | M4 | 801 | todo |  |  |
| LP-BE-806 | LPK-90 / — | Order support messages (adapted) | 2 | M4 | 801 | todo |  |  |
| LP-BE-807 | LPK-90 / — | Demo order endpoints | 2 | M4 | 803, 805, 106 | todo |  |  |
| LP-BE-901 | LPK-91 / — | Notification models & channel settings | 2 | M5 | 601 | todo |  |  |
| LP-BE-902 | LPK-91 / — | `notify()` and delivery | 5 | M5 | 901, 207, 203, 1305 | todo |  |  |
| LP-BE-903 | LPK-91 / — | Wire notification triggers | 3 | M5 | 902, 1103, 1104, 702 | todo |  |  |
| LP-BE-904 | LPK-91 / — | Notification endpoints, badges, public link | 3 | M5 | 902 | todo |  |  |
| LP-BE-1001 | LPK-92 / — | Support tickets (guest & customer) | 3 | M5 | 902, 104 | todo |  |  |
| LP-BE-1002 | LPK-92 / — | Operator replies (tickets & order messages) | 2 | M5 | 1001, 903 | todo |  |  |
| LP-BE-1101 | LPK-93 / — | Configuration admin | 5 | M2 | 301, 401 | done | ba69294 | pricing/tests/test_admin.py: USD markup (tariff inline) + USD VAT edited in admin change the next USD price; SiteSettings single row, no add/delete, translated inputs, delivery JSON validated; NBG fetch-now action; read-only rates; discount validation; PartImage/PickupPoint pages. NotificationChannelSetting admin follows its model in LP-BE-901/1305 (M5) |
| LP-BE-1102 | LPK-93 / — | Customer admin | 2 | M5† | 213 | todo |  |  |
| LP-BE-1103 | LPK-93 / — | Order & item admin rewired to services | 5 | M4 | 801, 705 | todo |  |  |
| LP-BE-1104 | LPK-93 / — | Quote request admin: price ready | 3 | M4† | 406, 303 | todo |  |  |
| LP-BE-1105 | LPK-93 / — | Payment & delivery log admin | 2 | M5† | 705, 902 | todo |  |  |
| LP-BE-1201 | LPK-94 / — | Remove legacy endpoints and dead code | 3 | M6 | all functional epics | todo |  |  |
| LP-BE-1202 | LPK-94 / — | Production settings & security review | 2 | M6 | 1201 | todo |  |  |
| LP-BE-1203 | LPK-94 / — | API contract tests | 3 | M6 | 1201 | todo |  |  |
| LP-BE-1204 | LPK-94 / — | Staging environment & operations docs | 3 | M6 | 1202 | todo |  |  |
| LP-BE-1205 | LPK-94 / — | Frontend handoff package | 1 | M6 | 1203, 1204 | todo |  |  |
| LP-BE-1301 | LPK-95 / LPK-166 | i18n foundation: languages, middleware, catalogs | 3 | M0 | 101 | done | 24ebd86 | PR #58, CI green (PostgreSQL 16) |
| LP-BE-1302 | LPK-95 / — | Translated content fields & admin widget | 5 | M1 | 1301 | done | c146b63, 1e462aa | api/tests/test_translated_fields.py (tr fallback, +1 language -> +1 input, default required); cities/districts editor ships with SiteSettings in LP-BE-301/1101 (plan G4) |
| LP-BE-1303 | LPK-95 / — | Customer preferred language | 2 | M1 | 201, 1301 | done | 40adde6 | customer_language() (accounts/test_services.py); Accept-Language en at registration -> en (test_register_verify.py); PATCH xx -> invalid (test_profile.py) |
| LP-BE-1304 | LPK-95 / — | Localized system texts (events, timeline, banners, system messages) | 5 | M3 | 601, 1301 | todo |  |  |
| LP-BE-1305 | LPK-95 / — | Notification & SMS templates per language | 3 | M5 | 901, 1303 | todo |  |  |
| LP-BE-1306 | LPK-95 / — | Translation pass & language QA | 3 | M6 | 1201 | todo |  |  |

## Milestone log

| Milestone | Branch | Status | Audit |
|---|---|---|---|
| M0 (Epic 1 + 1301) | `feature/lpk-83-platform-foundation` | done, PR #58 open | outbox fix `09338f1` |
| M1 | `feature/m1-accounts-auth` | done, merged into main (PR #59) | Ponytail audit 2026-11-03: no must-fix; see the M1 checkpoint report |
| M2 | `feature/m2-pricing-search-cart` (PR #60) | done (18/18 stories) + audit fix `a585dab` + review fixes `acdbf76`, `409c538`, `aeccb03` + owner pricing extension `bd7f995` (business days), `f8a0664` (canonical USD, currency mode, FX) ; not merged | D15/D16; awaiting fresh PostgreSQL CI and owner review |

