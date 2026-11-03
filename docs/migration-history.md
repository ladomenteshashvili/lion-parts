# Migration history: accounts rewrite (LP-BE-201) and compatibility risks

Audit date 2026-11-03, commit `befe29b` (branch `feature/m1-accounts-auth`). No database was accessed for
this audit; it is based on the migration files in Git. **Do not run `migrate` from this branch against
any database that was migrated before `befe29b`** (in particular `lion_parts_v2`) until the owner decides
how that database is replaced.

## What changed

| App | Before | After |
|---|---|---|
| accounts | `0001_initial` … `0008_alter_phoneverificationcode_purpose` (legacy `session_id` schema: `Customer.name`, `CustomerSession`, …) | a **new** `0001_initial` (G§6.1 schema) + `0002_seed_default_tariff` (data: tariff "Standard", GEL/USD 20 %) |
| orders | `0014_add_order_customer` depended on `accounts.0004_phoneverificationcode`; `0016_order_billing_type_and_more` on `accounts.0008_…` | both depend on `accounts.0001_initial`; no operation changed |
| others | — | unchanged (cart, parts and payments have no accounts migration dependency) |

Allowed by G§6 ("no production customers or orders: replace the old customer/cart/order/payment schema
with fresh migrations") and LP-BE-201 ("drop the old tables on staging, fresh DB").

## Fresh databases (CI, tests, a new staging DB)

`migrate` builds the full schema from zero: accounts `0001` → `0002`, then the orders chain. Verified by
the test runner (every test run creates the database from migrations) on SQLite locally and on
PostgreSQL 16 in CI. `makemigrations --check` is clean.

## Databases migrated before `befe29b` (e.g. `lion_parts_v2`, if it was migrated)

Their `django_migrations` table lists `accounts 0001_initial … 0008`. What Django would do:

1. **`accounts.0001_initial` counts as already applied**, because the name is the same, although its
   content is different. Django does not compare content. The old tables (`accounts_customer` with
   `session_id`/`name`, `accounts_customersession`, the old `accounts_legalentityprofile` columns)
   stay as they are.
2. No `InconsistentMigrationHistory` error is raised: the applied `orders.0014/0016` now depend on
   `accounts.0001_initial`, which is recorded as applied. **The mismatch is silent.**
3. `accounts.0002_seed_default_tariff` counts as unapplied, so `migrate` would run it against the old
   `accounts_customertariff` table, which has no `payment_terms` column and no `accounts_tariffmarkup` table. It fails.
   On PostgreSQL that migration's transaction rolls back, and the database is left with the old schema.
4. Any request touching accounts then fails at runtime (missing columns/tables): registration, login,
   admin customer pages, order admin pages that join the customer.
5. The stale records `accounts 0002_customer_can_request_quote … 0008` stay in `django_migrations`
   (Django ignores records without files).
6. `orders.0015_backfill_order_customers` (data migration, already applied there) filters the
   historical `Customer` by `session_id`. On a **fresh** DB it has no orders to process, so it never
   reaches that filter. It would only fail on a DB that has orders without a customer and has not
   applied 0015 yet. No such DB is expected.

There is no in-place upgrade path: the old account data model (customers identified by `session_id`,
no `User`) cannot be mapped onto the new one, and G§6 states there is no production customer data.

## Options for an existing database (owner decision, not done by the agent)

| Option | Effect |
|---|---|
| A. Create a **new** empty database for the new backend and point the new `.env` at it | No data touched; recommended |
| B. Drop and recreate the existing development DB | Needs explicit owner approval (destructive) |
| C. Keep the old DB for the legacy app only | Never run this branch's `migrate` against it |

Detection before any `migrate`: `python manage.py showmigrations accounts` on a pre-`befe29b` database
shows `[X] 0001_initial` and `[ ] 0002_seed_default_tariff`. That combination means "old schema; do not
migrate".

The orders, cart and payments apps get the same treatment (fresh migrations) in LP-BE-501/601/701.
The same rule applies then: fresh databases only.
