# Operations runbook (LP-BE-1204)

For whoever deploys and runs the Lion Parts v1 backend. Nothing here has been executed on a server by the
development agent: no environment was provisioned, migrated or changed. Security background:
`docs/security-review.md`.

## 0. Status before you start

**Not production-ready.** Release blockers (details in `docs/security-review.md` §4):

- **B1 – no real card payment provider.** Only `FakeProvider` exists. `DEPLOY_ENV=production` refuses
  `PAYMENT_PROVIDER=fake`, and `PAYMENT_PROVIDER=flitt` has no adapter yet, so a production process does
  not start. This is intentional (fail closed).
- **B2 – no in-place migration of the legacy databases.** v1 has fresh migrations. Never run `migrate` with
  v1 code against `lion_parts` or `lion_parts_v2` (or any database created by the legacy app).
- **B3 – native-speaker sign-off** of the ka/ru texts.
- **B4 – the operations items below** (access-log masking, `NUM_PROXIES`, cron, backups, HSTS policy).
- **B5 – LPK-219, second-payment risk.** Must be resolved before real card payments are enabled.
- **B6 – Django Admin exposure.** The admin must not be publicly exposed without an approved nginx IP/VPN allow-list
  and additional nginx rate limiting (§5).
- **B7 – `gthread` with real AMT (LPK-220).** No `gthread` with real AMT until LPK-220 is fixed and tested (§4).

> ⚠️ **`scripts/deploy-staging.sh` belongs to the legacy installation and is disabled in this repository.**
> It checked out `main` (now v1) and ran `migrate` in `/home/lado/lion-parts`, which would apply the v1
> migrations to the legacy staging database (B2). Since M6 the repository copy stops at once with an error
> (exit 1) before any command (regression test `backend/api/tests/test_deploy_script.py`).
> **Any OLD copy already present on a server (e.g. a checkout of an earlier commit in `/home/lado/lion-parts`)
> is still dangerous** until an authorized operator disables or removes it there; nothing on a server was
> changed. A v1 staging is built fresh (§2).

## 1. Architecture

- **App:** Django 6.1 + DRF, served by **gunicorn** (`config.wsgi:application`), Python 3.14.
- **Proxy:** **nginx** terminates HTTPS and proxies `/api/` and `/<ADMIN_URL_PATH>` to gunicorn; it serves
  `/static/` (`collectstatic` → `backend/staticfiles`) and `/media/` (`MEDIA_ROOT`, part images).
- **Database:** PostgreSQL 16+. The Django cache is a table in the same database (`createcachetable`);
  throttle counters live there, so limits hold across gunicorn workers.
- **External services:** AMT (parts supplier, SOAP), NBG (exchange rate), Sender.ge (SMS), SMTP (email),
  payment provider (B1).
- **Logs:** everything on stdout (collected by systemd/journald), INFO by default (`LOG_LEVEL`). Public link
  tokens are masked by Django; OTP codes, passwords, API keys and message texts are never logged.
- **Health:** `GET /api/health/` → `{"status": "ok", "service": "lion-parts-backend"}`. It is a **liveness**
  check (the process answers); it does not query the database. Monitor the database separately (§7).

## 2. Building a completely isolated fresh staging

Goal: a staging that shares **nothing** with `api-test.lionparts.ge` or the legacy installation.

1. **Host and user.** A new VM/container, or at least a new Unix user (e.g. `lionparts-staging`) with its own
   home. Do not reuse `/home/lado`, its `.env`, virtualenv, media or backups.
2. **Database.** A new PostgreSQL role and an **empty** database created for this staging only, e.g.:
   ```sql
   CREATE ROLE lp_staging LOGIN PASSWORD '<generated>';
   CREATE DATABASE lp_staging OWNER lp_staging ENCODING 'UTF8';
   ```
   Never point `DB_NAME` at `lion_parts`, `lion_parts_v2` or a copy of them.
