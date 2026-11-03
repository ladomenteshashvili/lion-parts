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

> ⚠️ **`scripts/deploy-staging.sh` belongs to the legacy installation.** It checks out `main`, which is now v1,
> and runs `migrate` in `/home/lado/lion-parts`. Running it would apply the v1 migrations to the legacy
> staging database (B2). Do not run it for v1; a v1 staging is built fresh (§2).

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
| `NUM_PROXIES` | `1` | `1` | **Required behind nginx.** With `0` every client shares one rate-limit bucket (nginx's address). |
| `ADMIN_URL_PATH` | a non-obvious path | any | Must match the nginx location. |
| `PARTS_PROVIDER` | `amt` | `amt` or `demo` | Production refuses `demo`. |
| `AMT_VERIFY_SSL` | `True` | `True` | Production refuses `False`. |
| `SENDER_GE_ENABLED` | `True` | `False` | Staging must not send real SMS. |
| `SMS_DEMO_MODE` | `False` | `True` | Demo OTP codes in responses; refused in production. |
| `ENABLE_DEMO_ENDPOINTS` | `False` | `True` | Refused in production and with a real SMS or payment provider. |
| `PAYMENT_PROVIDER` | real adapter (**B1**) | `fake` | Production refuses `fake`. |
| `EMAIL_BACKEND` | smtp | smtp (Mailpit) | Production refuses console/locmem/dummy/filebased. |
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
    --bind 127.0.0.1:8001 --workers 3 --timeout 60
# No --access-logfile: nginx writes the (masked) access log; gunicorn's would contain public link tokens.
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Notification SMS/email are sent synchronously inside the request (a known shortcut), so keep `--timeout` above
the SMS/SMTP timeouts (Sender.ge 15 s).

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

## 6. Scheduled tasks (cron, `CRON_TZ=Asia/Tbilisi`)

All commands are idempotent; run them as the service user with the same `.env`.

```cron
CRON_TZ=Asia/Tbilisi
15 0,9,13,17 * * *  cd /srv/lion-parts/backend && venv/bin/python manage.py fetch_nbg_rates
*/5 * * * *         cd /srv/lion-parts/backend && venv/bin/python manage.py reconcile_payments
30 3 1 * *          cd /srv/lion-parts/backend && venv/bin/python manage.py purge_expired_billing
45 3 * * *          cd /srv/lion-parts/backend && venv/bin/python manage.py clearsessions
```

- `fetch_nbg_rates`: stores the NBG USD rate and re-converts uncommitted GEL cart lines. Exit code 1 =
  "unavailable" (nothing changed; GEL prices answer `502 rate_unavailable` once the rate is too old). Alert on it.
- `reconcile_payments`: asks the provider about card payments pending for more than 2 minutes; after 30
  minutes they become `unknown` (highlighted in the payment admin). Prints `updated / unknown / errors`.
- `purge_expired_billing`: anonymizes the billing snapshot of deleted accounts' orders after 5 years.
- `clearsessions`: Django built-in.

## 7. Monitoring

| What | How | Action |
|---|---|---|
| Process up | `GET /api/health/` every minute | restart the service; check logs |
| Database | `pg_isready` and a periodic `SELECT 1` as the app role | DB incident |
| Errors | 5xx rate in the nginx log; `ERROR` lines in journald | investigate |
| Exchange rate | `fetch_nbg_rates` exit code 1 | NBG down: see §10 |
| Payments | count of `unknown` payments; `reconcile_payments` `errors > 0` | check with the provider, confirm by hand |
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
  `unknown` after 30 minutes. Customers can pay by invoice. Confirm unknown payments with the provider before
  marking anything; never mark a card payment paid by hand.
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
