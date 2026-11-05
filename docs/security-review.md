# Security review (LP-BE-1202, M6)

Scope: the Django/DRF backend on `feature/m6-final-hardening` (after M0–M5). Server, proxy and frontend
configuration are out of scope (no server was changed); what they must do is in `docs/operations.md`.

## 1. `manage.py check --deploy`

Run with production values (`DEBUG=False`, a 50+ character random `SECRET_KEY`, real `ALLOWED_HOSTS`, demo
off, SMTP mail, https origins). `DEPLOY_ENV=production` itself cannot boot yet (it refuses
`PAYMENT_PROVIDER=fake` and no real adapter exists: release blocker B1), so the check ran as `staging`
with every production security value set.

| Warning | Status |
|---|---|
| `security.W005` `SECURE_HSTS_INCLUDE_SUBDOMAINS` not set | **Justified, owner decision.** It forces HTTPS on every subdomain of the cookie domain (e.g. `api-test.lionparts.ge`). Configurable: `SECURE_HSTS_INCLUDE_SUBDOMAINS=True` once every subdomain is HTTPS-only. |
| `security.W021` `SECURE_HSTS_PRELOAD` not set | **Justified, owner decision** (browser preload lists are hard to undo). Configurable: `SECURE_HSTS_PRELOAD=True`. |

Everything else is clean: `DEBUG` off, strong key, secure + HttpOnly session cookie, secure CSRF cookie,
SSL redirect, HSTS one year, `X-Frame-Options: DENY`, nosniff, `Referrer-Policy: same-origin`.

## 2. Findings F1–F16 (`backend-review.md`)

| # | Finding | Status | Evidence |
|---|---|---|---|
| F1 | Client sets the price | **Closed.** Cart lines are added by reference (search log / offer / quote); prices come from the engine; checkout re-validates and freezes server prices (D15, D16). | `cart/test_add.py`, `orders/test_checkout.py::test_client_prices_rates_and_states_are_ignored` |
| F2 | Weak, non-expiring `session_id` | **Closed.** The legacy API is removed (LP-BE-1201); Django sessions (HttpOnly, Secure, SameSite=Lax, 30 days), rotated at login, flushed at logout and on account deletion. | `accounts/test_login.py`, `api/tests/test_legacy.py` |
| F3 | No rate limiting | **Closed.** OTP per phone and IP, login per IP and email, password reset, email sending, registration, search, support (guest per IP, customer per user), public links per IP. | `api/throttles.py`, `accounts/test_otp.py`, `accounts/test_login.py`, `orders/test_support_tickets.py`, `orders/test_notification_api.py` |
| F4 | `demo-login` always on | **Closed.** Removed (G§8.11); route asserted 404. Demo endpoints exist only with `ENABLE_DEMO_ENDPOINTS`, need staff + model permissions, and production refuses the flag. | `api/tests/test_legacy.py`, `orders/test_demo.py`, `config/tests.py` |
| F5 | `resolve-action` without `action_required` | **Closed.** Decisions validate the pending action and the decision matrix under row locks (`409 no_pending_action`). | `orders/test_decisions.py` |
| F6 | Account enumeration | **Closed.** Login failures have one body; password reset answers the same for unknown emails and sends nothing. | `accounts/test_login.py::test_every_failure_has_the_same_body`, `accounts/test_password_reset.py` |
| F7 | Order number collisions | **Closed.** `LP-{year}-{pk:05d}`, assigned inside the creating transaction. | `orders/test_services.py::OrderNumberTests` |
| F8 | Unvalidated cart input → 500 | **Closed.** Serializers with bounded quantity; prices/ETA/weight are never taken from the client. | `cart/test_cart.py`, `cart/test_add.py` |
| F9 | AMT TLS verification off | **Closed.** `AMT_VERIFY_SSL` defaults to True and production refuses False. Supplier credentials stay in the SOAP body (protocol requirement; never logged). | `config/tests.py::test_production_refuses_each_unsafe_switch` |
| F10 | No production hardening | **Closed.** HTTPS redirect, HSTS, secure cookies, JSON renderer only, fail-closed `DEPLOY_ENV` guard (debug, demo, demo SMS codes, demo parts/payment providers, non-delivering mail backends, weak `SECRET_KEY`, AMT TLS off, http `FRONTEND_BASE_URL` / `CSRF_TRUSTED_ORIGINS`, `ALLOWED_HOSTS=*`). | `config/tests.py`, `api/tests/test_log_secrets.py::SecurityHeaderTests` |
| F11 | `demo_code` tied to `SENDER_GE_ENABLED` | **Closed.** Only with `SMS_DEMO_MODE` and (`DEBUG` or demo endpoints); production refuses both. Disabled SMS never fails silently (D11). | `accounts/test_otp.py`, `config/tests.py` |
| F12 | Public notification links leak PII, never expire | **Closed (D23).** Minimal payload, no operator text, 30-day expiry, identical 404s, IP throttle, tokens masked in Django logs. | `orders/test_notification_api.py::PublicLinkTests` |
| F13 | Custom password policy | **Closed.** Django `AUTH_PASSWORD_VALIDATORS` (incl. common passwords) plus the complexity validator on registration, reset and change. | `accounts/test_register.py`, `accounts/test_password_reset.py`, `accounts/test_profile.py` |
| F14 | No pagination, N+1 | **Closed.** Paginated lists; order list/detail with a constant number of queries. | `orders/test_order_api.py::QueryCountTests` |
| F15 | No real payment flow | **Partly closed.** Payment model, provider contract, callbacks, reconciliation, invoices and manual refunds exist; only `FakeProvider` is implemented. **Release blocker B1.** | `payments/test_*.py` |
| F16 | Duplicated helpers | **Closed.** One phone normalizer (`accounts/validators.py`), one OTP service (`accounts/otp.py`), one price snapshot (`pricing/engine.py`). | — |

