# Isolated staging runbook (LPK-195 to LPK-200)

A staging that shares nothing with any existing application, database or site on the host. Everything here is prepared in
`deploy/staging/` and **has not been applied to any host**. Provisioning, starting services, nginx, DNS and TLS all need the
owner's approval. Section 10 says what has been rehearsed and what has not.

## 1. Target

| Item | Value |
|---|---|
| Linux user | `lionparts-stage`, no sudo, password locked |
| Checkout and virtual environment | `/home/lionparts-stage/lion-parts`, `backend/venv` |
| Database and role | new and empty: `lp_stage_v1`, role `lp_stage`; `lp_stage_restore_check` only for restore tests |
| Environment file | `backend/.env`, mode 600, from `deploy/staging/env.staging.example` |
| Application | `lionparts-stage.service`, gunicorn on `127.0.0.1:8011`, 2 workers x 4 threads |
| Mail | Mailpit: SMTP `127.0.0.1:1025`, web and API `127.0.0.1:8025`, reached through an SSH tunnel |
| Media, static, logs | `/home/lionparts-stage/media`, `backend/staticfiles`, `/home/lionparts-stage/log` |
| Behaviour | `DEPLOY_ENV=staging`, `DEBUG=False`, `PARTS_PROVIDER=demo`, `PAYMENT_PROVIDER=fake`, `SMS_DEMO_MODE=True`, `SENDER_GE_ENABLED=False`, `ENABLE_DEMO_ENDPOINTS=True` |

Before starting, check the host for conflicts with `deploy/staging/provision.sh`, which changes nothing without `--apply`. Do
not reuse an existing nginx site, service, role or database. `scripts/deploy-staging.sh` is the disabled legacy script: never use it.

## 2. Provision (owner, as root, once)

```bash
git clone https://github.com/ladomenteshashvili/lion-parts.git /tmp/lp-kit && cd /tmp/lp-kit/deploy/staging
./provision.sh           # prints the plan and any conflict; changes nothing
./provision.sh --apply   # creates only new resources; stops if any of them exists; starts nothing
```

It generates `SECRET_KEY` and the database password into `.env` and never prints them. Also needed, once per host: `gettext`,
`postgresql-client`, `curl`, and Python 3.14 with `venv`.

**Mailpit** is not installed by the script. Download a release from its official site, verify it against the project's published
checksum or signature, and install it as `/usr/local/bin/mailpit`. Its unit binds to loopback only.

## 3. First deploy (as `lionparts-stage`)

1. Replace `STAGE_HOST` and `FRONTEND_ORIGIN` in `.env`. Choose a hostname of its own for staging.
2. `deploy/staging/stage.sh deploy <git-ref>`. In order: code, dependencies, `compilemessages`, `collectstatic`, proof of the target,
   `migrate --plan`, `migrate`, `createcachetable`, `check`, `check --deploy`, `makemigrations --check`.
3. As the owner: `systemctl enable --now lionparts-stage-mailpit lionparts-stage`.
4. `backend/venv/bin/python manage.py createsuperuser` (with `.env` loaded), for the admin and the demo operator endpoints.
5. `backend/venv/bin/python manage.py fetch_nbg_rates`: the first rate. Reads the public NBG rate only. Without a rate, GEL prices
   answer `502 rate_unavailable`.
6. `deploy/staging/stage.sh health`.
7. Optional: in the admin, set notification channels to `email` to see notification emails in Mailpit. **All channels are seeded as
   `none`**, so without this only the account emails (verification, password reset, email change) are sent.

Every `stage.sh` command first refuses any other user, any other database, a readable `.env`, any non-demo setting or any real
credential, and then checks what Django really connects to. `check --deploy` should report only `security.W005` and `security.W021`,
the two HSTS owner decisions; anything else stops the script.

## 4. nginx and TLS (owner)

```bash
STAGE_HOST=<host> ADMIN_ALLOW=<office or VPN address> LISTEN=443 STAGE_HOME=/home/lionparts-stage \
TLS_CERT=/etc/letsencrypt/live/<host>/fullchain.pem TLS_KEY=/etc/letsencrypt/live/<host>/privkey.pem \
envsubst '$STAGE_HOST $ADMIN_ALLOW $LISTEN $STAGE_HOME $TLS_CERT $TLS_KEY' \
  < deploy/staging/nginx-stage.conf.template > /etc/nginx/sites-available/lionparts-stage
```

TLS and DNS checklist:

- [ ] A DNS record for the new hostname only; no existing record changes.
- [ ] A certificate for that hostname only, issued without changing other sites.
- [ ] The nginx user can write `/home/lionparts-stage/log` and read `staticfiles` and `media`.
- [ ] `nginx -t` passes; only then enable the site and reload. A reload touches the whole nginx, so choose the moment.
- [ ] `ADMIN_ALLOW` is the approved address (B6); the admin is not public.
- [ ] `https://<host>/api/health/` answers `ok`; `https://<host>/` answers 404.
- [ ] HSTS: staging sends `max-age=31536000` without `includeSubDomains`. A browser remembers it for a year for this host only.

Do not expose Mailpit, port 8011 or PostgreSQL publicly. `NUM_PROXIES` stays 1 while nginx alone is in front.

