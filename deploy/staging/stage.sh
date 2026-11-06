#!/usr/bin/env bash
# Operate the isolated Lion Parts staging: docs/staging-runbook.md.
#   stage.sh check | deploy <git-ref> | health | backup | restore-verify <dump> | restore <dump> | rollback <git-ref>
# Every command first proves its target. It refuses any database, user or settings other than the isolated staging's.
# It never touches nginx, DNS, TLS, cron, other services or other databases; restarting the service is printed, not done.
set -euo pipefail

STAGE_USER="${STAGE_USER:-lionparts-stage}"
STAGE_HOME="${STAGE_HOME:-/home/lionparts-stage}"
CHECKOUT="${STAGE_CHECKOUT:-$STAGE_HOME/lion-parts}"
ENV_FILE="${STAGE_ENV_FILE:-$CHECKOUT/backend/.env}"
BACKUP_DIR="${STAGE_BACKUP_DIR:-$STAGE_HOME/backups}"
PORT="${STAGE_PORT:-8011}"
EXPECTED_DB=lp_stage_v1
RESTORE_DB=lp_stage_restore_check
KEEP_BACKUPS=7

die() { echo "REFUSED: $*" >&2; exit 2; }
say() { echo "== $*"; }
want() { [[ "${!1:-}" == "$2" ]] || die "$1 must be '$2' in $ENV_FILE"; }
empty() { [[ -z "${!1:-}" ]] || die "$1 must be empty on staging"; }

load_target() {
    [[ "$(id -un)" == "$STAGE_USER" ]] || die "run as $STAGE_USER, not $(id -un)"
    [[ "$(id -u)" != 0 ]] || die "never run as root"
    [[ -f "$ENV_FILE" ]] || die "$ENV_FILE does not exist"
    [[ "$(stat -c %a "$ENV_FILE")" == 600 ]] || die "$ENV_FILE must have mode 600"
    [[ "$(stat -c %U "$ENV_FILE")" == "$STAGE_USER" ]] || die "$ENV_FILE must belong to $STAGE_USER"
    set -a; . "$ENV_FILE"; set +a
    want DEPLOY_ENV staging; want DEBUG False; want PARTS_PROVIDER demo; want PAYMENT_PROVIDER fake
    want SMS_DEMO_MODE True; want SENDER_GE_ENABLED False; want ENABLE_DEMO_ENDPOINTS True
    want DB_NAME "$EXPECTED_DB"
    case "${DB_USER:-}" in lion_parts*|postgres|"") die "DB_USER '${DB_USER:-}' is not the staging role" ;; esac
    want EMAIL_HOST 127.0.0.1
    for name in AMT_API_LOGIN AMT_API_PASSWORD SENDER_GE_API_KEY FLITT_MERCHANT_ID FLITT_SECRET_KEY EMAIL_HOST_PASSWORD; do
        empty "$name"
    done
    [[ -n "${SECRET_KEY:-}" ]] || die "SECRET_KEY is empty"
    case "$(realpath "$CHECKOUT")" in /home/lado*|/home/lionpartsdev/lion-parts) die "not the staging checkout" ;; esac
    cd "$CHECKOUT/backend"
    export PGHOST="$DB_HOST" PGPORT="$DB_PORT" PGUSER="$DB_USER" PGPASSWORD="$DB_PASSWORD"
}

manage() { venv/bin/python manage.py "$@"; }

# What Django will really use, read from the loaded settings rather than from the file.
verify_effective() {
    venv/bin/python - "$EXPECTED_DB" <<'PY'
import os, sys
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
import django
django.setup()
from django.conf import settings as s
from django.db import connection
expected = {"DB": sys.argv[1], "DEPLOY_ENV": "staging", "DEBUG": False, "PARTS_PROVIDER": "demo", "PAYMENT_PROVIDER": "fake",
            "SENDER_GE_ENABLED": False, "SMS_DEMO_MODE": True}
actual = {"DB": s.DATABASES["default"]["NAME"], **{k: getattr(s, k) for k in list(expected)[1:]}}
wrong = {k: v for k, v in actual.items() if v != expected[k]}
if wrong:
    sys.exit(f"REFUSED: effective settings differ: {wrong}")
with connection.cursor() as cursor:
    cursor.execute("SELECT current_database()")
    name = cursor.fetchone()[0]
if name != sys.argv[1]:
    sys.exit(f"REFUSED: connected to {name}")
print("effective target:", name, "on", s.DATABASES["default"]["HOST"], s.DATABASES["default"]["PORT"])
PY
}

cmd_check() {
    load_target
    verify_effective
    say "Django checks"
    manage check
    # W005 and W021 are the HSTS owner decisions (docs/security-review.md).
    manage check --deploy 2>&1 | tee /dev/stderr | grep -E "^\?:.*\((security\.W[0-9]+)\)" | grep -vE "W005|W021" \
        && die "unexpected deployment warning" || true
    manage makemigrations --check --dry-run
    say "target verified"
}