## 3. Controls reviewed in M6

- **Authentication / CSRF:** session auth with CSRF on every unsafe method, anonymous included (`api/auth.py`); JSON 403 `csrf_failed`. Staff accounts have no `Customer` and never pass customer permissions.
- **Permissions / isolation:** every customer query is scoped by `request.user.customer`; foreign objects are 404 (orders, items, payments, notifications, support messages, tickets, quotes, cart lines). Admin models holding money or delivery logs are read-only; operator actions need model permissions and are logged.
- **CORS:** explicit `CORS_ALLOWED_ORIGINS` with credentials; no wildcard.
- **Logging (changed in M6):** one stdout handler for every logger, INFO by default (`LOG_LEVEL`); public link tokens masked on the handler; `django.template` and `django.db.backends` pinned to INFO, because their DEBUG records contain template contexts (password reset links) and SQL parameters. A regression test drives registration, login, password reset, SMS provider failure, email failure and public links and asserts that no OTP code, password, reset token, API key, message text or link token is logged.
- **Email layout fix (found by that test):** emails declared `lang="ka"` whatever their language; they now use the active language.
- **Payments / refunds:** `mark_payment` is the only status changer (locks, idempotent); callbacks need the matching provider and a verified adapter answer; refunds are recorded, capped at what is owed and what was collected (D20); row locks are `FOR NO KEY UPDATE` (D26). Covered by the M3/M4 tests, including the PostgreSQL concurrency tests.
- **Notifications:** sent only after commit, deduplicated under a row lock, truthful delivery status (D25), templates limited to `if/else` and primitive values (D24).
- **Proxy trust:** `NUM_PROXIES` must be 1 behind nginx; with 0 every client shares one throttle bucket (the proxy's address). Cannot be detected in code; part of the deployment checklist.
- **Proxy / access logs:** Django masks public link tokens, but nginx and gunicorn access logs record full URLs. Masking there is a deployment requirement (`docs/operations.md`).

## 4. Release blockers (not production-ready)

| # | Blocker | Needed |
|---|---|---|
| B1 | **No real card payment provider.** Only `FakeProvider` exists; Flitt (or another provider) is not implemented, its USD/GEL support, callback signatures and refunds are unverified. Production refuses `PAYMENT_PROVIDER=fake`, so production cannot start. | An owner-approved payment integration (adapter, sandbox verification, signature and amount/currency checks), or an explicitly approved invoice-only launch with card payments switched off (not built). |
| B2 | **No in-place migration from the legacy databases.** The v1 migrations are fresh (`docs/migration-history.md`); `lion_parts` / `lion_parts_v2` cannot be migrated in place. | An owner-approved data migration plan (export/transform/import or a clean start) executed on a fresh database. |
| B3 | **Native-speaker sign-off** for ka/ru (LP-BE-1306 acceptance). | Review by native speakers. |
| B4 | **Operations items** in `docs/operations.md` (access-log masking, `NUM_PROXIES`, cron jobs, backups, HSTS policy) and **disabling any old copy of `scripts/deploy-staging.sh` on the legacy server** (the repository copy is disabled since M6; old checkouts on a server are not). | Done by an authorized operator; not verified here. |
| B5 | **Payment lifecycle review (Jira LPK-219, Epic LPK-178).** Provider-side session handling is unverified. | Resolve LPK-219 before real card payments are enabled. |
| B6 | **Django Admin exposure (LPK-182).** | The Django Admin must not be publicly exposed without an approved nginx IP/VPN allow-list and additional nginx rate limiting (`docs/operations.md` §5). Admin 2FA is a separate pending task (LPK-188/LPK-189). |
| B7 | **`gthread` with real AMT (Jira LPK-220).** | No `gthread` with real AMT until LPK-220 is fixed and tested. |

## 5. Post-review changes (LPK-172)

Implemented on the post-review branch; in review, GitHub CI pending, not merged.

| Story | Area | What changed | Evidence |
|---|---|---|---|
| FIX-01 (LPK-179) | Duplicate payment protection | A payment that may still complete blocks a new one (`409 payment_in_progress`), under the existing order row lock. Status transitions are guarded so a late provider answer cannot reopen a closed payment. | `payments/test_duplicates.py`, `payments/test_endpoints.py`, `payments/test_mark_payment.py` |
| FIX-01b (LPK-180) | Payment reconciliation | `reconcile_payments` and the admin action "Re-check with provider" (`payments.change_payment`) share one service. The provider is called outside any transaction, results apply only through `mark_payment`, unresolved results stay blocked, the audit trail is kept, and provider errors are logged by id and type only. | `payments/test_reconcile.py`, `payments/test_admin.py` |
| FIX-02 (LPK-181) | Email | SMTP timeout `EMAIL_TIMEOUT` (default 10 s; 0 or a negative value stops startup). `send_templated_email` returns False on failure and logs the template and error type only. | `api/tests/test_mail.py`, `config/tests.py` |
| FIX-03 (LPK-182) | Django Admin login | Failed logins are limited to 10 per IP and 5 per username in 15 minutes, answering `429` without checking the credentials. Only a keyed hash of the username is stored or logged. | `api/tests/test_admin_login.py` |
| FIX-04 (LPK-183) | Database and process model | `DB_CONN_MAX_AGE` (default 60) and connection health checks. `gthread` guidance for isolated staging tests. A review of shared state found no defects to fix in this change; staging load tests remain to be run. | `config/tests.py`, `api/tests/test_thread_safety.py` |

**Deployment requirements:**

- The Django Admin must sit behind an approved nginx IP/VPN allow-list and nginx rate limiting (B6, `docs/operations.md` §5).
- `NUM_PROXIES` must match the proxies in front of gunicorn (`docs/operations.md` §5).
- An `unknown` payment is resolved only by provider confirmation or an explicitly authorized, auditable escalation process. No manual status changes through a shell, the admin or direct database edits (`docs/operations.md` §10).
- Only `FakeProvider` exists (B1), so this has not run against a real provider.

**Tracked separately:**

| Jira | Scope | Blocker |
|---|---|---|
| LPK-219 (Epic LPK-178) | Payment lifecycle review | B5: required before real card payments |
| LPK-220 | Supplier lookup limits and external calls within database transactions | B7: required before `gthread` with real AMT |
| LPK-221 | Authentication and OTP throttling under concurrency | |
| LPK-222 | OTP resend cooldown under concurrency | |
| LPK-188, LPK-189 | Admin 2FA, pending owner approval | |

Detailed review notes are held privately by the project owner.
