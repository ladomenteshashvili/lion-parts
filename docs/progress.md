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
| LP-BE-301 | LPK-85 / — | Pricing app & models | 3 | M2 | 201, 1302 | done |  | pricing/tests/test_models.py: seed (GEL/USD VAT 18, pickup point, Tbilisi districts), singleton refuses a second row, discount normalize/validation, /api/meta/ en/ru names, active-only currencies/points, latest rate |
| LP-BE-302 | LPK-85 / — | NBG exchange rate client & job | 3 | M2 | 301 | done |  | pricing/tests/test_rates.py: recorded NBG response -> 2.5971 for 2026-10-24, quantity division + upsert, stale+down -> RateUnavailable (502 rate_unavailable), fresh -> no HTTP, idempotent command; requests mocked |
| LP-BE-303 | LPK-85 / — | Price engine & snapshots | 5 | M2 | 302, 304 | todo |  |  |
| LP-BE-304 | LPK-85 / — | Discount engine | 3 | M2 | 301 | done |  | pricing/tests/test_discounts.py: 5% customer vs 10% part -> 10%, fixed 50 GEL at 2.5 -> 20.00 USD, tie -> lower id, guests get no customer/tariff rules, inactive/expired/future ignored, cap, rounding |
| LP-BE-401 | LPK-86 / — | Parts model changes & part images | 3 | M2 | 201, 301 | todo |  |  |
| LP-BE-402 | LPK-86 / — | Offer builder (AMT + demo) on the price engine | 5 | M2 | 303, 401 | todo |  |  |
| LP-BE-403 | LPK-86 / — | Search endpoint | 3 | M2 | 402, 104 | todo |  |  |
| LP-BE-404 | LPK-86 / — | Search history, seen, clear | 8 | M2 | 403, 406 | todo |  |  |
| LP-BE-405 | LPK-86 / — | Weight price calculation | 3 | M2 | 403 | todo |  |  |
| LP-BE-406 | LPK-86 / — | Quote requests & public prepared-quote link | 3 | M2 | 403 | todo |  |  |
| LP-BE-407 | LPK-86 / — | Attach guest search logs on login | 1 | M2 | 205, 403 | todo |  |  |
| LP-BE-501 | LPK-87 / — | Cart models & cart resolution | 3 | M2 | 303, 401 | todo |  |  |
| LP-BE-502 | LPK-87 / — | Add to cart by reference (never by price) | 5 | M2 | 501, 403, 406 | todo |  |  |
| LP-BE-503 | LPK-87 / — | Cart read & line operations | 5 | M2 | 502 | todo |  |  |
| LP-BE-504 | LPK-87 / — | Cart currency switch | 3 | M2 | 503 | todo |  |  |
| LP-BE-505 | LPK-87 / — | Refresh a cart line | 3 | M2 | 503, 402 | todo |  |  |
| LP-BE-506 | LPK-87 / — | Guest cart merge on login/registration | 5 | M2 | 503, 205, 206 | todo |  |  |
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
| LP-BE-1101 | LPK-93 / — | Configuration admin | 5 | M2 | 301, 401 | todo |  |  |
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
| M1 | `feature/m1-accounts-auth` | done (15/15 stories) + review fixes (`52ab640` DEPLOY_ENV, `2c7781b` audit fixes); approved for push and CI, not merge | Ponytail audit 2026-11-03: no must-fix; see the M1 checkpoint report |

