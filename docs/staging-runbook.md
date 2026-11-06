# Isolated staging runbook (LPK-195 to LPK-200)

A staging that shares nothing with any existing application, database or site on the host. Everything here is prepared in
`deploy/staging/` and **has not been applied to any host**. Provisioning, starting services, nginx, DNS and TLS all need the
owner's approval. Section 10 says what has been rehearsed and what has not.

## 1. Target

| Item | Value |
|---|---|
| Linux user | `lionparts-stage`, no sudo, password locked |
| Checkout and virtual environment | `/home/lionparts-stage/lion-parts`, `backend/venv` |
| Database and role | new and empty: `lp_stage_v1`, role `lp_stage`, at `127.0.0.1:5432`; `lp_stage_restore_check` only for restore tests |
| Environment file | `backend/.env`, mode 600, from `deploy/staging/env.staging.example` |
| Application | `lionparts-stage.service`, gunicorn on `127.0.0.1:8011`, 2 workers x 4 threads |
| Mail | Mailpit: SMTP `127.0.0.1:1025`, web and API `127.0.0.1:8025`, reached through an SSH tunnel |
| Media, static | `/home/lionparts-stage/media`, `backend/staticfiles`, read by nginx (section 4) |
| Backups | `/home/lionparts-stage/backups`, mode 700 |
| nginx logs | `/var/log/nginx/lionparts-stage-{access,error}.log` |
| Admin superusers | exactly two, staging only (section 3) |
| Behaviour | `DEPLOY_ENV=staging`, `DEBUG=False`, `PARTS_PROVIDER=demo`, `PAYMENT_PROVIDER=fake`, `SMS_DEMO_MODE=True`, `SENDER_GE_ENABLED=False`, `ENABLE_DEMO_ENDPOINTS=True` |

Before starting, check the host for conflicts with `deploy/staging/provision.sh`, which changes nothing without `--apply`. Do
not reuse an existing nginx site, service, role or database. `scripts/deploy-staging.sh` is the disabled legacy script: never use it.

## 2. Provision (owner, as root, once)

```bash
git clone https://github.com/ladomenteshashvili/lion-parts.git /tmp/lp-kit && cd /tmp/lp-kit/deploy/staging
./provision.sh           # prints the plan and any conflict; changes nothing
./provision.sh --apply 2>&1 | tee /root/lp-stage-provision.log   # creates only new resources; stops if any exists; starts nothing
```

It generates `SECRET_KEY` and the database password into `.env` and never prints them. Also needed, once per host: `gettext`,
`postgresql-client`, `curl`, and Python 3.14 with `venv`.

**Mailpit** is not installed by the script. Download a release from its official site, verify it against the project's published
checksum or signature, and install it as `/usr/local/bin/mailpit`. Its unit binds to loopback only.

### If `--apply` fails part-way

`provision.sh` never removes anything, and it refuses to run again while any of its resources exists. The steps below are done by
hand and only with the owner's approval. They apply only to resources that **this failed run created**.

1. **Stop.** Do not run `--apply` again, and do not start or deploy anything. The log saved above lists each `creating:` step; it holds no
   secrets. Do not open or copy `.env`.
2. **List what exists.** As root, `./provision.sh` changes nothing and names every resource it finds.
3. **Compare.** A resource can be removed only if the log shows this run created it. If a resource is not in the log, or you cannot
   tell, leave it and take the question to the owner.
4. **Prove it is unused.** Each check must come back as described, or stop:

   ```bash
   sudo -u postgres psql -Atc "SELECT datname, pg_get_userbyid(datdba) FROM pg_database WHERE datname IN ('lp_stage_v1', 'lp_stage_restore_check')"   # owner lp_stage
   sudo -u postgres psql -d lp_stage_v1 -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"              # 0
   sudo -u postgres psql -d lp_stage_restore_check -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"   # 0
   ps -u lionparts-stage                                                                                               # no processes
   crontab -l -u lionparts-stage                                                                                       # no crontab
   systemctl is-enabled lionparts-stage lionparts-stage-mailpit                                                        # disabled or not found
   ls -la /home/lionparts-stage                                                                                        # only what this run made
   ```

5. **Remove only what this run created**, one exact name at a time, skipping any it did not reach:

   ```bash
   rm -- /etc/systemd/system/lionparts-stage.service /etc/systemd/system/lionparts-stage-mailpit.service
   systemctl daemon-reload
   sudo -u postgres psql -v ON_ERROR_STOP=1 -c 'DROP DATABASE lp_stage_restore_check'
   sudo -u postgres psql -v ON_ERROR_STOP=1 -c 'DROP DATABASE lp_stage_v1'
   sudo -u postgres psql -v ON_ERROR_STOP=1 -c 'DROP ROLE lp_stage'
   userdel lionparts-stage
   rm -rf -- /home/lionparts-stage
   ```

   Do not add `FORCE`, `CASCADE`, `DROP OWNED` or a wildcard. If a command refuses, something else depends on that resource: stop and
   report it. Nothing else on the host is part of this cleanup: not nginx, not other roles, databases, users or services.