3. **Domain and TLS.** A new hostname (e.g. `api-staging.lionparts.ge`) with its own certificate. Agree with the
   frontend company whether the frontend is same-origin or a sibling subdomain (cookies: `SESSION_COOKIE_DOMAIN`,
   `CSRF_COOKIE_DOMAIN`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`).
4. **Code.**
   ```bash
   git clone git@github.com:ladomenteshashvili/lion-parts.git && cd lion-parts && git checkout <release tag or main>
   cd backend && python3.14 -m venv venv && . venv/bin/activate && pip install -r requirements.txt
   sudo apt install gettext   # once per host (compilemessages)
   ```
5. **Configuration.** `cp .env.example .env`, then set the staging column of §3. Generate `SECRET_KEY` with
   `python -c "import secrets; print(secrets.token_urlsafe(50))"`. Keep `.env` mode 600, owned by the service user.
6. **Database objects** (on the empty database only):
   ```bash
   python manage.py check --deploy
   python manage.py migrate          # creates the schema and seeds tariffs, pickup point, cities, channels, templates
   python manage.py createcachetable
   python manage.py compilemessages --ignore=venv
   python manage.py collectstatic --noinput
   python manage.py createsuperuser  # the operator account; also the frontend team's staff login for demo endpoints
   python manage.py fetch_nbg_rates  # first exchange rate
   ```
7. **Mail.** Run **Mailpit** (SMTP on 1025, web UI on 8025) on the staging host; set `EMAIL_HOST=127.0.0.1`,
   `EMAIL_PORT=1025`, `EMAIL_USE_TLS=False`. Share the web UI (behind basic auth or VPN) with the frontend team.
8. **Processes:** gunicorn as a systemd service (§4), nginx site (§5), cron (§6).
9. **Smoke test** (frontend team, G§4.10): register (demo SMS code in the response, verification email in
   Mailpit), search, add to cart, checkout with card, finish the fake payment
   (`POST /api/payments/fake/<id>/complete/ {"status": "paid"}`), follow the order; operator steps through the
   demo endpoints or the admin.

## 3. Configuration checklist

Every variable is listed in `backend/.env.example`. Values that differ per environment:

| Variable | Production | Staging | Notes |
|---|---|---|---|
| `DEPLOY_ENV` | `production` | `staging` | Unset = production (fail closed). |
| `DEBUG` | `False` | `False` | Production refuses `True`. |
| `SECRET_KEY` | 50+ random chars | 50+ random chars | Production refuses short or placeholder keys. |
| `ALLOWED_HOSTS` | API hostname | staging API hostname | Never `*` (refused in production). |
| `FRONTEND_BASE_URL` | `https://lionparts.ge` | staging frontend URL | Links in SMS/email. Production requires `https://`. |
| `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS` | frontend origin(s) | staging frontend origin(s) | https only in production. |
| `DB_CONN_MAX_AGE` | `60` | `60` | Seconds a thread keeps its database connection. `0` closes it after every request; a negative value stops startup (§4). |
| `NUM_PROXIES` | `1` | `1` | **Required behind nginx.** With `0` every client shares one rate-limit bucket (nginx's address). |
| `ADMIN_URL_PATH` | a non-obvious path | any | Must match the nginx location. |
| `PARTS_PROVIDER` | `amt` | `amt` or `demo` | Production refuses `demo`. |
| `AMT_VERIFY_SSL` | `True` | `True` | Production refuses `False`. |
| `SENDER_GE_ENABLED` | `True` | `False` | Staging must not send real SMS. |
| `SMS_DEMO_MODE` | `False` | `True` | Demo OTP codes in responses; refused in production. |
| `ENABLE_DEMO_ENDPOINTS` | `False` | `True` | Refused in production and with a real SMS or payment provider. |
| `PAYMENT_PROVIDER` | real adapter (**B1**) | `fake` | Production refuses `fake`. |
| `EMAIL_BACKEND` | smtp | smtp (Mailpit) | Production refuses console/locmem/dummy/filebased. |
| `EMAIL_TIMEOUT` | `10` | `10` | SMTP connection/socket timeout in whole seconds (§4). With the SMTP backend, 0 or a negative value stops the app at startup. |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD` | owner decision | `False` | Only when every subdomain is HTTPS-only. |
| `LOG_LEVEL` | `INFO` | `INFO` | `DEBUG` only briefly for diagnosis. |
| `MEDIA_ROOT` | persistent path | persistent path | Back it up (§8). |

## 4. gunicorn

Example systemd unit (`/etc/systemd/system/lionparts-api.service`):

```ini
[Unit]
Description=Lion Parts API
After=network.target postgresql.service

[Service]
User=lionparts
WorkingDirectory=/srv/lion-parts/backend
EnvironmentFile=/srv/lion-parts/backend/.env
ExecStart=/srv/lion-parts/backend/venv/bin/gunicorn config.wsgi:application \
    --bind 127.0.0.1:8001 --worker-class gthread --workers 3 --threads 8 --timeout 60 --no-control-socket
# No --access-logfile: nginx writes the (masked) access log; gunicorn's would contain public link tokens.
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

This is a **starting configuration for isolated staging tests, not a production-ready guarantee or optimum**. It gives
3 × 8 = up to 24 request-handling threads. It is documentation only: no service configuration has been changed or
deployed. **Do not run `gthread` with real AMT until LPK-220 is fixed and tested (B7).**

### What `--timeout` does with `gthread`

**It is not a per-request deadline.** The worker's main thread reports to gunicorn, so a slow request in another thread
does not trigger the timeout. In a local test with `--timeout 3` and a request taking 7 s, `sync` killed the worker at
3 s and `gthread` let the request finish at 7 s. nginx may stop waiting first (`proxy_read_timeout`, 60 s by default) and
answer `504`, but the thread keeps running. So only the external call timeouts below limit how long a thread stays
busy.

### What can occupy a thread

Calls to AMT, SMS, SMTP and payment providers are synchronous, so each holds a thread for as long as it takes. Notification
SMS and email are sent inside the request (a known shortcut). When all threads are busy, new requests wait: in a local
test with 2 threads and 3 requests of 2 s, the third took 4 s. More threads do not add CPU capacity, and each password
check is CPU work.

| External call | Timeout |
|---|---|
| AMT | `AMT_TIMEOUT_SECONDS`, default 15 s per attempt |
| Sender.ge | 15 s |
| NBG | 10 s |
| SMTP | `EMAIL_TIMEOUT`, default 10 s (below) |
| Payment provider | None yet: only `FakeProvider` exists (B1). An adapter must set its own timeouts |

**Before using real AMT (release blocker B7):** LPK-220 covers an overall supplier lookup limit and external calls made
within database transactions. It is not implemented yet.

### Other points

- **Keep a margin:** one request can make several of these calls.
- **Gunicorn control socket (owner decision: disabled unless explicitly needed):** gunicorn 26 opens one by default at
  `~/.gunicorn/gunicorn.ctl`, so the example above passes `--no-control-socket`. Verified locally on gunicorn 26.2.0: the
  server runs and creates no socket. If you upgrade gunicorn, check that the option still exists.
- **Tracked concurrency work:** throttling (LPK-221) and the OTP resend cooldown (LPK-222). See `docs/security-review.md` §5.

### Email timeout

`EMAIL_TIMEOUT` controls the SMTP connection and socket timeout. It applies to each network wait, not the whole message, so one email can take longer than 10 s in
total.

A timeout is a safety limit, not a guarantee that an email will be delivered. It stops a slow mail server from holding
a worker. When it is reached, the email counts as failed and the action that triggered it still completes. The server
may or may not have accepted the message, so a failed email is not proof that nothing was sent.

### PostgreSQL connection budget

Each thread that has handled a request can hold its own database connection, so one gunicorn instance with 3 workers
× 8 threads can need about **24 application connections**. In a local test, 600 requests from 64 clients peaked at
exactly 24. Do not assume `max_connections` is large enough: check it.

```
needed = instances × workers × threads
       + cron jobs that can overlap
       + maintenance (migrations, pg_dump, psql)
       + monitoring
       + every other service using the same PostgreSQL server
       + superuser_reserved_connections

max_connections must exceed needed, with a margin.
```

| Item | Example, one instance |
|---|---|
| gunicorn, 1 × 3 × 8 | 24 |
| Cron (`reconcile_payments`, `reconcile_closed_payments`, `fetch_nbg_rates`, `purge_expired_billing`, `clearsessions`) | up to 5 |
| Maintenance | 3 |
| Monitoring | 1 |
| Reserved for superusers (PostgreSQL default) | 3 |
| **Total, before other services** | **36** |

- **Additional instances multiply it:** two instances need 48 application connections.
- **A restart that overlaps old and new workers** can briefly double the application part. Allow for it, or stop the old
  workers first.
- **`DB_CONN_MAX_AGE`** (default 60, `0` closes after every request, negative stops startup) is checked only when a thread
  starts or finishes a request. **It does not close idle connections on its own.** With a limit of 2 s in a local test,
  16 connections were still open after 5 s idle. So plan for the full 24 being held at all times after a busy period,
  until each thread's next request or a restart. With `0`, none stayed open after the load.
- **`CONN_HEALTH_CHECKS`** adds one check query the first time a reused connection is used in a request. Recovery from a
  lost connection has not been tested live.

Read-only checks for staging. Connect only to the staging database, never to a legacy one. `pg_stat_activity` shows the
whole server, so other services' connections count too:

```sql
SHOW max_connections;
SHOW superuser_reserved_connections;
-- Connections by database, user and state
SELECT datname, usename, state, count(*) FROM pg_stat_activity
WHERE backend_type = 'client backend' GROUP BY 1, 2, 3 ORDER BY 4 DESC;
-- Transactions left open, and how long
SELECT count(*), max(now() - xact_start) FROM pg_stat_activity WHERE state = 'idle in transaction';
-- Requests waiting on a lock
SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock';
```

### DatabaseCache at `MAX_ENTRIES=100000`

Throttles and admin login limits write to the `cache_table`, so even requests that need no other data use a connection.

- **Row growth:** expired rows are removed only when read again or when the table passes `MAX_ENTRIES`, so expect the
  table to climb towards 100,000 rows and then drop, rather than stay small.
- **Write cost:** every write runs `COUNT(*)` on the table, so cost rises with row count and with concurrent writers.
- **Culling:** above the limit, a request removes rows to make room. Watch how often this happens.
- **Bloat:** updates and deletes leave dead rows for autovacuum to clear.

One run on a development machine with a disposable database, for scale only. It is **not a guarantee**:

| Rows | 1 writer, p50 / p95 | 24 writers, p50 / p95 |
|---|---|---|
| 300 | 1.3 / 1.7 ms | 8.5 / 15.7 ms |
| 20,000 | 2.0 / 3.5 ms | 13.8 / 22.5 ms |
| 90,000 | 4.0 / 9.9 ms | 45.1 / 65.7 ms |

```sql
-- Rows, and how many have expired
SELECT count(*), count(*) FILTER (WHERE expires < now()) AS expired FROM cache_table;
-- Size on disk
SELECT pg_size_pretty(pg_total_relation_size('cache_table'));
-- Dead rows and last vacuum
SELECT n_live_tup, n_dead_tup, last_autovacuum FROM pg_stat_user_tables WHERE relname = 'cache_table';
```

### Staging load test checklist

Run these before settling on any production values. The thresholds are **starting points to investigate, not limits this
system has been shown to meet**. Use a load generator against staging only, with `SENDER_GE_ENABLED=False` and the demo
providers or a supplier sandbox.

| Measure | Look into it if |
|---|---|
| Connections, against `max_connections` | Above 80% at peak |
| `idle in transaction` age | Above 30 s |
| Sessions waiting on a lock | Sustained above 0 |
| `cache_table` rows | Above 80,000, or culling happens often |
| Request latency, p95, per endpoint | Worse than with the old configuration |
| Thread exhaustion | Health check latency rises under load |
| A simulated supplier timeout | Threads stay busy longer than expected |

## 5. nginx

```nginx
# Public link tokens (/n/<token>, /q/<token>, /api/public/n|q/<token>/) never reach the access log (D23).
map $request_uri $masked_uri {
    ~^(?<pre>.*/(?:api/public/)?[nq]/)[0-9A-Fa-f-]{8,}(?<post>.*)$  "${pre}<token>${post}";
    default $request_uri;
}
log_format masked '$remote_addr - [$time_local] "$request_method $masked_uri $server_protocol" '
                  '$status $body_bytes_sent "$http_user_agent"';

server {
    listen 443 ssl http2;
    server_name api.lionparts.ge;
    access_log /var/log/nginx/lionparts-api.access.log masked;   # never the default "combined" format
    client_max_body_size 10m;                                    # part images in the admin

    location /api/          { proxy_pass http://127.0.0.1:8001; include proxy_params_lionparts; }
    location /<ADMIN_URL_PATH> { proxy_pass http://127.0.0.1:8001; include proxy_params_lionparts; }
    location /static/       { alias /srv/lion-parts/backend/staticfiles/; }
    location /media/        { alias /srv/lion-parts/backend/media/; }
}
```

`proxy_params_lionparts`:

```nginx
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto $scheme;           # SECURE_PROXY_SSL_HEADER: HTTPS detection
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;   # client IP for throttles (NUM_PROXIES=1)
proxy_set_header X-Real-IP $remote_addr;
```

The **frontend** host (`/n/<token>`, `/q/<token>` routes) needs the same `map` and `log_format`: those tokens
are in its URLs too. Check any CDN, load balancer or analytics in front of either host for full-URL logging.

### Protecting the admin login (staging and production)

**Mandatory deployment condition (owner decision, B6):** the Django Admin must not be publicly exposed without an
approved nginx IP/VPN allow-list and additional nginx rate limiting. Admin 2FA is a separate pending task
(LPK-188/LPK-189).

The application limits failed admin logins by itself: 10 per IP and 5 per username in 15 minutes
(`docs/security-review.md` §5). nginx adds the required outer layer. This is an **example to adapt**: the addresses are
placeholders, and nothing here has been applied to a server. Test with `nginx -t` before reloading.

```nginx
# In the http {} block. Counts all requests to the login page per client address.
limit_req_zone $binary_remote_addr zone=admin_login:10m rate=10r/m;

server {
    # ... as above; these replace the single admin location ...

    location /<ADMIN_URL_PATH> {
        allow 203.0.113.10;      # office or VPN exit address (placeholder)
        deny  all;
        proxy_pass http://127.0.0.1:8001; include proxy_params_lionparts;
    }

    # An exact match wins over the location above, so it must repeat the allow-list.
    location = /<ADMIN_URL_PATH>login/ {
        allow 203.0.113.10;
        deny  all;
        limit_req zone=admin_login burst=5 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:8001; include proxy_params_lionparts;
    }
}
```

**Proxy IP handling:**

- `NUM_PROXIES` must equal the number of proxies in front of gunicorn: `1` for nginx alone. The application then takes
  the address nginx appended to `X-Forwarded-For`, so a client cannot change it by sending its own header.
- A wrong value means rate limits identify the wrong client. It cannot be detected in code, so check it at every deployment.
- Keep gunicorn bound to `127.0.0.1`, so nothing can reach it around nginx and forge the header.
- With a CDN or load balancer in front of nginx, `NUM_PROXIES` goes up by one for each, and nginx's `$remote_addr`
  becomes that proxy. Configure `set_real_ip_from` and `real_ip_header` first, or the allow-list and `limit_req` see the
  wrong address.

**If an operator is locked out:** the lockout ends by itself 15 minutes after the failures. Log lines `Admin login blocked` show the client IP, a keyed hash of the username and which limit was reached.

## 6. Scheduled tasks (cron, `CRON_TZ=Asia/Tbilisi`)

All commands are idempotent; run them as the service user with the same `.env`.

```cron
CRON_TZ=Asia/Tbilisi
15 0,9,13,17 * * *  cd /srv/lion-parts/backend && venv/bin/python manage.py fetch_nbg_rates
*/5 * * * *         cd /srv/lion-parts/backend && venv/bin/python manage.py reconcile_payments
7,22,37,52 * * * *  cd /srv/lion-parts/backend && venv/bin/python manage.py reconcile_closed_payments
30 3 1 * *          cd /srv/lion-parts/backend && venv/bin/python manage.py purge_expired_billing
45 3 * * *          cd /srv/lion-parts/backend && venv/bin/python manage.py clearsessions
```

- `fetch_nbg_rates`: stores the NBG USD rate and re-converts uncommitted GEL cart lines. Exit code 1 =
  "unavailable" (nothing changed; GEL prices answer `502 rate_unavailable` once the rate is too old). Alert on it.
- `reconcile_payments`: asks the configured provider about its card payments that are `unknown` or `pending` for
  more than 2 minutes, and applies `paid` / `failed` / `expired`. A payment still `pending` after 30 minutes becomes
  `unknown` (highlighted in the payment admin); age alone never closes an `unknown` payment. Prints
  `updated / unknown / errors`, where `unknown` counts only payments that newly became unknown. Any `errors` count
  means a provider failure, an invalid answer, or a payment that needs checking (§10).
- `reconcile_closed_payments`: asks the configured provider about card payments that are `failed` or `expired` here,
  had a provider page, and have no closure evidence yet. `paid` is counted as usual. A closing answer keeps the local
  status and notifies nobody. At most 100 payments per run; each one backs off from 15 minutes to once a day and is
  checked until there is evidence, so it recovers by itself after a provider outage. Prints
  `checked / closed / paid / open / errors / unresolved / other provider / needs review`. This job must be scheduled: nothing else
  looks for this evidence.
- `purge_expired_billing`: anonymizes the billing snapshot of deleted accounts' orders after 5 years.
- `clearsessions`: Django built-in.

## 7. Monitoring

| What | How | Action |
|---|---|---|
| Process up | `GET /api/health/` every minute | restart the service; check logs |
| Database | `pg_isready` and a periodic `SELECT 1` as the app role | DB incident |
| Errors | 5xx rate in the nginx log; `ERROR` lines in journald | investigate |
| Exchange rate | `fetch_nbg_rates` exit code 1 | NBG down: see §10 |
| Database connections and cache | the read-only queries in §4 | follow the staging checklist in §4 |
| Payments | count of `unknown` payments; `reconcile_payments` `errors > 0`; log line "provider says paid" | follow the manual escalation in §10 |
| Closed payments | `reconcile_closed_payments` has run in the last hour; its `errors > 0`, `other provider > 0`, `needs review > 0`; log lines "no provider evidence after", "reports it paid" and "needs review" | follow the manual escalation in §10 |
| Notifications | `failed` rows in the delivery log (admin filter); `skipped` in production means a channel is off | fix the provider, then "resend" |
| Disk | database, `MEDIA_ROOT`, logs | rotate / grow |

## 8. Backups and restore

- **Database:** nightly `pg_dump -Fc` of the application database, kept 30 days, encrypted, stored off the
  host. **Media:** nightly copy of `MEDIA_ROOT`.
- Never commit dumps; never restore a production dump into staging without anonymizing it first.
- **Restore test** monthly into a disposable database: `pg_restore -d <empty db>`, then `manage.py check` and
  `manage.py showmigrations` against it.

## 9. Release and rollback

1. Announce, tag the release (`git tag vX.Y.Z`).
2. **Backup** database and media (§8).
3. `git checkout vX.Y.Z`, `pip install -r requirements.txt`, `compilemessages`, `collectstatic`,
   `check --deploy`, `migrate`, `systemctl restart lionparts-api`.
4. Smoke test: `/api/health/`, `/api/meta/`, a login, an order list.

Migrations are forward-only. **Rollback** = stop the service, restore the pre-release backup, check out the
previous tag, restart. Run `migrate` with the new code only after the backup has finished.

## 10. Incidents

- **Payment provider down:** card payments stay `created/pending`; `reconcile_payments` retries and marks
  `unknown` after 30 minutes. A `pending` or `unknown` payment, or a card payment closed here while its provider page
  may still take money, blocks every new payment on its order, invoice included (`409 payment_in_progress`), so those
  customers cannot switch to invoice; other customers can pay by invoice. The jobs keep re-checking them, so they
  resolve once the provider answers again.
- **Unresolved card payment (manual escalation):** use this when the job or "Re-check with provider" cannot get a
  final answer, or reports a conflict.
  1. Run "Re-check with provider" in the payment admin (needs `payments.change_payment`). It works only on card
     payments of the configured provider that are `pending` or `unknown`, or closed here without closure evidence. It
     never lets an operator choose the result.
  2. Look the payment up in the provider's merchant dashboard by `provider_order_id`. Ask the provider's support if
     the dashboard has no answer. Keep the evidence: provider reference, status, amount, currency, date, who checked.
  3. Ask the provider to resend its signed callback. The callback checks the signature, amount and currency, so this
     is the normal way to apply a verified result.
  4. If the provider cannot confirm the result, the payment stays `unknown` and keeps blocking new payments on that
     order, because a second payment could charge the customer twice. Tell the customer it is being checked. If they
     want out, an operator can cancel the order: the payment stays `unknown`, and if it is later paid the order shows
     `refund_pending`.
  5. **Provider change:** the job and the admin action ask only the configured provider, and the callback endpoint
     answers `404` for any other, so a previous provider's payments no longer resolve on their own. Before switching,
     filter the payment admin by `pending` and `unknown` and resolve them with the current provider. Any left over
     stay blocked and are escalated (step 10), using the old provider's dashboard for evidence (step 2).
  6. **Provider conflict:** the log line "provider says paid … marked unknown", with `errors` counted by the job, means
     the provider's answers conflict. The payment stays `unknown`, blocks new card and invoice payments, and the job keeps
     asking. `provider_payload` records `conflict_with` and `conflict_second_answer`. Follow steps 2 and 3.
  7. **More than one payment on an order (LPK-219):** escalate to the owner. If an order shows `refund_pending`, use
     "Record refund of the amount owed". Use "Cancel invoice" for an invoice the order no longer owes.
  8. **A closed payment that still blocks (LPK-219):** a card payment `failed` or `expired` here keeps blocking new
     payments until the provider confirms the session has closed. Cancelling an order or item does not lift it. Run
     "Re-check with provider" after the session's expiry. If there is still no proof, tell the customer it is being
     checked and escalate to the owner. Do not edit `provider_closed_at`.
  9. **Paid results waiting for review (LPK-219):** a payment that became `paid` after closing here, on a cancelled
     order, or for more than the order owed is flagged, once, with its reasons (payment admin, filter "Review"). The money
     is already counted. Check it, use "Record refund of the amount owed" if the order shows `refund_pending`, then "Mark
     as reviewed". The admin filter "Provider session" lists payments not yet proven closed.
  10. **No manual status changes.** An `unknown` payment is resolved only by provider confirmation or an explicitly
     authorized, auditable escalation process. Nobody sets the status of a real card payment by hand, through the
     admin, a Django shell or the database, until a separately approved procedure is defined. Unresolved payments stay
     as they are and are escalated, never guessed. Any future resolution requires all of: verified provider evidence,
     an incident/reference record, explicit approval from the owner or authorised finance, and an auditable resolution.
- **Staging, abandoned fake payments:** a fake payment left unfinished becomes `unknown` after 30 minutes once
  `reconcile_payments` runs. `FakeProvider` has no answer for it and the demo complete endpoint accepts only `pending`
  payments, so it stays `unknown` and blocks that order. Start a fresh test order instead.
- **SMS provider down:** OTP sending answers `502 sms_failed`; notification deliveries are `failed`
  (resend from the delivery log later). Email keeps working.
- **NBG rate unavailable:** USD prices keep working; GEL prices answer `502 rate_unavailable` once the stored
  rate is older than `SiteSettings.rate_max_age_days`. Run `fetch_nbg_rates` again when NBG is back.
- **AMT supplier down:** search answers `502 supplier_unavailable`; prepared quotes and the cart keep working.
- **Leaked secret** (`SECRET_KEY`, DB password, Sender.ge/AMT/payment keys): rotate it at the provider and in
  `.env`, restart. A new `SECRET_KEY` logs everyone out and invalidates reset/verification links.
- **Suspected data exposure:** preserve logs, revoke sessions (`clearsessions` + rotate `SECRET_KEY`), contact
  the owner; public notification tokens expire after 30 days and can be invalidated by deleting the
  notification rows concerned.