cmd_deploy() {
    local ref="${1:?usage: stage.sh deploy <git-ref>}"
    load_target
    git -C "$CHECKOUT" diff --quiet && git -C "$CHECKOUT" diff --cached --quiet || die "the staging checkout has local changes"
    say "code: $ref"
    git -C "$CHECKOUT" fetch --tags origin
    git -C "$CHECKOUT" checkout --detach "$ref"
    deps_and_assets
    verify_effective
    say "pending migrations"
    manage migrate --plan
    manage migrate --noinput
    manage createcachetable
    cmd_check
    say "done. As the owner: systemctl restart lionparts-stage, then stage.sh health"
}

deps_and_assets() {
    venv/bin/pip install --quiet -r requirements.txt
    manage compilemessages --ignore=venv
    manage collectstatic --noinput
}

cmd_health() {
    load_target
    local url="http://127.0.0.1:$PORT/api/health/" answer
    answer="$(curl -fsS -m 10 -H "Host: ${ALLOWED_HOSTS%%,*}" -H "X-Forwarded-Proto: https" "$url")" \
        || die "no healthy answer from $url"
    echo "$answer"
    [[ "$answer" == *'"ok"'* ]] || die "unexpected health answer"
    psql -d "$EXPECTED_DB" -Atc "SELECT 'connections to $EXPECTED_DB: ' || count(*) FROM pg_stat_activity WHERE datname = '$EXPECTED_DB'"
    psql -d "$EXPECTED_DB" -Atc "SELECT 'cache rows: ' || count(*) FROM cache_table"
    psql -d "$EXPECTED_DB" -Atc "SELECT 'latest rate: ' || coalesce(max(date)::text, 'NONE') FROM pricing_exchangerate"
}

cmd_backup() {
    load_target
    mkdir -p "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"
    local stamp dump; stamp="$(date -u +%Y%m%dT%H%M%SZ)"; dump="$BACKUP_DIR/$EXPECTED_DB-$stamp.dump"
    umask 077
    pg_dump -Fc --no-owner -d "$EXPECTED_DB" -f "$dump"
    [[ -d "${MEDIA_ROOT:-}" ]] && tar -C "$MEDIA_ROOT" -czf "$BACKUP_DIR/media-$stamp.tar.gz" .
    pg_restore --list "$dump" > /dev/null
    ls -1t "$BACKUP_DIR"/"$EXPECTED_DB"-*.dump | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm --
    ls -1t "$BACKUP_DIR"/media-*.tar.gz 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm --
    echo "$dump"
}

table_counts() {
    psql -d "$1" -Atc "SELECT 'orders=' || (SELECT count(*) FROM orders_order) || ' payments=' || (SELECT count(*) FROM payments_payment)
        || ' customers=' || (SELECT count(*) FROM accounts_customer) || ' migrations=' || (SELECT count(*) FROM django_migrations)"
}

# Restores into the separate check database only: the live staging database is not touched.
cmd_restore_verify() {
    local dump="${1:?usage: stage.sh restore-verify <dump>}"
    load_target
    [[ -f "$dump" ]] || die "$dump does not exist"
    pg_restore --clean --if-exists --no-owner --exit-on-error -d "$RESTORE_DB" "$dump"
    local live restored; live="$(table_counts "$EXPECTED_DB")"; restored="$(table_counts "$RESTORE_DB")"
    echo "live:     $live"; echo "restored: $restored"
    DB_NAME="$RESTORE_DB" manage showmigrations --plan | grep -c '\[X\]' | sed 's/^/applied migrations in the restored copy: /'
    DB_NAME="$RESTORE_DB" manage check --database default > /dev/null
    say "restored copy opens; compare the counts with the time of the dump"
}

# Replaces lp_stage_v1 from a dump. Stop the service first. Only with CONFIRM=lp_stage_v1.
cmd_restore() {
    local dump="${1:?usage: CONFIRM=lp_stage_v1 stage.sh restore <dump>}"
    load_target
    [[ "${CONFIRM:-}" == "$EXPECTED_DB" ]] || die "set CONFIRM=$EXPECTED_DB to replace that database"
    [[ -f "$dump" ]] || die "$dump does not exist"
    if curl -fsS -m 3 -o /dev/null "http://127.0.0.1:$PORT/api/health/" -H "X-Forwarded-Proto: https" -H "Host: ${ALLOWED_HOSTS%%,*}"; then
        die "the service still answers on $PORT: stop it first"
    fi
    pg_restore --clean --if-exists --no-owner --exit-on-error -d "$EXPECTED_DB" "$dump"
    table_counts "$EXPECTED_DB"
}

cmd_rollback() {
    local ref="${1:?usage: stage.sh rollback <git-ref>}"
    load_target
    git -C "$CHECKOUT" checkout --detach "$ref"
    deps_and_assets
    verify_effective
    manage migrate --check > /dev/null 2>&1 || echo "WARNING: this code expects different migrations; restore the dump taken before the deploy."
    cmd_check
    say "code is at $ref. As the owner: systemctl restart lionparts-stage, then stage.sh health"
}

case "${1:-}" in
    check) cmd_check ;;
    deploy) shift; cmd_deploy "$@" ;;
    health) cmd_health ;;
    backup) cmd_backup ;;
    restore-verify) shift; cmd_restore_verify "$@" ;;
    restore) shift; cmd_restore "$@" ;;
    rollback) shift; cmd_rollback "$@" ;;
    *) sed -n 2,5p "$0"; exit 2 ;;
esac