6. **Check and retry.** `./provision.sh` must report `none found`. Fix the original cause, then run `--apply` again; it generates new secrets.

If only the last step failed (`systemctl daemon-reload`) and everything else is in place, run that command again instead of removing anything.

## 3. First deploy (as `lionparts-stage`)

1. Replace `STAGE_HOST` and `FRONTEND_ORIGIN` in `.env`. Choose a hostname of its own for staging.
2. `deploy/staging/stage.sh deploy <git-ref>`. In order: code, dependencies, `compilemessages`, `collectstatic`, proof of the target,
   `migrate --plan`, `migrate`, `createcachetable`, `check`, `check --deploy`, `makemigrations --check`.
3. As the owner: `systemctl enable --now lionparts-stage-mailpit lionparts-stage`.
4. Create the two approved superusers: see [Admin superusers](#admin-superusers-lpk-228). Skip this step until the owner has approved them.
5. `backend/venv/bin/python manage.py fetch_nbg_rates`: the first rate. Reads the public NBG rate only. Without a rate, GEL prices
   answer `502 rate_unavailable`.
6. `deploy/staging/stage.sh health`.
7. Optional: in the admin, set notification channels to `email` to see notification emails in Mailpit. **All channels are seeded as
   `none`**, so without this only the account emails (verification, password reset, email change) are sent.

Every `stage.sh` command first refuses any other Linux user, a readable `.env`, any non-demo setting, any real credential, and any
database other than exactly `lp_stage_v1`, role `lp_stage`, at `127.0.0.1:5432`. The others then prove the target before changing
anything, by comparing Django's loaded settings and what the server reports for the live connection: database, current and session
user, address and port, and that the role is not a superuser. `deploy` and `rollback` update the code first, because the proof runs on
that code, and prove the target before any migration. These values cannot be overridden: moving the database needs a reviewed
change to `stage.sh` and this runbook. `check --deploy` should report only `security.W005` and `security.W021`, the two HSTS owner
decisions; anything else stops the script.

### Admin superusers (LPK-228)

Staging has **exactly two** Django Admin superusers. They exist on staging only.

**Needs the owner's approval first:** the two account identifiers, and how each credential reaches its holder. Until then, create none.

- **No credentials in Git, logs or Jira.** That includes identifiers paired with passwords, screenshots and pasted terminal output.
  Hand each credential over only by the approved method, and never in a ticket, commit or shared chat.
- Create each account at the interactive prompt. Do not pass a password as an argument, in `DJANGO_SUPERUSER_PASSWORD` or through a script:

  ```bash
  cd /home/lionparts-stage/lion-parts/backend && set -a && . ./.env && set +a
  venv/bin/python manage.py createsuperuser   # once for each approved account
  venv/bin/python manage.py shell -v 0 -c "from django.contrib.auth import get_user_model as U; print(U().objects.filter(is_superuser=True).count())"   # must print 2
  ```

- Use a new, unique password for each. Reuse nothing from production, any other system or the other account.
- No third superuser and no shared one. UAT and demo operator work use one of the two, by its holder.
- Repeat the count after any restore. If it is not 2, stop and report it to the owner; do not fix it by adding or removing accounts yourself.
- Admin access stays limited by `ADMIN_ALLOW` (section 4, B6).

## 4. nginx and TLS (owner)

**This template covers Django only:** `/api/`, the admin, `/static/` and `/media/`. Every other path, including `/`, answers 404. It does
not serve the React frontend. React integration and `test2.lionparts.ge` are tracked separately in LPK-223 to LPK-227 and are not
part of this kit.

```bash
STAGE_HOST=<host> ADMIN_ALLOW=<office or VPN address> LISTEN=443 STAGE_HOME=/home/lionparts-stage \
TLS_CERT=/etc/letsencrypt/live/<host>/fullchain.pem TLS_KEY=/etc/letsencrypt/live/<host>/privkey.pem \
envsubst '$STAGE_HOST $ADMIN_ALLOW $LISTEN $STAGE_HOME $TLS_CERT $TLS_KEY' \
  < deploy/staging/nginx-stage.conf.template > /etc/nginx/sites-available/lionparts-stage
```

TLS and DNS checklist:

- [ ] A DNS record for the new hostname only; no existing record changes.
- [ ] A certificate for that hostname only, issued without changing other sites.
- [ ] The permission check below passes.
- [ ] The host's log rotation covers `/var/log/nginx/*.log`, or the two new logs have their own rule.
- [ ] `nginx -t` passes; only then enable the site and reload. A reload touches the whole nginx, so choose the moment.
- [ ] `ADMIN_ALLOW` is the approved address (B6); the admin is not public.
- [ ] `https://<host>/api/health/` answers `ok`; `https://<host>/` answers 404.
- [ ] HSTS: staging sends `max-age=31536000` without `includeSubDomains`. A browser remembers it for a year for this host only.

Do not expose Mailpit, port 8011 or PostgreSQL publicly. `NUM_PROXIES` stays 1 while nginx alone is in front.

### Permissions

The nginx user is **not** added to the `lionparts-stage` group, because that would let it list the checkout and backups. It gets only what it needs:

| Path | Mode | nginx user |
|---|---|---|
| `/home/lionparts-stage`, `lion-parts`, `lion-parts/backend` | 711 | passes through, cannot list |
| `lion-parts/backend/staticfiles`, `/home/lionparts-stage/media` | 755 directories, 644 files | reads |
| `lion-parts/backend/.env` | 600 | no access |
| `/home/lionparts-stage/backups` and everything in it | 700, 600 files | no access |
| `/var/log/nginx/lionparts-stage-*.log` | set by nginx and log rotation | nginx opens them; not in the staging home, so root never follows a path the staging user controls |

Database credentials exist only in `.env`. The template serves nothing but `staticfiles` and `media`, with no directory listing.

**Check as root, after the first deploy and before enabling the site.** Use the `user` value from the nginx configuration:

```bash
H=/home/lionparts-stage; N=www-data
as_nginx() { sudo -u "$N" test "$@" && echo "ALLOWED $*" || echo "denied  $*"; }
# Each of these must say ALLOWED:
as_nginx -r "$H/lion-parts/backend/staticfiles/admin/css/base.css"
as_nginx -x "$H/lion-parts/backend/staticfiles"
as_nginx -x "$H/media"
# Each of these must say denied:
as_nginx -r "$H/lion-parts/backend/.env"
as_nginx -r "$H"
as_nginx -r "$H/lion-parts/backend"
as_nginx -r "$H/backups"
as_nginx -x "$H/backups"
# Nothing should be printed: any file under media or staticfiles that nginx could not read, or a backup others could reach.
find "$H/media" "$H/lion-parts/backend/staticfiles" \( -type f ! -perm -o=r -o -type d ! -perm -o=rx \) -print
find "$H/backups" -perm /o=rwx -print
stat -c '%a %U %n' "$H/lion-parts/backend/.env"   # 600 lionparts-stage
```

**After the site is enabled**, from any machine. Each must answer 404 or 400, not 200 (`--path-as-is` stops curl tidying the path):

```bash
for path in /backups/ /.env /static/../.env /static/../../.env /media/../backups/ /media/../lion-parts/backend/.env; do
  curl -sk --path-as-is -o /dev/null -w "%{http_code} $path\n" "https://<host>$path"
done
curl -sk -o /dev/null -w '%{http_code} static (must be 200)\n' https://<host>/static/admin/css/base.css
```

If a check fails, stop and fix the mode of that one path; do not widen access to the parent directory or add the nginx user to a group.

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
read -rp 'Superuser: ' UAT_STAFF_EMAIL; read -rsp 'Password: ' UAT_STAFF_PASSWORD; echo   # kept out of shell history
UAT_STAFF_EMAIL="$UAT_STAFF_EMAIL" UAT_STAFF_PASSWORD="$UAT_STAFF_PASSWORD" venv/bin/python -m uat.run_http \
  --base-url https://<host> --origin https://<frontend origin> --mailpit http://127.0.0.1:8025 --transcript /tmp/uat.json
```

Use one of the two superusers from section 3, by its holder. It refuses any target without the demo routes. The rate limits apply to it: a full run uses about 6 registrations of the 10 allowed
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

The isolation and path checks were rehearsed the same way on a disposable cluster and a scratch nginx, in a scratch copy whose expected port was changed:

| Step | Result |
|---|---|
| `verify_target.py`, right target | passes |
| Wrong role, port or database in settings or on the live connection; a superuser role | refused |
| `stage.sh check`, `backup`, `restore-verify` with the proof in place | pass; backups directory 700, dump 600 |
| Paths in section 4 that must not be served | 400 or 404 |
| Static and media files; static directory | 200; 403, no listing |
| The superuser count command | runs on PostgreSQL |

**Not rehearsed:** `provision.sh --apply` as real root (a test runs its failure path with stand-ins only), the cleanup in section 2, the permission check
as a real nginx user, the systemd units, the real Mailpit (the runner's two API calls ran against a stand-in, so check them first), a real certificate,
DNS, `rollback`, and the crontab. Treat each as untested until staging exists.
