# Lion Parts backend

Django 6.1 + DRF API for Lion Parts. Contract and business rules: `AI_IMPLEMENTATION_GUIDE.md` (kept by the
owner, not in Git); the frontend handoff is `docs/api-handoff.md`. Operations: `docs/operations.md`.
Security review and release blockers: `docs/security-review.md`.

> **Not production-ready:** no real card payment provider yet and no in-place migration of the legacy
> databases (`docs/security-review.md` §4).

## Quick start (SQLite, no external services)

Demo parts supplier, demo SMS codes in the responses, fake payments, emails kept in memory.

```bash
cd backend
python3.14 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
sudo apt install gettext                                  # once: needed by compilemessages
python manage.py compilemessages --ignore=venv
python manage.py migrate --settings=config.e2e_settings   # creates backend/e2e.sqlite3 (gitignored)
python manage.py createcachetable --settings=config.e2e_settings
python manage.py createsuperuser --settings=config.e2e_settings
python manage.py runserver 127.0.0.1:8000 --settings=config.e2e_settings
```

The API is at `http://127.0.0.1:8000/api/` (try `/api/meta/`), the admin at `http://127.0.0.1:8000/admin/`.
GEL prices need an exchange rate: run `fetch_nbg_rates` once, or add one in the admin (Pricing → Exchange rates).

## Local setup with PostgreSQL

```bash
cp .env.example .env     # fill in real values privately; never commit .env
# create an empty database and role for yourself (never a legacy lion_parts* database)
python manage.py migrate && python manage.py createcachetable
python manage.py runserver 127.0.0.1:8000
```

Every environment variable is documented in `.env.example`; per-environment values are in
`docs/operations.md` §3.

## Checks and tests

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --settings=config.e2e_settings    # SQLite; PostgreSQL-only concurrency tests are skipped
python manage.py test                                   # PostgreSQL from .env: the full suite, nothing skipped
```

CI (`.github/workflows/ci.yml`) runs the full suite on PostgreSQL 16 for every pull request to `main`.

## Management commands (scheduled: `docs/operations.md` §6)

| Command | Purpose |
|---|---|
| `fetch_nbg_rates` | store today's NBG USD rate, re-convert GEL carts (exit 1 if NBG is unavailable) |
| `reconcile_payments` | resolve pending card payments with the provider (every 5 minutes) |
| `purge_expired_billing` | anonymize deleted accounts' order billing data after 5 years (monthly) |
| `clearsessions` | Django built-in (daily) |
| `inspect_amt <part_number>` | operator diagnostic: raw AMT rows and the prices they produce |

## Deployment notes (details: `docs/operations.md`)

- gunicorn `config.wsgi:application` behind nginx; nginx proxies `/api/` and `/<ADMIN_URL_PATH>`, serves
  `/static/` (`collectstatic`) and `/media/` (`MEDIA_ROOT`), and sets `X-Forwarded-Proto` and `X-Forwarded-For`
  (`NUM_PROXIES=1`).
- nginx access logs must use the masked log format (public link tokens); no gunicorn access log.
- Back up the database and `MEDIA_ROOT` nightly.

## Languages (ka default, en, ru)

msgids are English; catalogs live in `locale/<lang>/LC_MESSAGES/django.po` (commit `.po`, never `.mo`).

```bash
python manage.py makemessages -l ka -l ru --ignore=venv --no-obsolete --no-wrap
python manage.py compilemessages --ignore=venv
```

The API answers in the `Accept-Language` language (unsupported → Georgian); SMS, email and notifications use
the customer's `preferred_language`. `api/tests/test_i18n.py` fails on any untranslated, fuzzy or Georgian
literal string.

### Adding a language (5 steps)

1. Add `("<code>", "<own name>")` to `LANGUAGES` in `config/settings.py`.
2. `python manage.py makemessages -l <code> --ignore=venv --no-wrap`, translate every entry of
   `locale/<code>/LC_MESSAGES/django.po`, then `compilemessages`.
3. In the admin, fill the new language input of every translated field (pickup points, cities/districts in
   Site settings, tariff benefits).
4. In the admin (Notification channel settings), add the SMS/email template of every event in the new language
   (until then customers get the Georgian template).
5. Run the test suite (catalog completeness) and add the code to the frontend's language list.
