# Progress tracker

One row per backlog story (76). Status: `done` (committed, tests green), `in progress`, `todo`.
"M0" = Epic 1 foundation (PR #58). LPK keys of Epics 2–13 are filled in by the project owner
(master plan D7). Evidence = commit(s) + the test modules that prove the acceptance criteria.

| Story | LPK | Title | Pts | Milestone | Depends on | Status | Commit | Evidence |
|---|---|---|---|---|---|---|---|---|
| LP-BE-101 | LPK-96 | Settings & configuration baseline | 3 | M0 | — | done | bd996df | PR #58, CI green (PostgreSQL 16) |
| LP-BE-102 | LPK-97 | Error envelope, ApiError and translated messages | 5 | M0 | 101, 1301 | done | 458a2fd | PR #58, CI green (PostgreSQL 16) |
| LP-BE-103 | LPK-98 | Session authentication with CSRF for all unsafe requests + permissions | 3 | M0 | 102 | done | 59cba2f | PR #58, CI green (PostgreSQL 16) |
| LP-BE-104 | LPK-99 | Throttle classes | 3 | M0 | 101 | done | a02d675, d73336a | PR #58, CI green (PostgreSQL 16) |
| LP-BE-105 | LPK-100 | Pagination, meta and health | 2 | M0 | 102 | done | df3fbf3 | PR #58, CI green (PostgreSQL 16) |
| LP-BE-106 | LPK-101 | Demo endpoints switch & email outbox | 2 | M0 | 101 | done | 8b1b11d, 09338f1 | PR #58, CI green (PostgreSQL 16) |
| LP-BE-107 | LPK-102 | CI update | 1 | M0 | 101 | done | 0816312 | PR #58, CI green (PostgreSQL 16) |
| LP-BE-201 | TBD | Account models, fresh migrations, seed | 5 | M1 | 101 | done |  | accounts/test_models.py; legacy accounts views/serializers/urls deleted (routes frozen in api/legacy.py); orders migrations repointed; D4 shim |
| LP-BE-202 | TBD | Validators & normalizers | 2 | M1 | 201 | done |  | accounts/test_validators.py (phone formats incl. AC cases, IDs, VIN, part number, every password code) |
| LP-BE-203 | TBD | OTP service & SMS wrapper | 5 | M1 | 202 | done |  | accounts/test_otp.py: 5 wrong -> attempts_left 0 then otp_attempts_exceeded; 409 resend_in; demo_code matrix; sms_failed stores nothing; parallel guesses (PostgreSQL/CI) |
| LP-BE-204 | TBD | Registration step 1 (data + SMS) and resend | 5 | M1 | 203, 104 | todo |  |  |
| LP-BE-205 | TBD | Registration step 2 (verify phone → create account → login) | 5 | M1 | 204, 207 | todo |  |  |
| LP-BE-206 | TBD | Login, logout, me, csrf | 3 | M1 | 103, 201 | todo |  |  |
| LP-BE-207 | TBD | Email infrastructure | 2 | M1 | 101, 1301, 1303 | done |  | api/tests/test_mail.py: 4 templates reach the outbox with links; ru email + lang=ru; SMTP failure -> False + log; every template translated ka/ru |
| LP-BE-208 | TBD | Email verification (send & verify) | 3 | M1 | 207, 206 | todo |  |  |
| LP-BE-209 | TBD | Password reset (request & confirm) | 3 | M1 | 207 | todo |  |  |
| LP-BE-210 | TBD | Profile: read, update, change password | 2 | M1 | 206 | todo |  |  |
| LP-BE-211 | TBD | Email change | 3 | M1 | 207 | todo |  |  |
| LP-BE-212 | TBD | Phone change | 3 | M1 | 203 | todo |  |  |
| LP-BE-213 | TBD | Account deletion (anonymize) + retention command | 5 | M1 | 210 (open-orders check becomes real after LP-BE-601) | todo |  |  |
| LP-BE-301 | TBD | Pricing app & models | 3 | M2 | 201, 1302 | todo |  |  |
| LP-BE-302 | TBD | NBG exchange rate client & job | 3 | M2 | 301 | todo |  |  |
| LP-BE-303 | TBD | Price engine & snapshots | 5 | M2 | 302, 304 | todo |  |  |
| LP-BE-304 | TBD | Discount engine | 3 | M2 | 301 | todo |  |  |
| LP-BE-401 | TBD | Parts model changes & part images | 3 | M2 | 201, 301 | todo |  |  |
| LP-BE-402 | TBD | Offer builder (AMT + demo) on the price engine | 5 | M2 | 303, 401 | todo |  |  |
| LP-BE-403 | TBD | Search endpoint | 3 | M2 | 402, 104 | todo |  |  |
| LP-BE-404 | TBD | Search history, seen, clear | 8 | M2 | 403, 406 | todo |  |  |
| LP-BE-405 | TBD | Weight price calculation | 3 | M2 | 403 | todo |  |  |
| LP-BE-406 | TBD | Quote requests & public prepared-quote link | 3 | M2 | 403 | todo |  |  |
| LP-BE-407 | TBD | Attach guest search logs on login | 1 | M2 | 205, 403 | todo |  |  |
| LP-BE-501 | TBD | Cart models & cart resolution | 3 | M2 | 303, 401 | todo |  |  |
| LP-BE-502 | TBD | Add to cart by reference (never by price) | 5 | M2 | 501, 403, 406 | todo |  |  |
| LP-BE-503 | TBD | Cart read & line operations | 5 | M2 | 502 | todo |  |  |
| LP-BE-504 | TBD | Cart currency switch | 3 | M2 | 503 | todo |  |  |
| LP-BE-505 | TBD | Refresh a cart line | 3 | M2 | 503, 402 | todo |  |  |
| LP-BE-506 | TBD | Guest cart merge on login/registration | 5 | M2 | 503, 205, 206 | todo |  |  |
| LP-BE-601 | TBD | Order & OrderItem schema and core services | 5 | M3 | 301, 501 | todo |  |  |
| LP-BE-602 | TBD | Checkout options | 3 | M3 | 601, 503 | todo |  |  |
| LP-BE-603 | TBD | Create order (checkout) | 8 | M3 | 602, 701 | todo |  |  |
| LP-BE-604 | TBD | Invoice HTML update | 3 | M3 | 601 | todo |  |  |
| LP-BE-701 | TBD | Payment model, provider contract, FakeProvider | 3 | M3 | 601 | todo |  |  |
| LP-BE-702 | TBD | `mark_payment` service | 5 | M3 | 701 | todo |  |  |
| LP-BE-703 | TBD | Start payments & order payment endpoints | 5 | M3 | 702 | todo |  |  |
| LP-BE-704 | TBD | Provider callback endpoint | 2 | M3 | 702 | todo |  |  |
| LP-BE-705 | TBD | Invoice payments & manual confirmation | 3 | M3 | 702, 604 | todo |  |  |
| LP-BE-706 | TBD | Payment reconciliation job | 3 | M3 | 703 | todo |  |  |
| LP-BE-801 | TBD | Order serializers (list & detail) | 5 | M4 | 601, 702, 1304 | todo |  |  |
| LP-BE-802 | TBD | Orders list: groups, counts, priority | 3 | M4 | 801 | todo |  |  |
| LP-BE-803 | TBD | Customer decision endpoint | 5 | M4 | 801 | todo |  |  |
| LP-BE-804 | TBD | Customer order cancellation | 3 | M4 | 801, 702 | todo |  |  |
| LP-BE-805 | TBD | Courier fee accept/decline | 3 | M4 | 801 | todo |  |  |
| LP-BE-806 | TBD | Order support messages (adapted) | 2 | M4 | 801 | todo |  |  |
| LP-BE-807 | TBD | Demo order endpoints | 2 | M4 | 803, 805, 106 | todo |  |  |
| LP-BE-901 | TBD | Notification models & channel settings | 2 | M5 | 601 | todo |  |  |
| LP-BE-902 | TBD | `notify()` and delivery | 5 | M5 | 901, 207, 203, 1305 | todo |  |  |
| LP-BE-903 | TBD | Wire notification triggers | 3 | M5 | 902, 1103, 1104, 702 | todo |  |  |
| LP-BE-904 | TBD | Notification endpoints, badges, public link | 3 | M5 | 902 | todo |  |  |
| LP-BE-1001 | TBD | Support tickets (guest & customer) | 3 | M5 | 902, 104 | todo |  |  |
| LP-BE-1002 | TBD | Operator replies (tickets & order messages) | 2 | M5 | 1001, 903 | todo |  |  |
| LP-BE-1101 | TBD | Configuration admin | 5 | M2 | 301, 401 | todo |  |  |
| LP-BE-1102 | TBD | Customer admin | 2 | M5 | 213 | todo |  |  |
| LP-BE-1103 | TBD | Order & item admin rewired to services | 5 | M4 | 801, 705 | todo |  |  |
| LP-BE-1104 | TBD | Quote request admin: price ready | 3 | M4 | 406, 303 | todo |  |  |
| LP-BE-1105 | TBD | Payment & delivery log admin | 2 | M5 | 705, 902 | todo |  |  |
| LP-BE-1201 | TBD | Remove legacy endpoints and dead code | 3 | M6 | all functional epics | todo |  |  |
| LP-BE-1202 | TBD | Production settings & security review | 2 | M6 | 1201 | todo |  |  |
| LP-BE-1203 | TBD | API contract tests | 3 | M6 | 1201 | todo |  |  |
| LP-BE-1204 | TBD | Staging environment & operations docs | 3 | M6 | 1202 | todo |  |  |
| LP-BE-1205 | TBD | Frontend handoff package | 1 | M6 | 1203, 1204 | todo |  |  |
| LP-BE-1301 | LPK-166 | i18n foundation: languages, middleware, catalogs | 3 | M0 | 101 | done | 24ebd86 | PR #58, CI green (PostgreSQL 16) |
| LP-BE-1302 | TBD | Translated content fields & admin widget | 5 | M1 | 1301 | done |  | api/tests/test_translated_fields.py (tr fallback, +1 language -> +1 input, default required); cities/districts editor ships with SiteSettings in LP-BE-301/1101 (plan G4) |
| LP-BE-1303 | TBD | Customer preferred language | 2 | M1 | 201, 1301 | in progress |  | model field (201) + customer_language() (accounts/test_services.py); API ACs land in 205/210 |
| LP-BE-1304 | TBD | Localized system texts (events, timeline, banners, system messages) | 5 | M3 | 601, 1301 | todo |  |  |
| LP-BE-1305 | TBD | Notification & SMS templates per language | 3 | M5 | 901, 1303 | todo |  |  |
| LP-BE-1306 | TBD | Translation pass & language QA | 3 | M6 | 1201 | todo |  |  |

## Milestone log

| Milestone | Branch | Status | Audit |
|---|---|---|---|
| M0 (Epic 1 + 1301) | `feature/lpk-83-platform-foundation` | done, PR #58 open | outbox fix `09338f1` |
| M1 | `feature/m1-accounts-auth` | in progress | |