## 5. Every later deploy

| Step | How | Check |
|---|---|---|
| Backup first | `stage.sh backup` | prints the dump's path; note it for rollback |
| Migrations | `manage.py migrate --plan`, inside `stage.sh deploy` | read the plan; reversible? |
| Cache table | `createcachetable`, inside deploy | idempotent; throttles need it, and a missing table answers 429 |
| Translations | `compilemessages`, inside deploy | ka and ru compile |
| Static | `collectstatic`, inside deploy | admin CSS loads |
| Restart | owner: `systemctl restart lionparts-stage` | a restart resets nothing in the database; requests in flight get up to 30 s |
| Health | `stage.sh health` | `ok`, connection count, cache rows, latest rate |
| Smoke | `python -m uat.run_http` (section 6) | every journey passes |

## 6. UAT and load

```bash
cd backend && set -a && . ./.env && set +a
UAT_STAFF_EMAIL=<superuser> UAT_STAFF_PASSWORD=<password> venv/bin/python -m uat.run_http \
  --base-url https://<host> --origin https://<frontend origin> --mailpit http://127.0.0.1:8025 --transcript /tmp/uat.json
```

It refuses any target without the demo routes. The rate limits apply to it: a full run uses about 6 registrations of the 10 allowed
per IP per hour. Scenario lists and the bounded load tests are in `docs/uat-matrix.md`. To repeat within the hour, on this database only:
`psql -d lp_stage_v1 -c 'DELETE FROM cache_table'`.

## 7. Backup, restore, rollback

- **Backup:** `stage.sh backup` runs `pg_dump -Fc` and archives the media, mode 600, keeps the last 7 and checks that the dump is readable.
  Staging holds test data only: never restore a production dump into it.
- **Restore test:** `stage.sh restore-verify <dump>` restores into `lp_stage_restore_check` only, compares row counts and opens the copy with
  Django. Do it monthly and after any change to the backup.
- **Code rollback:** `stage.sh rollback <git-ref>`, then the owner restarts. It warns when the old code expects different migrations.
- **Database rollback:** stop the service, then `CONFIRM=lp_stage_v1 stage.sh restore <dump>`, deploy the matching code and start the
  service. It refuses while the service still answers. Everything after the dump is lost.

## 8. Basic monitoring

- [ ] `/api/health/` from outside, each minute. It proves the process answers, not the database; `stage.sh health` covers that.
- [ ] `systemctl is-active lionparts-stage lionparts-stage-mailpit`.
- [ ] Log lines through `journalctl -u lionparts-stage`: `Throttle ...: the cache failed`, `AMT lookup stopped`, `OTP send outcome unknown`, `NBG rate fetch failed`, `needs review`.
- [ ] PostgreSQL connections to `lp_stage_v1` against the role limit of 30.
- [ ] `cache_table` row count stays below `MAX_ENTRIES` (`docs/operations.md`).
- [ ] Disk, memory (the units cap 1.5 GB and 200 MB) and the age of the latest exchange rate.

## 9. Scheduled jobs inventory

`deploy/staging/crontab.example`, for `lionparts-stage` only, after approval. Nothing is installed.

| Job | Schedule (Asia/Tbilisi) | On staging |
|---|---|---|
| `fetch_nbg_rates` | 00:15, 09:15, 13:15, 17:15 | reads the public NBG rate |
| `reconcile_payments` | every 5 minutes | asks `FakeProvider` |
| `reconcile_closed_payments` | at :07, :22, :37, :52 | asks `FakeProvider`; fake rows are exempt |
| `purge_expired_billing` | 03:30 on the 1st | test data only |
| `clearsessions` | 03:45 | |
| `stage.sh backup` | 04:10 | local dump |

## 10. Rehearsal on 2026-11-06

Rehearsed under an unprivileged user with a scratch clone, a disposable PostgreSQL 18 cluster with the real database and role names, a
scratch nginx 1.28 with a self-signed certificate, and a local SMTP sink. No existing service, site, database or port was touched.

| Step | Result |
|---|---|
| `stage.sh deploy` on the empty database | 45 migrations; only W005 and W021 |
| `stage.sh check`, `health`, `backup` | pass |
| `restore-verify` | counts equal: 16 orders, 22 payments, 6 customers, 45 migrations |
| `restore` | refused without `CONFIRM` and while the service answered |
| Rendered nginx template | `nginx -t` passes, after fixing an unquoted regex that the rehearsal found, also present in `docs/operations.md` |
| `uat.run_http`, through nginx and gunicorn over HTTPS | 14 of 14 journeys |
| `uat.load_test`, all six scenarios | as in `docs/uat-matrix.md` D5 |
| Public link token in the nginx access log | masked as `<token>` |
| Site root, admin login, static file | 404, 200 from an allowed address, 200 |
| Emails over real SMTP | From, Reply-To, Message-ID and footer correct; `<html lang>` follows the customer |

**Not rehearsed:** `provision.sh --apply` (it needs root), the systemd units, the real Mailpit (the runner's two API calls ran against a stand-in, so
check them first), a real certificate, DNS, `rollback`, and the crontab. Treat each as untested until staging exists.
